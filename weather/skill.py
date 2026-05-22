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

from datetime import datetime, timedelta
from typing import Optional

from .intent_classifier import classify_intent, WeatherIntent, build_travel_window
from . import weather_engine
from .weather_engine import (
    get_weather,
    get_commute_windows,
    resolve_location,
    LocationNotFoundError,
    WeatherEngineError,
)
from .advice_engine import (
    WEATHER_UMBRELLA_THRESHOLD,
    advice_for_location,
    commute_umbrella_check,
    score_best_time_windows,
)
from .evaluation_context import (
    build_evaluation_context,
    coerce_iso_datetime,
    detect_commute_direction,
    hourly_precip_by_hour,
    EvaluationContext,
)
from .formatters.chat_formatter import format_chat, format_forecast_range
from .formatters.briefing_formatter import format_briefing
from .weather_models import WeatherData


# ── Chat interface ───────────────────────────────────────────────

def get_weather_chat(query: str, departure_time: Optional[str] = None) -> str:
    """
    Handle an interactive weather query and return chat-formatted output.

    Args:
        query: natural-language weather query
        departure_time: optional HH:MM departure time for travel queries. When provided
            and the query has needs_time_clarify=True, injects the time directly instead
            of returning the clarification string. Ignored when not needed.

    Returns:
        Formatted chat string ready to send to user.
        On error, returns a user-friendly error message.
    """
    try:
        # Step 1: classify intent
        intent = classify_intent(query)

        # Step 2: build evaluation context (central date/time/location layer)
        ctx = build_evaluation_context(intent)

        # Step 3: handle travel queries with inline time clarification
        # If the caller supplies departure_time, resolve the pending vague travel
        # window before honoring the clarification state from the first context.
        if intent.is_travel and intent.needs_time_clarify and not intent.is_best_time_request:
            if departure_time:
                # Agent-native path: inject departure time directly, skip clarification
                intent.departure_time = departure_time
                window = build_travel_window(intent.time_reference, departure_time)
                intent.travel_window_start = window[0]
                intent.travel_window_end = window[1]
                intent.travel_window_label = window[2]
                intent.needs_time_clarify = False
                # Rebuild context with resolved time
                ctx = build_evaluation_context(intent)
            else:
                dest = intent.destination or "that location"
                return f"⏰ What time are you leaving for {dest} tomorrow?"

        # Step 4: handle clarification state for other incomplete contexts
        if ctx.needs_clarification:
            return ctx.clarification_prompt or "⏰ What time are you leaving?"

        # Step 5: range forecast surfaces
        range_mode = _detect_forecast_range_mode(query)
        if range_mode:
            return _handle_forecast_range_query(intent, range_mode, ctx)

        # Step 6: best-time optimization. Commute timing is handled separately
        # from outing/destination timing because it must score both Home and Office.
        if intent.is_commute and intent.is_best_time_request:
            return _handle_commute_best_time_query(intent, ctx)

        if intent.is_best_time_request:
            return _handle_best_time_query(intent)

        # Step 7: determine if dual-location needed (commute query)
        if intent.is_commute:
            return _handle_commute_query(intent, ctx)

        if intent.is_travel and intent.destination:
            return _handle_travel_query(intent, ctx)

        # Step 8: single location query
        return _handle_single_location_query(intent, ctx.primary_location, ctx)

    except LocationNotFoundError:
        return f"❓ Couldn't find that location — try a city name like 'London' or 'Atlanta'."
    except WeatherEngineError as e:
        return f"🌧️ Weather temporarily unavailable ({e}). Try again shortly."
    except Exception as e:
        return f"⚠️ Something went wrong: {e}"


def _detect_forecast_range_mode(query: str) -> Optional[str]:
    q = query.lower()
    # 14-day / 2-week must come before 7-day / week to avoid false matches
    if any(term in q for term in (
        "next 14 days", "14 day", "14-day", "fourteen day", "fourteen-day",
        "next 2 weeks", "next two weeks", "coming 2 weeks", "coming two weeks",
        "next fortnight",
    )):
        return "next_14_days"
    if any(term in q for term in (
        "next 10 days", "10 day", "10-day", "ten day", "ten-day",
    )):
        return "next_10_days"
    if any(term in q for term in (
        "next 7 days", "7 day", "7-day", "seven day", "seven-day",
        "week ahead", "next week",
    )):
        return "next_7_days"
    if any(term in q for term in ("rest of the week", "rest of week", "this week")):
        return "rest_of_week"
    return None


