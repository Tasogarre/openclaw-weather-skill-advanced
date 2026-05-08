"""
formatters/chat_formatter.py — Phase 2 Chat Formatter v1

Conversational weather output with emoji, current conditions,
today/tomorrow outlook, and advice.
"""

from typing import Optional

from ..weather_models import WeatherData
from ..advice_engine import WeatherAdvice


def condition_emoji(condition: str, precip_probability: int = 0) -> str:
    """Return a compact weather-condition emoji for forecast labels."""
    text = (condition or "").lower()
    if precip_probability >= 50:
        return "🌧️"
    if any(term in text for term in ("thunder", "storm")):
        return "⛈️"
    if any(term in text for term in ("rain", "drizzle", "shower")):
        return "🌧️"
    if any(term in text for term in ("snow", "sleet", "ice")):
        return "❄️"
    if any(term in text for term in ("overcast", "cloud")):
        return "☁️"
    if any(term in text for term in ("clear", "sun", "few clouds")):
        return "☀️"
    if any(term in text for term in ("mist", "fog", "haze")):
        return "🌫️"
    return "🌤️"


def format_chat(
    weather: WeatherData,
    advice: Optional[WeatherAdvice] = None,
    location_display: Optional[str] = None,
) -> str:
    """
    Format weather data as conversational chat output.

    Output format:
        📍 London (Home)
        🌡️ 9°C, feels like 9°C — Overcast clouds
        🌧️ Rain expected during your morning commute (100% probability)

        Today: High 18°C / Low 8°C
        Tomorrow: High 16°C / Low 10°C

        ☂️ Umbrella: bring one — rain likely during your commute

    Args:
        weather: WeatherData from get_weather()
        advice: Optional WeatherAdvice (generated if not provided)
        location_display: Override display name (default: weather.location_label)

    Returns:
        Formatted string suitable for chat.
    """
    label = location_display or weather.location_label

    # ── Location header ────────────────────────────────────────
    lines = [f"📍 {label}"]

    # ── Current conditions ─────────────────────────────────────
    if weather.current:
        curr = weather.current
        temp = curr.temperature
        feels = curr.feels_like
        condition = curr.condition
        icon = condition_emoji(condition, int(curr.precipitation_mm > 0) * 100)

        if temp == feels:
            condition_line = f"🌡️ {temp:.0f}°C — {icon} {condition}"
        else:
            condition_line = f"🌡️ {temp:.0f}°C, feels like {feels:.0f}°C — {icon} {condition}"

        lines.append(condition_line)

    # ── Precipitation callout ─────────────────────────────────
    if weather.today and weather.today.precip_probability >= 50:
        lines.append(f"🌧️ Rain expected ({weather.today.precip_probability}% chance today)")

    # ── Alerts (OWM only) ─────────────────────────────────────
    if weather.alerts:
        for alert in weather.alerts:
            event = alert.event
            if alert.start:
                try:
                    day = alert.start.strftime("%a")
                    event = f"{day}: {event}"
                except Exception:
                    pass
            lines.append(f"🚨 {event}")

    # ── Today / Tomorrow outlook ───────────────────────────────
    outlook_parts = []
    if weather.today:
        t = weather.today
        icon = condition_emoji(t.condition, t.precip_probability)
        outlook_parts.append(f"Today: {icon} {t.condition} — High {t.high:.0f}°C / Low {t.low:.0f}°C")
    if weather.tomorrow:
        t = weather.tomorrow
        icon = condition_emoji(t.condition, t.precip_probability)
        outlook_parts.append(f"Tomorrow: {icon} {t.condition} — High {t.high:.0f}°C / Low {t.low:.0f}°C")

    if outlook_parts:
        lines.append("\n".join(outlook_parts))

    # ── Advice block ───────────────────────────────────────────
    if advice:
        advice_lines = _format_advice(advice)
        if advice_lines:
            lines.append("")
            lines.extend(advice_lines)

    return "\n".join(lines)


def _format_advice(advice: WeatherAdvice) -> list[str]:
    """Format WeatherAdvice into advisory lines."""
    result = []

    # Umbrella decision: use a practical visual label, not generic decoration.
    if advice.umbrella_reasons:
        result.append(f"☂️ Umbrella: bring one — {advice.umbrella_reasons[0]}")
    elif not advice.alerts_text:
        result.append("☂️ Umbrella: not needed based on current forecast")

    # Sunglasses
    if advice.sunglasses:
        result.append(f"🕶️ Sunglasses: {advice.sunglasses_reason}")

    # Jackets
    if advice.warm_jacket:
        result.append(f"🧥 Jacket needed — {advice.jacket_reason}")
    elif advice.light_jacket:
        result.append(f"🧥 Light jacket recommended — {advice.jacket_reason}")

    # Temperature extremes
    if advice.heat_caution:
        result.append(f"🔥 Heat caution: {advice.temperature_reason}")
    if advice.cold_caution:
        result.append(f"🥶 Cold alert: {advice.temperature_reason}")

    # Wind
    if advice.wind_caution:
        result.append(f"💨 Wind caution — {advice.wind_reason}")

    return result