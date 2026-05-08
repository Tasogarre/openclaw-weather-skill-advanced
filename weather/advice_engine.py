"""
advice_engine.py — Phase 2 Weather Advice Engine v1

Generates actionable advice flags from structured WeatherData.
Dual-location commute logic: fetches weather for both home and office
and checks rain probability at both locations during commute windows.

Advice rules:
  umbrella     rain probability >30% OR current precipitation >0mm
  sunglasses   UV index >=6 AND condition NOT overcast/rainy
  light_jacket feels-like 10-15°C
  warm_jacket  feels-like <10°C
  wind_caution wind speed >40 km/h OR gusts >60 km/h
  heat_caution feels-like >30°C
  cold_caution feels-like <0°C
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple, Optional


class ScoredBestTimeWindow(NamedTuple):
    """A single scored hour from the best-time optimization."""
    time: datetime
    precip_probability: int
    wind_speed: Optional[float] = None
    feels_like: Optional[float] = None


def score_best_time_windows(
    weather: WeatherData,
    candidate_start_hour: int,
    candidate_end_hour: int,
    now: Optional[datetime] = None,
    precip_threshold: int = 30,
) -> list[ScoredBestTimeWindow]:
    """
    Score candidate one-hour windows for best-time outing.


    Candidate windows are built from hourly_precipitation rows that fall
    within [candidate_start_hour, candidate_end_hour). Windows are scored
    by lowest precipitation probability first, then lower wind risk, then
    comfortable temperature. Past windows (before `now`) are excluded.


    Args:
        weather: WeatherData with hourly_precipitation populated
        candidate_start_hour: first hour to consider (0-23)
        candidate_end_hour: last hour (exclusive upper bound, 0-23)
        now: current time for today clamping (UTC datetime)
        precip_threshold: do not recommend windows with precip > this


    Returns:
        Sorted list of ScoredBestTimeWindow, best first.
        Empty list if no valid windows or hourly data unavailable.
    """
    if not weather.hourly_precipitation:
        return []

    scored: list[ScoredBestTimeWindow] = []
    # Normalise all datetimes to naive for consistent comparison
    # (hourly data may be UTC-aware or naive; now may be UTC-aware or naive)
    if now is not None and now.tzinfo is not None:
        now_ts = now.replace(tzinfo=None)
    else:
        now_ts = now

    # Build index of hourly_forecast for wind/temperature lookup
    hourly_lookup: dict[int, tuple[float, float]] = {}
    for hf in weather.hourly_forecast:
        hourly_lookup[hf.time.hour] = (hf.time, hf.feels_like)

    for hp in weather.hourly_precipitation:
        hour = hp.time.hour
        # Filter to candidate range
        if not (candidate_start_hour <= hour < candidate_end_hour):
            continue
        # Exclude past windows for today (strip tzinfo for comparison consistency)
        hp_ts = hp.time.replace(tzinfo=None) if hp.time.tzinfo else hp.time
        if now_ts is not None and hp_ts <= now_ts:
            continue
        # Skip windows with very high rain probability
        if hp.probability > precip_threshold:
            continue
        wind = None
        feels_like = None
        if hour in hourly_lookup:
            _, feels_like = hourly_lookup[hour]
        scored.append(ScoredBestTimeWindow(
            time=hp.time,
            precip_probability=hp.probability,
            wind_speed=wind,
            feels_like=feels_like,
        ))


    # Sort: lowest precip first, then wind, then temperature comfort
    scored.sort(key=lambda w: (w.precip_probability, (w.wind_speed or 0) / 10.0, abs((w.feels_like or 15) - 18) / 5.0))
    return scored


from .weather_models import WeatherData


@dataclass
class WeatherAdvice:
    """Actionable weather advice flags with optional explanations."""
    umbrella: bool = False
    umbrella_reasons: list[str] = field(default_factory=list)
    sunglasses: bool = False
    sunglasses_reason: str = ""
    light_jacket: bool = False
    warm_jacket: bool = False
    jacket_reason: str = ""
    wind_caution: bool = False
    wind_reason: str = ""
    heat_caution: bool = False
    cold_caution: bool = False
    temperature_reason: str = ""
    alerts_text: list[str] = field(default_factory=list)


def advice_for_location(weather: WeatherData) -> WeatherAdvice:
    """
    Generate WeatherAdvice for a single location.

    Args:
        weather: WeatherData from get_weather() or get_weather_commute()

    Returns:
        WeatherAdvice with all flags populated.
    """
    adv = WeatherAdvice()

    # ── Umbrella ─────────────────────────────────────────────
    # Check current precipitation
    if weather.current and weather.current.precipitation_mm > 0:
        adv.umbrella = True
        adv.umbrella_reasons.append(
            f"currently raining ({weather.current.precipitation_mm}mm)"
        )

    # Check today's precip probability
    if weather.today and weather.today.precip_probability > 30:
        adv.umbrella = True
        adv.umbrella_reasons.append(
            f"rain expected ({weather.today.precip_probability}% chance)"
        )

    # Check hourly precipitation probabilities
    for hp in weather.hourly_precipitation:
        if hp.probability > 30:
            adv.umbrella = True
            time_str = hp.time.strftime("%H:%M")
            adv.umbrella_reasons.append(
                f"rain chance {hp.probability}% at {time_str}"
            )
            break  # one flag is enough

    # ── Sunglasses ──────────────────────────────────────────
    uv = weather.current.uv_index if weather.current else None
    condition = weather.current.condition if weather.current else ""
    condition_lower = condition.lower()

    if uv is not None and uv >= 6:
        overcast_or_rainy = any(
            kw in condition_lower for kw in ("overcast", "rain", "drizzle", "thunder")
        )
        if not overcast_or_rainy:
            adv.sunglasses = True
            adv.sunglasses_reason = f"UV index {uv} — high sun exposure"

    # ── Temperature / Jacket ─────────────────────────────────
    feels_like = weather.current.feels_like if weather.current else None

    if feels_like is not None:
        if feels_like < 10:
            adv.warm_jacket = True
            adv.jacket_reason = f"feels like {feels_like}°C — cold"
        elif feels_like < 15:
            adv.light_jacket = True
            adv.jacket_reason = f"feels like {feels_like}°C — cool"

        if feels_like > 30:
            adv.heat_caution = True
            adv.temperature_reason = f"feels like {feels_like}°C — hot"
        elif feels_like < 0:
            adv.cold_caution = True
            adv.temperature_reason = f"feels like {feels_like}°C — freezing"

    # ── Wind ────────────────────────────────────────────────
    wind_speed = weather.current.wind_speed if weather.current else 0.0
    wind_gust = weather.current.wind_gust if weather.current else None

    if wind_speed > 40:
        adv.wind_caution = True
        adv.wind_reason = f"wind {wind_speed:.0f} km/h"
    elif wind_gust is not None and wind_gust > 60:
        adv.wind_caution = True
        adv.wind_reason = f"gusts {wind_gust:.0f} km/h"

    # ── Weather Alerts (OpenWeatherMap only) ─────────────────
    for alert in weather.alerts:
        adv.alerts_text.append(f"{alert.event}: {alert.description[:80]}")

    return adv


def commute_umbrella_check(
    home_weather: WeatherData,
    office_weather: WeatherData,
    window_start: str = "08:30",
    window_end: str = "11:30",
    threshold: int = 30,
) -> tuple[bool, list[str]]:
    """
    Check rain risk at both home and office during a commute window.
    Advises umbrella if EITHER location shows rain > threshold%.

    Args:
        home_weather: WeatherData for home location
        office_weather: WeatherData for office location
        window_start: HH:MM start of window (default 08:30 morning)
        window_end: HH:MM end of window (default 11:30 morning)
        threshold: rain probability % threshold (default 30)

    Returns:
        (needs_umbrella: bool, reasons: list[str])
    """
    from .weather_engine import rain_in_window

    reasons = []
    needs_umbrella = False

    # Check home
    home_risky = rain_in_window(home_weather, window_start, window_end, threshold)
    if home_risky:
        needs_umbrella = True
        reasons.append(
            f"Rain expected at Home during morning commute "
            f"({window_start}–{window_end})"
        )

    # Check office
    office_risky = rain_in_window(office_weather, window_start, window_end, threshold)
    if office_risky:
        needs_umbrella = True
        reasons.append(
            f"Rain expected at Office during morning commute "
            f"({window_start}–{window_end})"
        )

    # Also check via hourly_precipitation directly as a fallback check
    if not needs_umbrella:
        # Double-check: aggregate all hourly probabilities across both locations
        for weather in (home_weather, office_weather):
            for hp in weather.hourly_precipitation:
                t = hp.time.time()
                from datetime import time as dt_time
                try:
                    start_h, start_m = map(int, window_start.split(":"))
                    end_h, end_m = map(int, window_end.split(":"))
                    start_t = dt_time(start_h, start_m)
                    end_t = dt_time(end_h, end_m)
                    if start_t <= t <= end_t and hp.probability > threshold:
                        needs_umbrella = True
                        loc_label = weather.location_label
                        reasons.append(
                            f"Rain chance {hp.probability}% at {loc_label} "
                            f"at {t.strftime('%H:%M')}"
                        )
                        break
                except ValueError:
                    pass
            if needs_umbrella:
                break

    return needs_umbrella, reasons


def get_advice_for_location(location_input: str) -> tuple[WeatherData, WeatherAdvice]:
    """
    Convenience function: fetch weather for a location and return weather + advice.

    Args:
        location_input: registry alias or free-text location

    Returns:
        (WeatherData, WeatherAdvice)
    """
    from .weather_engine import get_weather

    weather = get_weather(location_input)
    advice = advice_for_location(weather)
    return weather, advice