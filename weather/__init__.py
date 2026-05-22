"""
weather skill — v1.3.0
Multi-location rich weather forecasting with Phase 2 intent classification,
advice, commute-aware chat output, and briefing formatting. Powered by
Open-Meteo (always) or OpenWeatherMap (when key available).
"""

__version__ = "1.3.0"

# Public API surface for Phase 1 + Phase 2
from .weather_models import (
    WeatherData,
    CurrentConditions,
    DailyForecast,
    HourlyPrecipitation,
    HourlyForecast,
    WeatherAlert,
)
from .weather_engine import (
    get_weather,
    get_default_location,
    get_commute_windows,
    resolve_location,
    rain_in_window,
    LocationNotFoundError,
    WeatherEngineError,
)
from .location_registry import list_registry_locations, delete_registry_location
from .geocoding import geocode, geocode_with_fallback, GeoResult, GeocodingError
from .weather_fetcher import fetch_forecast, WeatherFetchError
from .advice_engine import (
    WeatherAdvice,
    advice_for_location,
    commute_umbrella_check,
    get_advice_for_location,
)
from .intent_classifier import classify_intent, WeatherIntent
from .skill import (
    get_weather_chat,
    get_weather_briefing,
    get_weather_briefing_commute,
)

__all__ = [
    # models
    "WeatherData",
    "CurrentConditions",
    "DailyForecast",
    "HourlyPrecipitation",
    "HourlyForecast",
    "WeatherAlert",
    # engine
    "get_weather",
    "get_default_location",
    "get_commute_windows",
    "resolve_location",
    "rain_in_window",
    "LocationNotFoundError",
    "WeatherEngineError",
    # registry management
    "list_registry_locations",
    "delete_registry_location",
    # geocoding
    "geocode",
    "geocode_with_fallback",
    "GeoResult",
    "GeocodingError",
    # fetcher
    "fetch_forecast",
    "WeatherFetchError",
    # Phase 2: advice engine
    "WeatherAdvice",
    "advice_for_location",
    "commute_umbrella_check",
    "get_advice_for_location",
    # Phase 2: intent classifier
    "classify_intent",
    "WeatherIntent",
    # Phase 2: skill entry point
    "get_weather_chat",
    "get_weather_briefing",
    "get_weather_briefing_commute",
]