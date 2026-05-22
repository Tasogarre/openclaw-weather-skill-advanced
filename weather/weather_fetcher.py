"""
weather_fetcher.py — Dual-provider weather fetching (OpenWeatherMap + Open-Meteo)

OpenWeatherMap One Call 3.0 API docs:
  https://openweathermap.org/api/one-call-3#data

Open-Meteo Weather API docs:
  https://open-meteo.com/en/docs/weather-api

Design:
  - OWM_API_KEY present  → OpenWeatherMap One Call 3.0 (primary)
  - No OWM_API_KEY       → Open-Meteo (always works, no key needed)
  - OWM failures         → graceful fallback to Open-Meteo
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import logging
import os
from datetime import datetime, date, timezone as dt_tz
from typing import Optional

from .weather_models import (
    WeatherData, CurrentConditions, DailyForecast, HourlyPrecipitation,
    WeatherAlert, HourlyForecast,
)
from .geocoding import GeoResult
from .weather_settings import get_forecast_days

logger = logging.getLogger(__name__)

OWM_WEATHER_API = "https://api.openweathermap.org/data/3.0/onecall"
OM_FORECAST_API = "https://api.open-meteo.com/v1/forecast"
OWM_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
DEFAULT_FORECAST_DAYS = 14
MAX_FORECAST_DAYS = 14


def _forecast_days() -> int:
    """Configured forecast horizon, clamped to provider-supported 1-14 days."""
    return max(1, min(MAX_FORECAST_DAYS, get_forecast_days()))



class WeatherFetchError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════
# OpenWeatherMap One Call 3.0
# ═══════════════════════════════════════════════════════════════════════════

def _owm_condition_from_id(code: int) -> str:
    """Map OWM weather condition ID to human-readable string."""
    mapping = {
        200: "Thunderstorm with light rain",
        201: "Thunderstorm with rain",
        202: "Thunderstorm with heavy rain",
        210: "Light thunderstorm",
        211: "Thunderstorm",
        212: "Heavy thunderstorm",
        221: "Ragged thunderstorm",
        230: "Thunderstorm with light drizzle",
        231: "Thunderstorm with drizzle",
        232: "Thunderstorm with heavy drizzle",
        300: "Light drizzle",
        301: "Drizzle",
        302: "Heavy drizzle",
        310: "Light drizzle",
        311: "Drizzle",
        312: "Heavy drizzle",
        313: "Shower drizzle",
        314: "Shower drizzle",
        321: "Shower drizzle",
        500: "Light rain",
        501: "Moderate rain",
        502: "Heavy rain",
        503: "Very heavy rain",
        504: "Extreme rain",
        511: "Freezing rain",
        520: "Light shower rain",
        521: "Shower rain",
        522: "Heavy shower rain",
        531: "Ragged shower rain",
        600: "Light snow",
        601: "Snow",
        602: "Heavy snow",
        611: "Sleet",
        612: "Light sleet",
        613: "Sleet",
        615: "Light rain and snow",
        616: "Rain and snow",
        620: "Light snow showers",
        621: "Snow showers",
        622: "Heavy snow showers",
        701: "Mist",
        711: "Smoke",
        721: "Haze",
        731: "Dust/sand whirls",
        741: "Fog",
        751: "Sand",
        761: "Dust",
        762: "Volcanic ash",
        771: "Squall",
        781: "Tornado",
        800: "Clear",
        801: "Few clouds",
        802: "Scattered clouds",
        803: "Broken clouds",
        804: "Overcast clouds",
        900: "Tornado",
        901: "Tropical storm",
        902: "Hurricane",
        903: "Cold",
        904: "Hot",
        905: "Windy",
        906: "Hail",
        951: "Calm",
        952: "Light breeze",
        953: "Breeze",
        954: "Breeze",
        955: "Breeze",
        956: "Strong breeze",
        957: "Strong breeze",
        958: "Gale",
        959: "Severe gale",
        960: "Storm",
        961: "Severe storm",
        962: "Violent storm",
    }
    return mapping.get(code, f"Code {code}")


def _fetch_owm(geo: GeoResult) -> Optional[WeatherData]:
    """
    Fetch weather via OpenWeatherMap One Call 3.0.

    Returns None if API key is missing or request fails (caller falls back).
    """
    if not OWM_API_KEY:
        return None

    params = {
        "lat": geo.latitude,
        "lon": geo.longitude,
        "units": "metric",
        "exclude": "minutely,alerts",   # keep daily/hourly for full forecast interface
        "appid": OWM_API_KEY,
    }
    url = f"{OWM_WEATHER_API}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        logger.debug(f"OWM One Call HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        logger.debug(f"OWM One Call request failed: {e}")
        return None

    wd = WeatherData(
        location_label=raw.get("timezone", geo.name),
        latitude=raw["lat"],
        longitude=raw["lon"],
        timezone=raw.get("timezone", "UTC"),
        source="openweathermap",
    )

    # ── Current ─────────────────────────────────────────────────────────────
    cur = raw.get("current", {})
    if cur:
        code = int(cur.get("weather", [{}])[0].get("id", 0))
        wd.current = CurrentConditions(
            temperature=float(cur.get("temp", 0)),
            feels_like=float(cur.get("feels_like", 0)),
            condition=_owm_condition_from_id(code),
            condition_code=code,
            wind_speed=float(cur.get("wind_speed", 0)),
            wind_gust=float(cur.get("wind_gust", 0)) or None,
            wind_direction=str(cur.get("wind_deg", "")),
            precipitation_mm=float(cur.get("rain", {}).get("1h", 0)),
            humidity=int(cur.get("humidity", 0)) or None,
            cloud_cover=int(cur.get("clouds", 0)) or None,
            uv_index=int(cur.get("uvi", 0)) or None,
            pressure_hPa=float(cur.get("pressure", 0)) or None,
        )

    # ── Daily forecast horizon ───────────────────────────────────────────────
    daily_list = raw.get("daily", [])
    for day_block in daily_list[:_forecast_days()]:
        dt_iso = day_block.get("dt", 0)
        if not dt_iso:
            continue
        day_date = date.fromtimestamp(dt_iso)
        code = int(day_block.get("weather", [{}])[0].get("id", 0))
        df = DailyForecast(
            date=day_date,
            high=float(day_block.get("temp", {}).get("max", 0)),
            low=float(day_block.get("temp", {}).get("min", 0)),
            condition=_owm_condition_from_id(code),
            condition_code=code,
            precip_probability=int(day_block.get("pop", 0) * 100),
            precip_mm=float(day_block.get("rain", 0)),
            wind_speed=float(day_block.get("wind_speed", 0)),
            wind_gust=float(day_block.get("wind_gust", 0)) or None,
            sunrise=datetime.fromtimestamp(day_block["sunrise"]).astimezone(dt_tz.utc).strftime("%H:%M") if day_block.get("sunrise") else None,
            sunset=datetime.fromtimestamp(day_block["sunset"]).astimezone(dt_tz.utc).strftime("%H:%M") if day_block.get("sunset") else None,
        )
        wd.daily_forecast.append(df)
    wd.sync_legacy_daily_fields()

    # ── Hourly precipitation + temperature ──────────────────────────────────
    hourly_list = raw.get("hourly", [])
    now = datetime.now(dt_tz.utc)
    for h in hourly_list:
        dt_iso = h.get("dt", 0)
        if not dt_iso:
            continue
        t = datetime.fromtimestamp(dt_iso, dt_tz.utc)
        # Skip historical hours (more than 30 min old)
        if (now - t).total_seconds() > 30 * 60:
            continue
        wd.hourly_precipitation.append(HourlyPrecipitation(
            time=t,
            probability=int(float(h.get("pop", 0)) * 100),
            mm=float(h.get("rain", {}).get("1h", 0)) if isinstance(h.get("rain"), dict) else float(h.get("rain", 0)),
        ))
        
        # Also populate hourly_forecast for tonight's low calculation
        code = int(h.get("weather", [{}])[0].get("id", 0))
        wd.hourly_forecast.append(HourlyForecast(
            time=t,
            temperature=float(h.get("temp", 0)),
            feels_like=float(h.get("feels_like", 0)),
            condition=_owm_condition_from_id(code),
            precipitation_probability=int(float(h.get("pop", 0)) * 100),
        ))

    # ── Weather alerts (OWM only — separate endpoint) ───────────────────────
    wd.alerts = _fetch_owm_alerts(geo)

    return wd


def _fetch_owm_alerts(geo: GeoResult) -> list[WeatherAlert]:
    """
    Fetch weather alerts from OpenWeatherMap One Call 3.0 'alerts' field.

    Note: One Call 3.0 includes alerts in the main response when 'alerts' is
    not in the exclude list. Here we exclude alerts in the main call for
    bandwidth and fetch them separately to allow independent error handling.

    Returns empty list if no alerts or on error.
    """
    if not OWM_API_KEY:
        return []

    params = {
        "lat": geo.latitude,
        "lon": geo.longitude,
        "units": "metric",
        "exclude": "current,minutely,hourly,daily",
        "appid": OWM_API_KEY,
    }
    url = f"{OWM_WEATHER_API}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"OWM alerts fetch failed: {e}")
        return []

    alerts = []
    for a in raw.get("alerts", []):
        sender = a.get("sender_name", "")
        event = a.get("event", "")
        if not event:
            continue
        alerts.append(WeatherAlert(
            event=event,
            start=datetime.fromtimestamp(a["start"], dt_tz.utc) if a.get("start") else None,
            end=datetime.fromtimestamp(a["end"], dt_tz.utc) if a.get("end") else None,
            description=a.get("description", ""),
            sender_name=sender if sender != event else None,
        ))

    return alerts


# ═══════════════════════════════════════════════════════════════════════════
# Open-Meteo (always available — fallback when no OWM key or OWM fails)
# ═══════════════════════════════════════════════════════════════════════════

def _om_build_forecast_url(lat: float, lon: float, timezone_str: str, forecast_days: Optional[int] = None) -> str:
    """Build the Open-Meteo forecast URL with all required parameters."""
    days = forecast_days or _forecast_days()
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,relative_humidity_2m,cloud_cover,uv_index,visibility,pressure_msl",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,uv_index_max,sunrise,sunset",
        "hourly": "temperature_2m,apparent_temperature,weather_code,precipitation_probability,precipitation",
        "timezone": timezone_str,
        "forecast_days": days,
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }
    return f"{OM_FORECAST_API}?{urllib.parse.urlencode(params)}"


def _om_parse_wmo_code(code: int) -> str:
    """Map WMO weather code to human-readable condition string."""
    mapping = {
        0: "Clear",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return mapping.get(code, f"Code {code}")


def _om_daily_from_arrays(idx: int, dates, max_temps, min_temps, codes,
                           precip_probs, precip_sums, wind_max, gust_max,
                           uv_max, sunrise, sunset) -> Optional[DailyForecast]:
    if idx >= len(dates):
        return None
    code_val = int(codes[idx]) if idx < len(codes) else 0
    return DailyForecast(
        date=date.fromisoformat(dates[idx]),
        high=float(max_temps[idx]) if idx < len(max_temps) else 0,
        low=float(min_temps[idx]) if idx < len(min_temps) else 0,
        condition=_om_parse_wmo_code(code_val),
        condition_code=code_val,
        precip_probability=int(precip_probs[idx]) if idx < len(precip_probs) else 0,
        precip_mm=float(precip_sums[idx]) if idx < len(precip_sums) else 0,
        wind_speed=float(wind_max[idx]) if idx < len(wind_max) else 0,
        wind_gust=float(gust_max[idx]) if (idx < len(gust_max) and gust_max[idx]) else None,
        uv_index_max=int(uv_max[idx]) if (idx < len(uv_max) and uv_max[idx] is not None) else None,
        sunrise=sunrise[idx] if idx < len(sunrise) else None,
        sunset=sunset[idx] if idx < len(sunset) else None,
    )


def _fetch_om(geo: GeoResult) -> WeatherData:
    """
    Fetch weather via Open-Meteo Forecast API.

    Always available — no API key required.
    Raises WeatherFetchError on network/API errors.
    """
    url = _om_build_forecast_url(geo.latitude, geo.longitude, geo.timezone or "UTC")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "weather-skill/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise WeatherFetchError(f"Open-Meteo API HTTP {e.code}: {e.reason}") from e
    except Exception as e:
        raise WeatherFetchError(f"Open-Meteo API request failed: {e}") from e

    current_block = raw.get("current", {})
    daily_block = raw.get("daily", {})
    hourly_block = raw.get("hourly", {})

    wd = WeatherData(
        location_label=geo.name,
        latitude=geo.latitude,
        longitude=geo.longitude,
        timezone=geo.timezone or "UTC",
        source="open-meteo",
    )

    # ── Current conditions ────────────────────────────────────────────────────
    if current_block:
        code = int(current_block.get("weather_code", 0))
        wd.current = CurrentConditions(
            temperature=float(current_block.get("temperature_2m", 0)),
            feels_like=float(current_block.get("apparent_temperature", 0)),
            condition=_om_parse_wmo_code(code),
            condition_code=code,
            wind_speed=float(current_block.get("wind_speed_10m", 0)),
            wind_gust=float(current_block.get("wind_gusts_10m", 0)) or None,
            wind_direction=str(current_block.get("wind_direction_10m", "")),
            precipitation_mm=float(current_block.get("precipitation", 0)),
            humidity=int(current_block.get("relative_humidity_2m", 0)) or None,
            cloud_cover=int(current_block.get("cloud_cover", 0)) or None,
            uv_index=int(current_block.get("uv_index", 0)) or None,
            visibility_km=(float(current_block.get("visibility", 0)) / 1000) or None,
            pressure_hPa=float(current_block.get("pressure_msl", 0)) or None,
        )

    # ── Daily forecast horizon ───────────────────────────────────────────────
    if daily_block:
        dates = daily_block.get("time", [])
        for idx in range(min(len(dates), _forecast_days())):
            df = _om_daily_from_arrays(
                idx,
                dates,
                daily_block.get("temperature_2m_max", []),
                daily_block.get("temperature_2m_min", []),
                daily_block.get("weather_code", []),
                daily_block.get("precipitation_probability_max", []),
                daily_block.get("precipitation_sum", []),
                daily_block.get("wind_speed_10m_max", []),
                daily_block.get("wind_gusts_10m_max", []),
                daily_block.get("uv_index_max", []),
                daily_block.get("sunrise", []),
                daily_block.get("sunset", []),
            )
            if df is not None:
                wd.daily_forecast.append(df)
        wd.sync_legacy_daily_fields()

    # ── Hourly precipitation — future hours only ──────────────────────────────
    if hourly_block:
        times = hourly_block.get("time", [])
        probs = hourly_block.get("precipitation_probability", [])
        precips = hourly_block.get("precipitation", [])
        temps = hourly_block.get("temperature_2m", [])
        feels = hourly_block.get("apparent_temperature", [])
        codes = hourly_block.get("weather_code", [])
        now = datetime.now(dt_tz.utc)

        for i, t_str in enumerate(times):
            t_str_clean = t_str.replace("Z", "+00:00")
            t = datetime.fromisoformat(t_str_clean)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt_tz.utc)

            # Skip historical hours (more than 30 min old)
            age_seconds = (now - t).total_seconds()
            if age_seconds > 30 * 60:
                continue

            probability = int(probs[i]) if i < len(probs) else 0
            wd.hourly_precipitation.append(HourlyPrecipitation(
                time=t,
                probability=probability,
                mm=float(precips[i]) if i < len(precips) else 0,
            ))
            code = int(codes[i]) if i < len(codes) else 0
            wd.hourly_forecast.append(HourlyForecast(
                time=t,
                temperature=float(temps[i]) if i < len(temps) else 0,
                feels_like=float(feels[i]) if i < len(feels) else float(temps[i]) if i < len(temps) else 0,
                condition=_om_parse_wmo_code(code),
                precipitation_probability=probability,
            ))

    return wd


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def fetch_forecast(geo: GeoResult) -> WeatherData:
    """
    Fetch and parse weather for a given geocoded location.

    Provider selection:
      1. OWM_API_KEY present  → OpenWeatherMap One Call 3.0
      2. OWM fails / no key    → Open-Meteo (always available)

    Raises WeatherFetchError if both providers fail.
    """
    # Try OWM first if key is available
    if OWM_API_KEY:
        wd = _fetch_owm(geo)
        if wd is not None:
            return wd
        logger.info("OWM fetch returned None, falling back to Open-Meteo")

    # Fallback: Open-Meteo (always available)
    return _fetch_om(geo)