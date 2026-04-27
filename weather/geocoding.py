"""
geocoding.py — Dual-provider geocoding (OpenWeatherMap + Open-Meteo)

OpenWeatherMap geocoding API docs:
  https://openweathermap.org/api/geocoding-api#name

Open-Meteo geocoding API docs:
  https://open-meteo.com/en/docs/geocoding-api

UK postcode support:
  - OpenWeatherMap: /geo/1.0/zip?zip={postcode},{country_code}  (reliable for UK postcodes)
  - Open-Meteo: name search (unreliable for UK postcodes like SW1A 1AA, SE1 9SG)
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import re
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

OWM_GEO_API = "https://api.openweathermap.org/geo/1.0"
OM_GEO_API = "https://geocoding-api.open-meteo.com/v1/search"

# Environment variable for optional OpenWeatherMap API key
OWM_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")


@dataclass
class GeoResult:
    name: str
    latitude: float
    longitude: float
    country: str
    admin1: str = ""   # state / region
    timezone: str = ""
    elevation: float = 0.0


class GeocodingError(Exception):
    pass


def _is_uk_postcode(query: str) -> bool:
    """Detect UK postcode pattern (e.g. SW1A 1AA, SE1 9SG, SW1A 1AA)."""
    cleaned = query.strip().replace(" ", "").upper()
    # UK postcodes: 2-4 letters + 2-3 digits, optional space
    return bool(re.match(r'^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$', query.strip()))


# ─────────────────────────────────────────────────────────────────────────────
# OpenWeatherMap geocoding
# ─────────────────────────────────────────────────────────────────────────────

def _om_geocode(query: str, max_results: int = 3) -> list[GeoResult]:
    """
    Resolve via Open-Meteo Geocoding API.
    Returns list (possibly empty). Raises GeocodingError on network error.
    """
    if not query or not query.strip():
        raise GeocodingError("Empty query")

    clean_query = _om_clean_query(query)

    params = {
        "name": clean_query,
        "count": max_results,
        "language": "en",
        "format": "json",
    }
    url = f"{OM_GEO_API}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise GeocodingError(f"Open-Meteo geocoding HTTP {e.code}: {e.reason}") from e
    except Exception as e:
        raise GeocodingError(f"Open-Meteo geocoding request failed: {e}") from e

    results = data.get("results", [])
    if not results:
        return []

    candidates = []
    for r in results:
        candidates.append(GeoResult(
            name=r.get("name", ""),
            latitude=r["latitude"],
            longitude=r["longitude"],
            country=r.get("country", ""),
            admin1=r.get("admin1", ""),
            timezone=r.get("timezone", ""),
            elevation=r.get("elevation", 0.0),
        ))
    return candidates


def _om_clean_query(query: str) -> str:
    """
    Clean a location query string for Open-Meteo geocoding.

    Open-Meteo's geocoding API has quirks that require careful query cleaning:

    1. UK postcodes "SW1A 1AA" / "SE1 9SG": alphanumeric + space, must be sent as-is.
       Adding anything extra causes NO RESULTS.

    2. "City, Country" (no digits, has comma): open-meteo returns empty for Brazil
       cities when ", Brazil" is appended. Strip the country suffix, query bare city.

    3. "City, State" (no digits, has comma): open-meteo returns empty for Brazil
       multi-word cities without comma. Strip the comma, query as "City State".

    4. "City Country" / "City, Country" with country suffix stripped:
       → strip comma if present → bare city name

    5. US cities "City, State" with digits (postcode-like):
       → keep comma to avoid postcode parsing confusion

    Strategy:
    - Strip trailing country suffix (", UK" / ", Brazil" / ", USA" → bare city)
    - If query has digits: keep structure intact (UK postcodes, US ZIP codes)
    - If query is letters+commas+spaces only: strip commas → "City State/Country" pattern
    - Bare city names always work (API returns correct country first by population)
    """
    # Strip trailing country suffix (", UK" / ", Brazil" / ", USA" etc.)
    # Pattern: optional comma-space, country name, optional state abbrev, end of string
    cleaned = re.sub(r',?\s+[A-Z][a-z]+(\s+[A-Z]{2})?$', '', query).strip()

    # UK postcodes: purely alphanumeric + spaces (no letters-only comma restriction)
    # These must be sent as-is
    if re.match(r'^[a-zA-Z0-9\s]+$', cleaned):
        # Alphanumeric + spaces: send as-is, strip only multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    # No digits: city/state/country pattern → strip commas
    # "City, State" or "City, Country" → "City State/Country" without comma
    if re.match(r'^[a-zA-Z\s,]+$', cleaned):
        cleaned = cleaned.replace(',', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    # Has digits (US postcode "City, State ZIP" or similar): keep commas
    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# OpenWeatherMap geocoding (requires API key — primary for UK postcodes)
# ─────────────────────────────────────────────────────────────────────────────

def _owm_geocode_zip(postcode: str, country_code: str = "GB") -> Optional[GeoResult]:
    """
    Resolve a UK postcode via OpenWeatherMap /geo/1.0/zip endpoint.

    Only available when OWM_API_KEY is set.
    Returns None if key is missing or request fails.
    """
    if not OWM_API_KEY:
        return None

    url = f"{OWM_GEO_API}/zip?zip={urllib.parse.quote(postcode)},{country_code}&appid={OWM_API_KEY}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"OWM zip geocode failed for '{postcode}': {e}")
        return None

    # OWM zip endpoint returns lat/lon directly
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        return None

    return GeoResult(
        name=postcode,
        latitude=float(lat),
        longitude=float(lon),
        country=country_code,
        admin1=data.get("state", ""),
        timezone=data.get("timezone", ""),
        elevation=float(data.get("elevation", 0)),
    )


def _owm_geocode_name(query: str, max_results: int = 3) -> list[GeoResult]:
    """
    Resolve a location name via OpenWeatherMap /geo/1.0/direct endpoint.

    Only available when OWM_API_KEY is set.
    Returns empty list on failure or when key is missing.
    """
    if not OWM_API_KEY:
        return []

    params = {
        "q": query,
        "limit": max_results,
        "appid": OWM_API_KEY,
    }
    url = f"{OWM_GEO_API}/direct?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"OWM name geocode failed for '{query}': {e}")
        return []

    if not isinstance(data, list):
        return []

    candidates = []
    for r in data:
        candidates.append(GeoResult(
            name=r.get("name", ""),
            latitude=r["lat"],
            longitude=r["lon"],
            country=r.get("country", ""),
            admin1=r.get("state", ""),
            timezone=r.get("timezone", ""),
            elevation=float(r.get("elevation", 0)),
        ))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Public API — unified geocoding with OWM primary when key is available
# ─────────────────────────────────────────────────────────────────────────────

def geocode(query: str, max_results: int = 3) -> list[GeoResult]:
    """
    Resolve a free-text location query to lat/lon.

    Resolution chain:
      1. UK postcode detected + OWM API key available → OWM /geo/1.0/zip
      2. OWM API key available → OWM /geo/1.0/direct (primary)
      3. Open-Meteo (always available, fallback)

    Returns list of candidates (possibly empty).
    Raises GeocodingError on network errors (caller handles empty results gracefully).
    """
    if not query or not query.strip():
        raise GeocodingError("Empty query")

    # ── UK postcode shortcut via OWM zip endpoint ─────────────────────────────
    if _is_uk_postcode(query) and OWM_API_KEY:
        result = _owm_geocode_zip(query.strip())
        if result:
            return [result]
        # Fall through to other providers

    # ── OWM direct geocoding (primary when key available) ────────────────────
    if OWM_API_KEY:
        owm_results = _owm_geocode_name(query, max_results=max_results)
        if owm_results:
            return owm_results

    # ── Open-Meteo fallback (always available) ────────────────────────────────
    try:
        results = _om_geocode(query, max_results=max_results)
        if results:
            return results
    except GeocodingError:
        pass  # fall through

    # No results from any provider
    return []


def geocode_with_fallback(primary_query: str, fallback_query: str) -> GeoResult:
    """
    Attempt geocoding with a two-step fallback chain.

    1. Try primary_query (cleaned internally)
    2. If no results, try fallback_query
    3. If still no results, raise GeocodingError

    Returns the first available result.
    """
    for q in [primary_query, fallback_query]:
        if not q:
            continue

        # UK postcode + OWM key shortcut
        if _is_uk_postcode(q) and OWM_API_KEY:
            result = _owm_geocode_zip(q.strip())
            if result:
                logger.debug(f"OWM zip geocoded '{q}' -> {result}")
                return result

        try:
            results = geocode(q, max_results=1)
            if results:
                logger.debug(f"Geocoded '{q}' -> {results[0]}")
                return results[0]
        except GeocodingError as e:
            logger.debug(f"Geocode attempt for '{q}' failed: {e}")
            continue

    raise GeocodingError(
        f"Could not resolve location: primary='{primary_query}', fallback='{fallback_query}'"
    )