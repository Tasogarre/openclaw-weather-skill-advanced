"""
skill.py — Phase 2 Weather Skill Entry Point v1.2

Ties together: intent classifier, weather engine, advice engine, and formatters.

Usage (from Python):
    from skills.weather.skill import get_weather_chat, get_weather_briefing

    # Interactive chat query
    result = get_weather_chat("Do I need an umbrella when I go to the office tomorrow?")
    print(result)

    # Morning briefing
    briefing = get_weather_briefing(location="home")
    print(briefing)

Skill invocation chain:
    User query
      → classify_intent()  (Ollama + deterministic fallback)
      → resolve location    (registry + geocoding)
      → fetch weather       (dual-provider, auto-fallback)
      → generate advice     (advice_for_location / commute_umbrella_check)
      → format output       (chat_formatter / briefing_formatter)
      → return string

For commute queries:
    → fetch home AND office weather
    → check rain at both locations during commute window
    → advise umbrella if EITHER shows rain >30%
"""

from typing import Optional

from .intent_classifier import classify_intent, WeatherIntent
from .weather_engine import (
    get_weather,
    get_commute_windows,
    resolve_location,
    LocationNotFoundError,
    WeatherEngineError,
)
from .advice_engine import (
    advice_for_location,
    commute_umbrella_check,
    WeatherAdvice,
)
from .formatters.chat_formatter import format_chat
from .formatters.briefing_formatter import format_briefing
from .weather_models import WeatherData


# ── Chat interface ───────────────────────────────────────────────

def get_weather_chat(query: str) -> str:
    """
    Handle an interactive weather query and return chat-formatted output.

    Args:
        query: natural-language weather query

    Returns:
        Formatted chat string ready to send to user.
        On error, returns a user-friendly error message.
    """
    try:
        # Step 1: classify intent
        intent = classify_intent(query)
        location_input = intent.location

        # Step 2: determine if dual-location needed (commute query)
        if intent.is_commute:
            return _handle_commute_query(intent)
        else:
            return _handle_single_location_query(intent, location_input)

    except LocationNotFoundError:
        return f"❓ Couldn't find that location — try a city name like 'London' or 'Atlanta'."
    except WeatherEngineError as e:
        return f"🌧️ Weather temporarily unavailable ({e}). Try again shortly."
    except Exception as e:
        return f"⚠️ Something went wrong: {e}"


def _handle_single_location_query(intent: WeatherIntent, location_input: str) -> str:
    """Handle a single-location weather query."""
    weather = get_weather(location_input)
    advice = advice_for_location(weather)

    # Resolve location display name
    try:
        _, geo = resolve_location(location_input)
        registry_entry, _ = resolve_location(location_input)
        display = registry_entry.get("display_name") if registry_entry else geo.name
    except Exception:
        display = weather.location_label

    return format_chat(weather, advice, location_display=display)


def _handle_commute_query(intent: WeatherIntent) -> str:
    """
    Handle a commute query: fetch weather for both home and office,
    check rain at both during commute window, and advise accordingly.
    """
    windows = get_commute_windows()
    morning = windows.get("morning", {"start": "08:30", "end": "11:30"})

    # Fetch weather for both locations
    try:
        home_weather = get_weather("home")
    except (LocationNotFoundError, WeatherEngineError) as e:
        return f"❓ Couldn't get home weather: {e}"

    try:
        office_weather = get_weather("office")
    except (LocationNotFoundError, WeatherEngineError) as e:
        return f"❓ Couldn't get office weather: {e}"

    # Commute umbrella check
    needs_umbrella, umbrella_reasons = commute_umbrella_check(
        home_weather,
        office_weather,
        window_start=morning["start"],
        window_end=morning["end"],
        threshold=30,
    )

    # Generate advice for both locations
    home_advice = advice_for_location(home_weather)
    office_advice = advice_for_location(office_weather)

    # Build output with both locations
    lines = []

    # Home block
    lines.append("🏠 **Home**")
    lines.append(format_chat(home_weather, home_advice, location_display="Home").split("\n", 1)[-1])

    # Office block
    lines.append("")
    lines.append("🏢 **Office**")
    lines.append(format_chat(office_weather, office_advice, location_display="Office").split("\n", 1)[-1])

    # Commute advice
    if needs_umbrella:
        lines.append("")
        lines.append(f"💡 **Commute advice:** Bring an umbrella — rain expected during morning commute")
        for reason in umbrella_reasons:
            lines.append(f"   • {reason}")
    else:
        lines.append("")
        lines.append("💡 **Commute advice:** No rain expected during morning commute")

    return "\n".join(lines)


# ── Briefing interface (morning briefing integration) ─────────────

def get_weather_briefing(location: str = "home") -> str:
    """
    Return a compact briefing-formatted weather string.

    Used by bin/morning_briefing.py as the new weather path.

    Args:
        location: registry alias (default: "home") or free-text

    Returns:
        Single-line or short-paragraph formatted weather string.
        On error, returns a fallback string.
    """
    try:
        weather = get_weather(location)
        advice = advice_for_location(weather)
        return format_briefing(weather, advice)
    except LocationNotFoundError:
        return f"**Weather:** location not found ({location})"
    except WeatherEngineError as e:
        return f"**Weather:** unavailable — {e}"
    except Exception as e:
        return f"**Weather:** check failed ({e})"


def get_weather_briefing_commute() -> str:
    """
    Return commute-aware briefing with both home and office conditions.

    Used by morning briefing when commute weather is relevant.
    """
    try:
        home_weather = get_weather("home")
        office_weather = get_weather("office")

        windows = get_commute_windows()
        morning = windows.get("morning", {"start": "08:30", "end": "11:30"})

        needs_umbrella, reasons = commute_umbrella_check(
            home_weather,
            office_weather,
            window_start=morning["start"],
            window_end=morning["end"],
            threshold=30,
        )

        home_advice = advice_for_location(home_weather)
        office_advice = advice_for_location(office_weather)

        home_brief = format_briefing(home_weather, home_advice)
        office_brief = format_briefing(office_weather, office_advice)

        output = f"**Home:** {home_brief}\n**Office:** {office_brief}"
        if needs_umbrella:
            output += "\n**Commute:** Rain expected during morning commute — bring umbrella."
        else:
            output += "\n**Commute:** No rain expected during morning commute."

        return output

    except Exception as e:
        return f"**Weather:** commute briefing failed ({e})"