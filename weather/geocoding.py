"""
geocoding.py — Location geocoding for the weather skill.

Provides a small, dependency-free geocoding layer used by weather_engine.py and
weather_fetcher.py. OpenWeatherMap is preferred when OPENWEATHERMAP_API_KEY is
available, especially for UK postcodes; Open-Meteo remains the zero-config
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

OWM_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
OWM_DIRECT_API = "https://api.openweathermap.org/geo/1.0/direct"
OWM_ZIP_API = "https://api.openweathermap.org/geo/1.0/zip"
OM_GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_API = "https://nominatim.openstreetmap.org/search"


@dataclass
class GeoResult:
    """Normalised geocoding result consumed by the weather fetcher."""

    name: str
    latitude: float
    longitude: float
    country: str = ""
    admin1: str = ""
    timezone: str = ""


class GeocodingError(Exception):
    """Raised when a location cannot be geocoded."""


_UK_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE)


def _is_uk_postcode(query: str) -> bool:
    return bool(_UK_POSTCODE_RE.match((query or "").strip()))


def _request_json(url: str, timeout: int = 12) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _owm_geocode_zip(postcode: str) -> GeoResult | None:
    """Geocode a UK postcode through OpenWeatherMap's zip endpoint."""
    if not OWM_API_KEY:
        return None

    normalised = postcode.strip().upper().replace(" ", "")
    params = {
        "zip": f"{normalised},GB",
        "appid": OWM_API_KEY,
    }
    url = f"{OWM_ZIP_API}?{urllib.parse.urlencode(params)}"
    try:
        raw = _request_json(url)
        return GeoResult(
            name=str(raw.get("name") or postcode.upper()),
            latitude=float(raw["lat"]),
            longitude=float(raw["lon"]),
            country=str(raw.get("country") or "GB"),
            admin1="",
            timezone=str(raw.get("timezone") or ""),
        )
    except (KeyError, ValueError, urllib.error.HTTPError) as e:
        logger.debug("OWM zip geocode failed for %r: %s", postcode, e)
        return None
    except Exception as e:
        logger.debug("OWM zip geocode request failed for %r: %s", postcode, e)
        return None