def _display_for_location(location_input: str, fallback: str) -> str:
    """Return the registry/geocoder display label for a weather location."""
    try:
        registry_entry, geo = resolve_location(location_input)
        display = registry_entry.get("display_name") if registry_entry else geo.name
        return display.replace("St. ", "St ") if isinstance(display, str) else fallback
    except Exception:
        return fallback


def _context_location_note(ctx: Optional[EvaluationContext]) -> str:
    if not ctx or ctx.location_source != "itinerary":
        return ""
    label = ctx.primary_display or ctx.primary_location
    suffix = f" ({ctx.itinerary_label})" if ctx.itinerary_label else ""
    return f"📍 Using travel context: {label}{suffix}"


def _handle_forecast_range_query(intent: WeatherIntent, mode: str, ctx: Optional[EvaluationContext] = None) -> str:
    location_input = (ctx.primary_location if ctx else None) or intent.location or "home"
    weather = get_weather(location_input)
    display = _display_for_location(location_input, weather.location_label)

    output = format_forecast_range(weather, mode=mode, location_display=display)
    note = _context_location_note(ctx)
    if note:
        output = f"{note}\n\n{output}"
    confirmation = getattr(weather, "registry_confirmation", None)
    if confirmation:
        output += f"\n\n{confirmation}"
    return output


def _handle_single_location_query(intent: WeatherIntent, location_input: str, ctx: Optional[EvaluationContext] = None) -> str:
    """Handle a single-location weather query."""
    weather = get_weather(location_input)
    advice = advice_for_location(weather)

    display = _display_for_location(location_input, weather.location_label)

    output = format_chat(weather, advice, location_display=display)
    note = _context_location_note(ctx)
    if note:
        output = f"{note}\n\n{output}"
    confirmation = getattr(weather, "registry_confirmation", None)
    if confirmation:
        output += f"\n\n{confirmation}"
    return output


def _parse_window(intent: WeatherIntent) -> tuple[datetime | None, datetime | None]:
    if not intent.travel_window_start or not intent.travel_window_end:
        return None, None
    return coerce_iso_datetime(intent.travel_window_start), coerce_iso_datetime(intent.travel_window_end)


def _travel_rain_reasons(weather: WeatherData, label: str, intent: WeatherIntent, threshold: int = WEATHER_UMBRELLA_THRESHOLD) -> list[str]:
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


def _handle_travel_query(intent: WeatherIntent, ctx: Optional[EvaluationContext] = None) -> str:
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

    # Window-scoped rain check using evaluation context
    reasons = []
    if ctx and ctx.has_window():
        origin_risky, origin_reasons = ctx.rain_in_window(origin_weather.hourly_precipitation)
        if origin_risky:
            reasons.extend(f"Origin: {r}" for r in origin_reasons)
        dest_risky, dest_reasons = ctx.rain_in_window(destination_weather.hourly_precipitation)
        if dest_risky:
            reasons.extend(f"Destination: {r}" for r in dest_reasons)
    else:
        # Fallback to legacy window parsing
        reasons.extend(_travel_rain_reasons(origin_weather, "origin", intent, threshold=WEATHER_UMBRELLA_THRESHOLD))
        reasons.extend(_travel_rain_reasons(destination_weather, "destination", intent, threshold=WEATHER_UMBRELLA_THRESHOLD))

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
        weather = weather_engine.get_weather(destination)
    except (LocationNotFoundError, WeatherEngineError):
        return f"❓ Couldn't get weather for {destination}. Try a different location."


    # Only clamp same-day live forecasts. Tests and non-today forecasts may carry
    # provider/model dates that are not today's date in the runtime environment.
    forecast_dates = {hp.time.date() for hp in weather.hourly_precipitation}
    today = datetime.now().date()
    now = datetime.now() if intent.target_date == "today" and today in forecast_dates else None
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
        scored_windows = fallback

    best = scored_windows[0]
    best_time = best.time
    precip = best.precip_probability
    temp = best.feels_like

    # Build window label
    window_label = best_time.strftime("%H:%M")
    window_end_label = (best_time.replace(minute=50) + timedelta(hours=1)).strftime("%H:%M")

    display = _display_for_location(destination, destination)

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
    if best.explanation:
        lines.append(f"Conditions: {best.explanation}.")

    # Ranked candidate windows with explanation
    if len(scored_windows) > 1:
        lines.append("")
        lines.append("📊 **Ranked candidates:**")
        for i, w in enumerate(scored_windows[:5], 1):
            marker = "✅" if i == 1 else "  "
            status = "best" if i == 1 else f"#{i}"
            expl = f" — {w.explanation}" if w.explanation else ""
            lines.append(f"{marker} {status}: {w.time:%H:%M} — {w.precip_probability}% rain risk{expl}")

    confirmations = getattr(weather, "registry_confirmation", None)
    if confirmations:
        lines.append("")
        lines.append(confirmations)

    return "\n".join(lines)


