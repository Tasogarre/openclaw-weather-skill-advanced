"""
weather_engine.py — Core orchestration for Weather Skill v1

Takes a location query (alias or free text), resolves it via the registry
and geocoding chain, fetches the forecast, and returns a structured
WeatherData object ready for advice or formatting.

Preserves existing morning briefing weather path — this module does NOT
replace bin/morning_briefing.py; it is the foundation for the Phase 3
integration once the new engine is verified.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .geocoding import geocode_with_fallback, GeoResult, GeocodingError
from .weather_fetcher import fetch_forecast, WeatherFetchError
from .weather_models import WeatherData

logger = logging.getLogger(__name__)

# Registry path — designed to also support memory/ override later
DEFAULT_REGISTRY_PATH = Path(__file__).parent / "location_registry.json"

# Commute window defaults (also in registry JSON, kept here for programmatic access)
COMMUTE_MORNING = ("08:30", "11:30")
COMMUTE_EVENING = ("16:30", "19:30")


class LocationNotFoundError(Exception):
    pass


class WeatherEngineError(Exception):
    pass


def _load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_location(location_input: str) -> tuple[dict, GeoResult]:
    """
    Resolve a location input (alias or free text) to a (registry_entry, GeoResult) tuple.

    Resolution chain:
      1. Check if input matches a known registry alias -> use primary + fallback queries
      2. Otherwise treat as free-text -> geocode directly
      3. Geocoding primary fails -> try fallback query (registry entries only)
      4. All fail -> raise LocationNotFoundError

    Returns (registry_entry_or_None, geo_result)
    """
    registry = _load_registry()
    locations = registry.get("locations", {})

    input_lower = location_input.lower().strip()

    # Bare or empty query -> default to home
    if input_lower in ("", "home", "."):
        for alias, entry in locations.items():
            if entry.get("is_default"):
                try:
                    geo = geocode_with_fallback(entry["primary_query"], entry.get("fallback_query", ""))
                    return entry, geo
                except GeocodingError as e:
                    raise LocationNotFoundError(f"Default location not resolvable: {e}") from e
        raise LocationNotFoundError("No default location configured")

    # Step 1: check registry aliases
    for alias, entry in locations.items():
        if input_lower in [alias] + entry.get("aliases", []):
            primary_q = entry["primary_query"]
            fallback_q = entry.get("fallback_query", "")
            try:
                geo = geocode_with_fallback(primary_q, fallback_q)
                return entry, geo
            except GeocodingError as e:
                logger.warning(f"Registry alias '{alias}' geocode failed: {e}")
                raise LocationNotFoundError(f"Could not resolve location '{location_input}'") from e

    # Step 2: free-text geocoding (no registry match)
    try:
        from .geocoding import geocode
        results = geocode(location_input, max_results=1)
        if not results:
            raise GeocodingError("No geocoding results")
        return None, results[0]
    except GeocodingError as e:
        raise LocationNotFoundError(
            f"Could not resolve location: '{location_input}' — {e}"
        ) from e


def get_default_location() -> tuple[dict, GeoResult]:
    """Resolve the default location (home) from the registry."""
    registry = _load_registry()
    locations = registry.get("locations", {})
    for alias, entry in locations.items():
        if entry.get("is_default"):
            geo = geocode_with_fallback(entry["primary_query"], entry.get("fallback_query", ""))
            return entry, geo
    # Fallback: hard-code London
    from .geocoding import geocode
    results = geocode("London", max_results=1)
    if not results:
        raise LocationNotFoundError("No default location available")
    return {"canonical": "home", "display_name": "Home"}, results[0]


def get_weather(
    location_input: Optional[str] = None,
    *,
    location_alias: Optional[str] = None,
) -> WeatherData:
    """
    Fetch weather for a location.

    Usage:
        get_weather()                      -> default (home) location
        get_weather("atlanta")             -> by alias or free text
        get_weather(location_alias="office")

    Raises LocationNotFoundError, WeatherEngineError on failure.
    """
    try:
        if location_input:
            _, geo = resolve_location(location_input)
        elif location_alias:
            registry = _load_registry()
            entry = registry.get("locations", {}).get(location_alias)
            if not entry:
                raise WeatherEngineError(f"Unknown location alias: {location_alias}")
            _, geo = resolve_location(location_alias)
        else:
            _, geo = get_default_location()

        return fetch_forecast(geo)

    except LocationNotFoundError:
        raise
    except GeocodingError as e:
        raise WeatherEngineError(f"Location resolution failed: {e}") from e
    except WeatherFetchError as e:
        raise WeatherEngineError(f"Forecast fetch failed: {e}") from e


def get_commute_windows() -> dict:
    """
    Return commute window definitions.

    Env vars take precedence over the registry JSON:
      WEATHER_COMMUTE_MORNING_START  (default 08:30)
      WEATHER_COMMUTE_MORNING_END    (default 11:30)
      WEATHER_COMMUTE_EVENING_START (default 16:30)
      WEATHER_COMMUTE_EVENING_END   (default 19:30)

    Allows operators to override commute windows without touching the JSON file.
    """
    registry = _load_registry()
    defaults = {
        "morning": {"start": "08:30", "end": "11:30"},
        "evening": {"start": "16:30", "end": "19:30"},
    }
    raw = registry.get("commute_windows", defaults)
    return {
        "morning": {
            "start": os.environ.get("WEATHER_COMMUTE_MORNING_START") or raw.get("morning", {}).get("start", "08:30"),
            "end": os.environ.get("WEATHER_COMMUTE_MORNING_END") or raw.get("morning", {}).get("end", "11:30"),
        },
        "evening": {
            "start": os.environ.get("WEATHER_COMMUTE_EVENING_START") or raw.get("evening", {}).get("start", "16:30"),
            "end": os.environ.get("WEATHER_COMMUTE_EVENING_END") or raw.get("evening", {}).get("end", "19:30"),
        },
    }


def rain_in_window(
    weather: WeatherData,
    window_start: str,
    window_end: str,
    probability_threshold: int = 40,
) -> bool:
    """
    Check whether precipitation is expected during a specific time window.

    Args:
        weather: WeatherData with hourly_precipitation populated
        window_start: "HH:MM" local time
        window_end: "HH:MM" local time
        probability_threshold: min probability % to count as "raining"

    Returns True if any hourly block in the window has precip probability >= threshold.
    """
    from datetime import time as dt_time
    from datetime import datetime as dt_datetime

    try:
        start_h, start_m = map(int, window_start.split(":"))
        end_h, end_m = map(int, window_end.split(":"))
    except ValueError:
        return False

    start_t = dt_time(start_h, start_m)
    end_t = dt_time(end_h, end_m)

    for hp in weather.hourly_precipitation:
        t = hp.time.astimezone().time()
        in_window = start_t <= t <= end_t
        if in_window and hp.probability >= probability_threshold:
            return True

    return False