def _owm_geocode_direct(query: str, max_results: int = 5) -> list[GeoResult]:
    """Geocode free text through OpenWeatherMap's direct endpoint."""
    if not OWM_API_KEY:
        return []

    params = {
        "q": query,
        "limit": max_results,
        "appid": OWM_API_KEY,
    }
    url = f"{OWM_DIRECT_API}?{urllib.parse.urlencode(params)}"
    try:
        raw = _request_json(url)
        results: list[GeoResult] = []
        if not isinstance(raw, list):
            return []
        for item in raw:
            try:
                local_names = item.get("local_names") or {}
                name = local_names.get("en") or item.get("name") or query
                results.append(
                    GeoResult(
                        name=str(name),
                        latitude=float(item["lat"]),
                        longitude=float(item["lon"]),
                        country=str(item.get("country") or ""),
                        admin1=str(item.get("state") or ""),
                        timezone=str(item.get("timezone") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results
    except urllib.error.HTTPError as e:
        logger.debug("OWM direct geocode HTTP error for %r: %s", query, e)
        return []
    except Exception as e:
        logger.debug("OWM direct geocode failed for %r: %s", query, e)
        return []


def _om_geocode(query: str, max_results: int = 5) -> list[GeoResult]:
    """Geocode free text through Open-Meteo's zero-config geocoding API."""
    params = {
        "name": query,
        "count": max_results,
        "language": "en",
        "format": "json",
    }
    url = f"{OM_GEOCODING_API}?{urllib.parse.urlencode(params)}"
    try:
        raw = _request_json(url)
        rows = raw.get("results") if isinstance(raw, dict) else None
        if not rows:
            return []
        results: list[GeoResult] = []
        for item in rows:
            try:
                results.append(
                    GeoResult(
                        name=str(item.get("name") or query),
                        latitude=float(item["latitude"]),
                        longitude=float(item["longitude"]),
                        country=str(item.get("country_code") or item.get("country") or ""),
                        admin1=str(item.get("admin1") or ""),
                        timezone=str(item.get("timezone") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results
    except urllib.error.HTTPError as e:
        logger.debug("Open-Meteo geocode HTTP error for %r: %s", query, e)
        return []
    except Exception as e:
        logger.debug("Open-Meteo geocode failed for %r: %s", query, e)
        return []


def _nominatim_geocode(query: str, max_results: int = 1) -> list[GeoResult]:
    """Best-effort OpenStreetMap/Nominatim fallback for street-level free text."""
    q = (query or "").strip()
    if not q:
        return []
    params = {
        "q": q,
        "format": "jsonv2",
        "limit": max_results,
        "addressdetails": 1,
        "countrycodes": "gb" if "london" in q.lower() or re.search(r"\b[A-Z]{1,2}\d", q, re.IGNORECASE) else "",
    }
    params = {k: v for k, v in params.items() if v not in ("", None)}
    url = f"{NOMINATIM_API}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.3 OpenClaw"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        if not isinstance(raw, list):
            return []
        results: list[GeoResult] = []
        for item in raw:
            try:
                address = item.get("address") if isinstance(item.get("address"), dict) else {}
                country_code = address.get("country_code") or ""
                country = str(country_code).upper() or str(address.get("country") or "")
                results.append(
                    GeoResult(
                        name=str(item.get("display_name") or item.get("name") or q),
                        latitude=float(item["lat"]),
                        longitude=float(item["lon"]),
                        country=country,
                        admin1=str(address.get("state") or ""),
                        timezone="Europe/London" if country == "GB" else "",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results
    except Exception as e:
        logger.debug("Nominatim geocode failed for %r: %s", query, e)
        return []


def _web_search_geocode(query: str) -> list[GeoResult]:
    """
    Final best-effort fallback for free-text locations that provider geocoders miss.

    This deliberately returns an empty list on failure. The normal publication path
    must not depend on web-search scraping; it is only a last resort for unusual
    operator-entered locations.
    """
    try:
        search_q = f"{query} address London"
        encoded_q = urllib.parse.quote(search_q)
        url = f"https://duckduckgo.com/html/?q={encoded_q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        html = urllib.parse.unquote(html)

        postcode_pattern = r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b"
        match = re.search(postcode_pattern, html)
        if match:
            postcode = match.group(0).replace(" ", "")
            results = _nominatim_geocode(postcode, max_results=1)
            if results:
                logger.debug("web search resolved %r -> postcode %s -> %s", query, postcode, results[0])
                return results
            result = _owm_geocode_zip(postcode)
            if result:
                logger.debug("web search resolved %r -> postcode %s -> %s", query, postcode, result)
                return [result]

        address_patterns = [
            r"\b\d+[A-Z]?\s+Lower\s+Marsh\b",
            r"\b\d+[A-Z]?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b",
        ]
        for pattern in address_patterns:
            for match in re.finditer(pattern, html):
                candidate = match.group(0)
                if any(skip in candidate.lower() for skip in ("mozilla", "duckduckgo", "html")):
                    continue
                results = _nominatim_geocode(f"{candidate}, London", max_results=1)
                if results:
                    logger.debug("web search resolved %r -> address %s -> %s", query, candidate, results[0])
                    return results

        lat_match = re.search(r'data-lat="([^"]+)"', html)
        lon_match = re.search(r'data-lon="([^"]+)"', html)
        if lat_match and lon_match:
            return [
                GeoResult(
                    name=query.title(),
                    latitude=float(lat_match.group(1)),
                    longitude=float(lon_match.group(1)),
                    country="GB",
                    admin1="England",
                    timezone="Europe/London",
                )
            ]
    except Exception as e:
        logger.debug("web search geocode failed for %r: %s", query, e)
    return []


def geocode(query: str, max_results: int = 5) -> list[GeoResult]:
    """
    Geocode a location query.

    Order:
    1. UK postcode via OpenWeatherMap zip endpoint when available.
    2. OpenWeatherMap direct endpoint when available.
    3. Open-Meteo zero-config endpoint.
    4. Best-effort web-search fallback.
    """
    q = (query or "").strip()
    if not q:
        return []

    if _is_uk_postcode(q):
        result = _owm_geocode_zip(q)
        if result:
            return [result]

    results = _owm_geocode_direct(q, max_results=max_results)
    if results:
        return results[:max_results]

    results = _om_geocode(q, max_results=max_results)
    if results:
        return results[:max_results]

    london_q = q if re.search(r"\blondon\b", q, re.IGNORECASE) else f"{q} London"
    results = _nominatim_geocode(london_q, max_results=max_results)
    if results:
        return results[:max_results]

    results = _web_search_geocode(q)
    return results[:max_results]


def geocode_with_fallback(primary_query: str, fallback_query: str = "") -> GeoResult:
    """
    Resolve a primary query, then a fallback query.

    Intended for registry entries where the primary query is precise (for example
    a UK postcode) and the fallback query is a human-readable place name that the
    free Open-Meteo geocoder can resolve.
    """
    attempted: list[str] = []
    for q in (primary_query, fallback_query):
        q = (q or "").strip()
        if not q or q in attempted:
            continue
        attempted.append(q)
        results = geocode(q, max_results=1)
        if results:
            return results[0]

    detail = ", ".join(repr(q) for q in attempted) or "<empty query>"
    raise GeocodingError(f"No geocoding results for {detail}")