def _handle_commute_best_time_query(intent: WeatherIntent, ctx: Optional[EvaluationContext] = None) -> str:
    """Recommend the best departure hour within the configured commute window."""
    windows = get_commute_windows()
    direction = detect_commute_direction(intent.raw_query)
    window = windows.get(direction, {"start": "08:30", "end": "11:30"})
    direction_label = f"{direction} commute"
    ctx = ctx or build_evaluation_context(intent)
    ctx_window = ctx.primary_window()

    try:
        home_weather = get_weather("home")
        office_weather = get_weather("office")
    except (LocationNotFoundError, WeatherEngineError) as e:
        return f"❓ Couldn't get commute weather: {e}"

    home_by_hour = hourly_precip_by_hour(home_weather, ctx_window) if ctx_window else {}
    office_by_hour = hourly_precip_by_hour(office_weather, ctx_window) if ctx_window else {}
    candidate_hours = sorted(set(home_by_hour) | set(office_by_hour))

    if not candidate_hours:
        # Fall back to the start of the commute window when hourly rain data is unavailable.
        best_label = window["start"]
        lines = [
            f"🌧️ **Best time to leave:** {best_label} (limited hourly rain data)",
            f"☂️ **Umbrella:** check live conditions before leaving — no hourly commute rain signal available.",
            f"🕒 **Window checked:** {window['start']}–{window['end']} ({direction_label})",
        ]
        return "\n".join(lines)

    # Score: lower max precip across home+office is better
    ranked = sorted(
        (max(home_by_hour.get(hour, 0), office_by_hour.get(hour, 0)), hour)
        for hour in candidate_hours
    )
    best_prob, best_hour = ranked[0]
    best_label = f"{best_hour:02d}:00"
    needs_umbrella = best_prob >= WEATHER_UMBRELLA_THRESHOLD

    lines = [
        f"🌧️ **Best time to leave:** {best_label} — lowest commute rain risk ({best_prob}%).",
        (
            f"☂️ **Umbrella:** bring one — even the best slot is at/above {WEATHER_UMBRELLA_THRESHOLD}% rain risk."
            if needs_umbrella
            else f"☂️ **Umbrella:** probably not needed if you leave around {best_label}."
        ),
        f"🕒 **Window checked:** {window['start']}–{window['end']} ({direction_label})",
        "📍 **Checked:** Home + Office",
    ]

    # Show ranked candidate windows with explanation
    if len(ranked) > 1:
        lines.append("")
        lines.append("📊 **Ranked commute windows:**")
        for i, (prob, hour) in enumerate(ranked[:5], 1):
            marker = "✅" if i == 1 else "  "
            home_p = home_by_hour.get(hour, 0)
            office_p = office_by_hour.get(hour, 0)
            detail = f"Home {home_p}% / Office {office_p}%"
            lines.append(f"{marker} #{i}: {hour:02d}:00 — max {prob}% rain ({detail})")

    return "\n".join(lines)


