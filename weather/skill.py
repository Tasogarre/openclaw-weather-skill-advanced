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

from __future__ import annotations

from datetime import datetime
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
    score_best_time_windows,
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

        # Step 2: handle travel queries with inline time clarification
        if intent.is_travel and intent.needs_time_clarify and not intent.is_best_time_request:
            dest = intent.destination or "that location"
            return f"⏰ What time are you leaving for {dest} tomorrow?"

        # Step 3: best-time outing optimization (U7)
        if intent.is_best_time_request:
            return _handle_best_time_query(intent)

        # Step 4: determine if dual-location needed (commute query)
        if intent.is_commute:
            return _handle_commute_query(intent)

        if intent.is_travel and intent.destination:
            return _handle_travel_query(intent)


        # Step 5: single location query
        location_input = intent.location
        if intent.is_travel and intent.destination:
            location_input = intent.destination
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

    output = format_chat(weather, advice, location_display=display)
    confirmation = getattr(weather, "registry_confirmation", None)
    if confirmation:
        output += f"\n\n{confirmation}"
    return output


def _parse_window(intent: WeatherIntent) -> tuple[datetime | None, datetime | None]:
    if not intent.travel_window_start or not intent.travel_window_end:
        return None, None
    try:
        return datetime.fromisoformat(intent.travel_window_start), datetime.fromisoformat(intent.travel_window_end)
    except ValueError:
        return None, None


def _travel_rain_reasons(weather: WeatherData, label: str, intent: WeatherIntent, threshold: int = 30) -> list[str]:
    start, end = _parse_window(intent)
    if not start or not end:
        return []
    reasons = []
    for hp in weather.hourly_precipitation:
        hp_time = hp.time
        if hp_time.tzinfo and start.tzinfo:
            hp_time = hp_time.astimezone(start.tzinfo)
        if start <= hp_time <= end and hp.probability >= threshold:
            reasons.append(f"{label}: rain chance {hp.probability}% at {hp_time:%H:%M}")
            break
    return reasons


def _handle_travel_query(intent: WeatherIntent) -> str:
    destination = intent.destination or intent.location
    origin = intent.origin or "home"
    destination_weather = get_weather(destination)
    origin_weather = get_weather(origin)

    destination_advice = advice_for_location(destination_weather)
    origin_advice = advice_for_location(origin_weather)
    window_label = intent.travel_window_label or "the next hour"

    lines = [f"🕒 **Travel window:** {window_label}"]
    lines.append("")
    lines.append(f"📍 **Origin: {origin.title() if origin in ('home', 'office') else origin}**")
    lines.append(format_chat(origin_weather, origin_advice, location_display=origin.title() if origin in ("home", "office") else origin).split("\n", 1)[-1])
    lines.append("")
    lines.append(f"📍 **Destination: {destination}**")
    lines.append(format_chat(destination_weather, destination_advice, location_display=destination).split("\n", 1)[-1])

    reasons = []
    reasons.extend(_travel_rain_reasons(origin_weather, "origin", intent, threshold=30))
    reasons.extend(_travel_rain_reasons(destination_weather, "destination", intent, threshold=30))
    if reasons:
        lines.append("")
        lines.append(f"☔ **Travel umbrella:** Bring one for {window_label}.")
        lines.extend(f"   • {reason}" for reason in reasons)
    else:
        lines.append("")
        lines.append(f"☔ **Travel umbrella:** Not needed for {window_label} at origin or destination.")

    confirmations = [getattr(origin_weather, "registry_confirmation", None), getattr(destination_weather, "registry_confirmation", None)]
    confirmations = [c for c in confirmations if c]
    if confirmations:
        lines.append("")
        lines.extend(confirmations)
    return "\n".join(lines)


def _handle_best_time_query(intent: WeatherIntent) -> str:
    """
    Handle a best-time outing optimization query.

    Resolves destination, fetches forecast, scores candidate one-hour windows
    within the intent's candidate_window_start/end range, and returns the
    best practical window with weather conditions and a 'why' explanation.

    Does NOT ask for a departure time — choosing the time IS the output.
    """
    destination = intent.destination or intent.location

    start_hour = intent.candidate_window_start
    end_hour = intent.candidate_window_end

    if start_hour == 0 and end_hour == 0:
        return (
            f"❓ No practical weather window found for {destination}. "
            f"The best visiting hours for today have already passed — try again tomorrow."
        )


    try:
        weather = get_weather(destination)
    except (LocationNotFoundError, WeatherEngineError):
        return f"❓ Couldn't get weather for {destination}. Try a different location."


    now = datetime.now()
    scored_windows = score_best_time_windows(
        weather,
        candidate_start_hour=start_hour,
        candidate_end_hour=end_hour,
        now=now,
    )

    if not scored_windows:
        # Fallback: check if there's ANY hourly data with acceptable precip
        fallback = score_best_time_windows(
            weather,
            candidate_start_hour=start_hour,
            candidate_end_hour=end_hour,
            now=now,
            precip_threshold=60,  # relax threshold for fallback message
        )
        if not fallback:
            return (
                f"🌧️ Rain is expected throughout the best visiting window for {destination} today. "
                f"I'd recommend rescheduling if you can — the forecast doesn't offer a dry break in the usual visiting hours."
            )

    best = scored_windows[0]
    best_time = best.time
    precip = best.precip_probability
    temp = best.feels_like

    # Build window label
    window_label = best_time.strftime("%H:%M")
    window_end_label = (best_time.replace(minute=50) + __import__('datetime').timedelta(hours=1)).strftime("%H:%M")

    # Destination display name
    try:
        _, geo = resolve_location(destination)
        registry_entry, _ = resolve_location(destination)
        display = registry_entry.get("display_name") if registry_entry else geo.name
    except Exception:
        display = destination

    lines = []
    lines.append(f"🌤️ **Best time for {display}: {window_label}–{window_end_label}**")
    if precip <= 10:
        lines.append(f"Very low rain risk ({precip}% chance). Great window!")
    elif precip <= 20:
        lines.append(f"Low rain risk ({precip}% chance). Good window!")
    else:
        lines.append(f"Rain chance {precip}% — the best of a challenging day.")
    if temp is not None:
        lines.append(f"Feels like {temp:.0f}°C.")
    lines.append(f"")
    lines.append("📍 **Why this window:**")
    lines.append(f"Scored {len(scored_windows)} candidate hour(s). Lowest precipitation in the practical outing window")
    lines.append(f"({start_hour:02d}:00–{end_hour:02d} local time).")

    confirmations = getattr(weather, "registry_confirmation", None)
    if confirmations:
        lines.append("")
        lines.append(confirmations)

    return "\n".join(lines)


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
        lines.append(f"☔ **Commute umbrella:** Bring one — rain expected during morning commute")
        for reason in umbrella_reasons:
            lines.append(f"   • {reason}")
    else:
        lines.append("")
        lines.append("☔ **Commute umbrella:** Not needed during morning commute")

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