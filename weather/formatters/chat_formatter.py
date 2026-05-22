"""
formatters/chat_formatter.py — Phase 2 Chat Formatter v1

Conversational weather output with emoji, current conditions,
today/tomorrow outlook, and advice.
"""

import os
from datetime import date, timedelta
from typing import Optional

from ..weather_models import DailyForecast, WeatherData
from ..advice_engine import WeatherAdvice
from ..weather_settings import get_week_start_day

# Visual rain-emoji trigger for forecast labels. Higher bar than umbrella recommendation
# (WEATHER_UMBRELLA_THRESHOLD) because this threshold drives an emoji callout, not
# an action recommendation.
PRECIP_DISPLAY_THRESHOLD = 50


def condition_emoji(condition: str, precip_probability: int = 0) -> str:
    """Return a compact weather-condition emoji for forecast labels."""
    text = (condition or "").lower()
    if precip_probability >= PRECIP_DISPLAY_THRESHOLD:
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


def _daily_forecasts(weather: WeatherData) -> list[DailyForecast]:
    if weather.daily_forecast:
        return list(weather.daily_forecast)
    return [d for d in (weather.today, weather.tomorrow, weather.day_after) if d is not None]


def _day_label(day: DailyForecast, today: Optional[date] = None) -> str:
    today = today or date.today()
    if day.date == today:
        return "Today"
    if day.date == today + timedelta(days=1):
        return "Tomorrow"
    return day.date.strftime("%a %-d %b")


def _format_daily_line(day: DailyForecast, today: Optional[date] = None) -> str:
    icon = condition_emoji(day.condition, day.precip_probability)
    return (
        f"{_day_label(day, today)}: {icon} {day.condition} — "
        f"High {day.high:.0f}°C / Low {day.low:.0f}°C, rain {day.precip_probability}%"
    )


def _rest_of_week_days(days: list[DailyForecast], week_start_day: Optional[int] = None) -> list[DailyForecast]:
    if not days:
        return []
    week_start = get_week_start_day() if week_start_day is None else week_start_day
    first = days[0].date
    days_until_next_week_start = (week_start - first.weekday()) % 7
    if days_until_next_week_start == 0:
        days_until_next_week_start = 7
    end = first + timedelta(days=days_until_next_week_start)
    return [d for d in days if first <= d.date < end]


def format_forecast_range(
    weather: WeatherData,
    *,
    mode: str = "week",
    location_display: Optional[str] = None,
    week_start_day: Optional[int] = None,
) -> str:
    """Format rest-of-week, next-7-days, next-10-days, or next-14-days daily forecast output."""
    label = location_display or weather.location_label
    days = _daily_forecasts(weather)
    if mode == "rest_of_week":
        selected = _rest_of_week_days(days, week_start_day=week_start_day)
        title = "Rest of week"
    elif mode == "next_10_days":
        selected = days[:10]
        title = "Next 10 days"
    elif mode == "next_14_days":
        selected = days[:14]
        title = "Next 14 days"
    else:
        selected = days[:7]
        title = "Next 7 days"

    lines = [f"📍 {label}", f"📅 **{title} forecast**"]
    if not selected:
        lines.append("No daily forecast data available.")
        return "\n".join(lines)

    today = days[0].date
    lines.extend(_format_daily_line(day, today) for day in selected)

    wettest = max(selected, key=lambda d: (d.precip_probability, d.precip_mm))
    warmest = max(selected, key=lambda d: d.high)
    lines.append("")
    lines.append(f"🌧️ Wettest: {_day_label(wettest, today)} ({wettest.precip_probability}% rain chance)")
    lines.append(f"🌡️ Warmest: {_day_label(warmest, today)} ({warmest.high:.0f}°C high)")
    # Only show provider-note when we asked for more than we got back
    _expected = 14 if mode == "next_14_days" else 10 if mode == "next_10_days" else 7 if mode == "next_7_days" else len(selected)
    if len(selected) < _expected:
        lines.append(f"ℹ️ Provider returned {len(selected)} daily forecasts.")
    return "\n".join(lines)


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
        💧 Rain expected during your morning commute (100% probability)

        Today: High 18°C / Low 8°C
        Tomorrow: High 16°C / Low 10°C

        💡 Bring an umbrella — rain likely during your commute

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
    if weather.today and weather.today.precip_probability >= PRECIP_DISPLAY_THRESHOLD:
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
    for day in _daily_forecasts(weather)[:2]:
        outlook_parts.append(_format_daily_line(day, _daily_forecasts(weather)[0].date))

    if outlook_parts:
        lines.append("\n".join(outlook_parts))

    # ── Advice block ───────────────────────────────────────────
    if advice:
        advice_lines = _format_advice(advice)
        if advice_lines:
            lines.append("")
            lines.extend(advice_lines)

    # Append fallback note when OWM was configured but Open-Meteo was used instead
    if weather.source == "open-meteo" and os.environ.get("OPENWEATHERMAP_API_KEY"):
        lines.append("(via Open-Meteo -- alerts unavailable)")

    return "\n".join(lines)


def _format_advice(advice: WeatherAdvice) -> list[str]:
    """Format WeatherAdvice into advisory lines."""
    result = []

    # Umbrella decision: use a practical visual label, not generic decoration.
    if advice.umbrella_reasons:
        result.append(f"☔ Umbrella: bring one — {advice.umbrella_reasons[0]}")
    elif not advice.alerts_text:
        result.append("☔ Umbrella: not needed based on current forecast")

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