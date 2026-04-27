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

from dataclasses import dataclass, field
from typing import Optional

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