def _handle_commute_query(intent: WeatherIntent, ctx: Optional[EvaluationContext] = None) -> str:
    """
    Handle a commute query: fetch weather for both home and office,
    check rain at both during commute window, and advise accordingly.
    """
    windows = get_commute_windows()
    direction = detect_commute_direction(intent.raw_query)
    window = windows.get(direction, {"start": "08:30", "end": "11:30"})
    direction_label = f"{direction} commute"
    ctx_window = ctx.primary_window() if ctx else None

    # Fetch weather for both locations
    try:
        home_weather = get_weather("home")
    except (LocationNotFoundError, WeatherEngineError) as e:
        return f"❓ Couldn't get home weather: {e}"

    try:
        office_weather = get_weather("office")
    except (LocationNotFoundError, WeatherEngineError) as e:
        return f"❓ Couldn't get office weather: {e}"

    # Commute umbrella check — window-scoped via evaluation context
    needs_umbrella, umbrella_reasons = commute_umbrella_check(
        home_weather,
        office_weather,
        window_start=window["start"],
        window_end=window["end"],
        threshold=WEATHER_UMBRELLA_THRESHOLD,
        window=ctx_window,
    )

    # Generate advice for both locations
    home_advice = advice_for_location(home_weather)
    office_advice = advice_for_location(office_weather)

    # Build output with rain/umbrella decision first.
    lines = []
    if needs_umbrella:
        lines.append(f"🌧️ **Rain decision:** Bring an umbrella for the {direction_label} ({window['start']}–{window['end']}).")
        lines.append("☂️ **Umbrella:** yes — rain risk at Home or Office crosses the threshold.")
        for reason in umbrella_reasons:
            lines.append(f"   • {reason}")
        lines.append(f"☔ **Commute umbrella:** Bring one — rain expected during {direction_label}")
    else:
        lines.append(f"🌧️ **Rain decision:** No umbrella needed for the {direction_label} ({window['start']}–{window['end']}).")
        lines.append("☂️ **Umbrella:** no — Home and Office stay below the rain threshold.")
        lines.append(f"☔ **Commute umbrella:** Not needed during {direction_label}")

    lines.append("")

    # Home block
    lines.append("🏠 **Home**")
    lines.append(format_chat(home_weather, home_advice, location_display="Home").split("\n", 1)[-1])

    # Office block
    lines.append("")
    lines.append("🏢 **Office**")
    lines.append(format_chat(office_weather, office_advice, location_display="Office").split("\n", 1)[-1])

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


def get_weather_briefing_for_today(now: Optional[datetime] = None) -> str:
    """Return today's briefing using active itinerary location when present.

    This is the safe default for scheduled morning briefing: travel/current
    location context wins for the current date, otherwise it falls back to home.
    Commute remains opt-in via get_weather_briefing_commute().
    """
    try:
        intent = WeatherIntent(location="home", time_reference="today", intent_type="general", raw_query="morning briefing weather")
        ctx = build_evaluation_context(intent, now=now)
        if ctx.needs_clarification:
            return ctx.clarification_prompt or "**Weather:** needs location clarification"
        output = get_weather_briefing(location=ctx.primary_location)
        note = _context_location_note(ctx)
        if note:
            return f"{note}\n{output}"
        return output
    except Exception as e:
        return f"**Weather:** check failed ({e})"


def get_weather_briefing_commute(query: str = "") -> str:
    """
    Return commute-aware briefing with both home and office conditions.

    Used by morning briefing when commute weather is relevant.

    Args:
        query: optional raw query used for commute direction detection (morning vs evening)
    """
    try:
        intent = classify_intent(query or "commute weather")
        intent.is_commute = True
        intent.is_travel = False
        ctx = build_evaluation_context(intent)
        ctx_window = ctx.primary_window()

        home_weather = get_weather("home")
        office_weather = get_weather("office")

        direction = detect_commute_direction(query)
        windows = get_commute_windows()
        commute_window = windows.get(direction, {"start": "08:30", "end": "11:30"})
        direction_label = f"{direction} commute"

        needs_umbrella, reasons = commute_umbrella_check(
            home_weather,
            office_weather,
            window_start=commute_window["start"],
            window_end=commute_window["end"],
            threshold=WEATHER_UMBRELLA_THRESHOLD,
            window=ctx_window,
        )

        home_advice = advice_for_location(home_weather)
        office_advice = advice_for_location(office_weather)

        home_brief = format_briefing(home_weather, home_advice)
        office_brief = format_briefing(office_weather, office_advice)

        output = f"**Home:** {home_brief}\n**Office:** {office_brief}"
        if needs_umbrella:
            output += f"\n**Commute:** Rain expected during {direction_label} — bring umbrella."
        else:
            output += f"\n**Commute:** No rain expected during {direction_label}."

        return output

    except Exception as e:
        return f"**Weather:** commute briefing failed ({e})"