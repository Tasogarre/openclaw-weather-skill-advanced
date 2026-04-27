"""
formatters/briefing_formatter.py — Phase 2 Briefing Formatter v1

Compact single-line (or single short paragraph) weather output
for morning briefing. No emoji, text-dense.
"""

from typing import Optional
from datetime import datetime

from ..weather_models import WeatherData
from ..advice_engine import WeatherAdvice


def _get_tonight_11pm_temp(weather: WeatherData) -> Optional[float]:
    """Extract tonight's 11pm temperature from hourly forecast."""
    if not weather.hourly_forecast:
        return None
    
    today = datetime.now().date()
    for hf in weather.hourly_forecast:
        if hf.time.date() == today and hf.time.hour == 23:
            return hf.temperature
    
    return None


def format_briefing(
    weather: WeatherData,
    advice: Optional[WeatherAdvice] = None,
) -> str:
    """
    Format weather data as a single-line compact briefing.

    Output format:
        **Weather:** 9°C, overcast. High 18°C. Rain during morning commute — bring umbrella.

    Args:
        weather: WeatherData from get_weather()
        advice: Optional WeatherAdvice (generated if not provided)

    Returns:
        Single-line (or short paragraph) formatted string suitable for briefing.
    """
    parts = []

    # ── Current conditions ─────────────────────────────────────
    if weather.current:
        curr = weather.current
        temp = curr.temperature
        feels = curr.feels_like
        condition = curr.condition.lower() if curr.condition else "unknown"
        
        # Format: "Current 9°C (feels like 8°C), overcast clouds"
        if abs(feels - temp) >= 2:
            parts.append(f"Current {temp:.0f}°C (feels like {feels:.0f}°C), {condition}")
        else:
            parts.append(f"Current {temp:.0f}°C, {condition}")

    # ── Today's high ───────────────────────────────────────────
    if weather.today:
        parts.append(f"High {weather.today.high:.0f}°C")

    # ── Tonight's low (11pm today from hourly forecast) ───────
    tonight_temp = _get_tonight_11pm_temp(weather)
    if tonight_temp is not None:
        parts.append(f"Low {tonight_temp:.0f}°C tonight")

    # ── Key advice ─────────────────────────────────────────────
    if advice:
        advice_str = _briefing_advice(weather, advice)
        if advice_str:
            parts.append(advice_str)

    # ── Alerts (OWM only) ─────────────────────────────────────
    if weather.alerts:
        alert_desc = "; ".join(a.event for a in weather.alerts[:2])
        parts.append(f"[{alert_desc}]")

    return "**Weather:** " + ". ".join(parts)


def _briefing_advice(weather: WeatherData, advice: WeatherAdvice) -> str:
    """Build the advice portion of a briefing line."""
    bits = []

    # Umbrella with rain timing
    if advice.umbrella:
        rain_timing = _get_rain_timing(weather)
        if rain_timing:
            bits.append(rain_timing)
        elif advice.umbrella_reasons:
            reason = advice.umbrella_reasons[0]
            bits.append(f"Rain likely — bring umbrella")
        else:
            bits.append("Rain possible — bring umbrella")

    # Temperature
    if advice.heat_caution:
        bits.append(f"Hot ({advice.temperature_reason})")
    elif advice.cold_caution:
        bits.append(f"Cold ({advice.temperature_reason})")

    # Wind
    if advice.wind_caution:
        bits.append(f"Wind ({advice.wind_reason})")

    # Jackets
    if advice.warm_jacket:
        bits.append("Warm jacket")
    elif advice.light_jacket:
        bits.append("Light jacket")

    # Sunglasses
    if advice.sunglasses:
        bits.append("Sunglasses helpful")

    return ". ".join(bits)


def _get_rain_timing(weather: WeatherData) -> str:
    """Extract rain timing from hourly precipitation data for briefing."""
    if not weather.hourly_precipitation:
        return ""
    
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    
    # Find first significant rain (>30% probability or >0mm)
    rain_start = None
    rain_end = None
    max_prob = 0
    
    for hp in weather.hourly_precipitation[:24]:  # Next 24 hours
        if hp.probability > 30 or hp.mm > 0:
            if rain_start is None:
                rain_start = hp.time
            rain_end = hp.time
            max_prob = max(max_prob, hp.probability)
    
    if not rain_start:
        return ""
    
    # Format timing
    start_str = rain_start.strftime("%I%p").lstrip("0").lower()
    
    # If rain starts soon (within 2 hours), be more specific
    hours_until = (rain_start - now).total_seconds() / 3600
    
    if hours_until < 0.5:
        return f"Rain now ({max_prob}%) — bring umbrella"
    elif hours_until < 2:
        return f"Rain starting {start_str} ({max_prob}%) — bring umbrella"
    elif rain_end and (rain_end - rain_start).total_seconds() > 3 * 3600:
        # Long rain period
        end_str = rain_end.strftime("%I%p").lstrip("0").lower()
        return f"Rain {start_str}-{end_str} ({max_prob}%) — bring umbrella"
    else:
        return f"Rain starting {start_str} ({max_prob}%) — bring umbrella"