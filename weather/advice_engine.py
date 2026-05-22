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

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import NamedTuple, Optional

WEATHER_UMBRELLA_THRESHOLD = int(os.environ.get("WEATHER_UMBRELLA_THRESHOLD", "30"))


class ScoredBestTimeWindow(NamedTuple):
    """A single scored hour from the best-time optimization."""
    time: datetime
    precip_probability: int
    wind_speed: Optional[float] = None
    feels_like: Optional[float] = None
    rank: int = 0
    explanation: str = ""
    composite_score: float = 0.0


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


    # Build composite score (lower is better)
    def _score(w: ScoredBestTimeWindow) -> float:
        precip = w.precip_probability
        wind = (w.wind_speed or 0) / 10.0
        temp = abs((w.feels_like or 15) - 18) / 5.0
        return precip * 3.0 + wind * 1.0 + temp * 0.5

    scored_with_score = [
        ScoredBestTimeWindow(
            time=w.time,
            precip_probability=w.precip_probability,
            wind_speed=w.wind_speed,
            feels_like=w.feels_like,
            rank=0,
            explanation=_explain_window(w),
            composite_score=_score(w),
        )
        for w in scored
    ]

    scored_with_score.sort(key=lambda w: w.composite_score)
    ranked = [
        ScoredBestTimeWindow(
            time=w.time,
            precip_probability=w.precip_probability,
            wind_speed=w.wind_speed,
            feels_like=w.feels_like,
            rank=idx + 1,
            explanation=w.explanation,
            composite_score=w.composite_score,
        )
        for idx, w in enumerate(scored_with_score)
    ]
    return ranked


def _explain_window(w: ScoredBestTimeWindow) -> str:
    """Generate a human-readable explanation for why this window was scored."""
    parts: list[str] = []
    if w.precip_probability <= 10:
        parts.append("very low rain chance")
    elif w.precip_probability <= 30:
        parts.append("low rain chance")
    elif w.precip_probability <= 50:
        parts.append("moderate rain chance")
    else:
        parts.append("high rain chance")

    if w.wind_speed is not None:
        if w.wind_speed > 40:
            parts.append("strong winds")
        elif w.wind_speed > 25:
            parts.append("breezy")
        else:
            parts.append("calm winds")

    if w.feels_like is not None:
        if 15 <= w.feels_like <= 22:
            parts.append("comfortable temperature")
        elif w.feels_like < 10:
            parts.append("cold")
        elif w.feels_like > 25:
            parts.append("warm")

    return ", ".join(parts)


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

    # Check today's precip probability (general/day-level advice)
    if weather.today and weather.today.precip_probability > WEATHER_UMBRELLA_THRESHOLD:
        adv.umbrella = True
        adv.umbrella_reasons.append(
            f"rain expected ({weather.today.precip_probability}% chance)"
        )

    # Check hourly precipitation probabilities (general/day-level advice)
    for hp in weather.hourly_precipitation:
        if hp.probability > WEATHER_UMBRELLA_THRESHOLD:
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
    window=None,
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
    from .evaluation_context import WeatherWindow, precipitation_in_window

    reasons = []
    needs_umbrella = False

    if window is None:
        # Backward-compatible fallback for direct callers: build a generic
        # window for today. Skill entry points pass a fully resolved context
        # window so tomorrow/evening commute decisions use the requested date.
        now = datetime.now(timezone.utc)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            start_h, start_m = map(int, window_start.split(":"))
            end_h, end_m = map(int, window_end.split(":"))
            start_dt = base.replace(hour=start_h, minute=start_m)
            end_dt = base.replace(hour=end_h, minute=end_m)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
        except ValueError:
            return False, []

        window = WeatherWindow(
            label=f"commute ({window_start}–{window_end})",
            start=start_dt,
            end=end_dt,
            window_type="commute",
        )

    # Check home — window-scoped only
    home_risky, home_reasons = precipitation_in_window(home_weather.hourly_precipitation, window, threshold)
    if home_risky:
        needs_umbrella = True
        reasons.extend(f"Home: {r}" for r in home_reasons)

    # Check office — window-scoped only
    office_risky, office_reasons = precipitation_in_window(office_weather.hourly_precipitation, window, threshold)
    if office_risky:
        needs_umbrella = True
        reasons.extend(f"Office: {r}" for r in office_reasons)

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