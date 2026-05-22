"""
evaluation_context.py — Central date/time/location evaluation context layer for weather requests.

Provides a unified context object that captures:
  - The resolved location(s) for a weather query
  - The relevant time window(s) (commute, travel, best-time outing)
  - Whether the query is window-specific or general
  - Origin/destination modeling for travel queries

This layer sits between intent classification and weather fetching/advice,
ensuring that rain/umbrella decisions are scoped to the requested window
and do not dominate answers with out-of-window conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from .weather_models import WeatherData, HourlyPrecipitation


@dataclass
class WeatherWindow:
    """A specific time window for weather evaluation."""
    label: str                           # e.g. "morning commute", "travel to Paddington"
    start: datetime
    end: datetime
    window_type: str = "general"         # commute | travel | best_time | general
    origin_location: Optional[str] = None
    destination_location: Optional[str] = None


@dataclass
class EvaluationContext:
    """
    Central evaluation context for a weather request.

    Holds all locations and time windows relevant to the query so that
    downstream engines can make window-scoped decisions.
    """
    primary_location: str = "home"
    primary_display: Optional[str] = None
    windows: list[WeatherWindow] = field(default_factory=list)
    is_commute: bool = False
    is_travel: bool = False
    is_best_time: bool = False
    origin: Optional[str] = None         # registry alias or free text
    destination: Optional[str] = None    # registry alias or free text
    time_reference: str = "now"          # now | today | tomorrow | YYYY-MM-DD
    evaluation_date: Optional[date] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    raw_query: str = ""

    # Legacy convenience accessors for backward compatibility with tests
    @property
    def window_start(self) -> Optional[datetime]:
        return self.windows[0].start if self.windows else None

    @property
    def window_end(self) -> Optional[datetime]:
        return self.windows[0].end if self.windows else None

    def add_window(self, window: WeatherWindow) -> None:
        self.windows.append(window)

    def primary_window(self) -> Optional[WeatherWindow]:
        return self.windows[0] if self.windows else None

    def has_window(self) -> bool:
        return bool(self.windows)

    def is_window_specific(self) -> bool:
        """Return True if the window is short enough to be considered specific."""
        if not self.windows:
            return False
        w = self.windows[0]
        duration = w.end - w.start
        return duration <= timedelta(hours=4)

    def rain_in_window(
        self,
        hourly_precipitation: list[HourlyPrecipitation],
        threshold: int = 30,
    ) -> tuple[bool, list[str]]:
        """
        Check whether precipitation is expected during the primary window.

        Only considers hourly precipitation blocks that fall WITHIN the window.
        Rain outside the window is ignored.

        Args:
            hourly_precipitation: list of HourlyPrecipitation
            threshold: min probability % to count as "raining"

        Returns:
            (has_precipitation: bool, reasons: list[str])
        """
        window = self.primary_window()
        if not window:
            return False, []
        return precipitation_in_window(
            hourly_precipitation=hourly_precipitation,
            window=window,
            threshold=threshold,
        )


def build_evaluation_context(
    intent,
    *,
    now: Optional[datetime] = None,
    default_commute_windows: Optional[dict] = None,
) -> EvaluationContext:
    """
    Build an EvaluationContext from a classified WeatherIntent.

    Args:
        intent: WeatherIntent from classify_intent()
        now: optional reference datetime (defaults to UTC now)
        default_commute_windows: dict with "morning" and "evening" entries

    Returns:
        EvaluationContext ready for weather fetching and advice.
    """
    from .intent_classifier import WeatherIntent
    from .weather_engine import get_commute_windows

    now = now or datetime.now(timezone.utc)
    ctx = EvaluationContext(
        primary_location=intent.location or "home",
        is_commute=intent.is_commute,
        is_travel=intent.is_travel,
        is_best_time=intent.is_best_time_request,
        origin=intent.origin,
        destination=intent.destination,
        time_reference=intent.time_reference,
        evaluation_date=_resolve_evaluation_date(intent.time_reference, now),
        raw_query=intent.raw_query,
    )

    # ── Clarification state ───────────────────────────────────
    if getattr(intent, 'needs_time_clarify', False) and intent.is_travel and not intent.is_best_time_request:
        ctx.needs_clarification = True
        dest = intent.destination or "that location"
        ctx.clarification_prompt = f"⏰ What time are you leaving for {dest} tomorrow?"
        return ctx

    # ── Commute windows ───────────────────────────────────────
    if intent.is_commute:
        windows_cfg = default_commute_windows or get_commute_windows()
        direction = detect_commute_direction(intent.raw_query)
        window_cfg = windows_cfg.get(direction, {"start": "08:30", "end": "11:30"})
        start_dt, end_dt = _parse_window_to_datetimes(
            intent.time_reference, window_cfg["start"], window_cfg["end"], now
        )
        ctx.add_window(WeatherWindow(
            label=f"{direction} commute ({window_cfg['start']}–{window_cfg['end']})",
            start=start_dt,
            end=end_dt,
            window_type="commute",
            origin_location="home" if direction == "morning" else "office",
            destination_location="office" if direction == "morning" else "home",
        ))
        ctx.origin = ctx.windows[0].origin_location
        ctx.destination = ctx.windows[0].destination_location
        return ctx

    # ── Travel windows ────────────────────────────────────────
    if intent.is_travel and intent.travel_window_start and intent.travel_window_end:
        start_dt = coerce_iso_datetime(intent.travel_window_start)
        end_dt = coerce_iso_datetime(intent.travel_window_end)
        if start_dt is None or end_dt is None:
            return ctx
        ctx.add_window(WeatherWindow(
            label=intent.travel_window_label or f"{start_dt:%H:%M}–{end_dt:%H:%M}",
            start=start_dt,
            end=end_dt,
            window_type="travel",
            origin_location=intent.origin or "home",
            destination_location=intent.destination or intent.location,
        ))
        return ctx

    # ── Best-time windows ─────────────────────────────────────
    if intent.is_best_time_request:
        # Best-time queries do not have a fixed travel window; instead they
        # search candidate windows. The context marks this mode so the handler
        # can score multiple candidates.
        ctx.is_best_time = True
        return ctx

    # ── General / single-location ─────────────────────────────
    # No specific window — general forecast for the day
    return ctx


def coerce_iso_datetime(value: object) -> Optional[datetime]:
    """Best-effort ISO datetime parser for model/deterministic intent fields."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _resolve_evaluation_date(time_reference: str, now: datetime) -> Optional[date]:
    """Resolve the evaluation date from a time reference string."""
    if not isinstance(time_reference, str) or not time_reference.strip():
        return now.date()
    if time_reference == "tomorrow":
        return (now + timedelta(days=1)).date()
    if time_reference == "today":
        return now.date()
    if time_reference == "now":
        return now.date()
    # Try to parse as ISO date
    try:
        return datetime.fromisoformat(time_reference).date()
    except ValueError:
        pass
    return now.date()


def detect_commute_direction(raw_query: str) -> str:
    """Return 'evening' or 'morning' based on query signals."""
    q = raw_query.lower()
    evening_signals = (
        "evening", "afternoon", "commute home", "heading home",
        "leaving work", "home commute", "from work", "from the office",
        "from office", "coming home", "back home", "after work",
    )
    if any(s in q for s in evening_signals):
        return "evening"
    return "morning"


def _parse_window_to_datetimes(
    time_reference: str,
    window_start: str,
    window_end: str,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Parse HH:MM strings into datetimes relative to time_reference."""
    base = _base_date_for_reference(time_reference, now)
    start_h, start_m = map(int, window_start.split(":"))
    end_h, end_m = map(int, window_end.split(":"))
    start = base.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = base.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _base_date_for_reference(time_reference: str, now: datetime) -> datetime:
    """Return a base datetime for the given time reference."""
    if time_reference == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if time_reference == "next-week":
        return (now + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Default to today
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ── Window-scoped precipitation evaluation ─────────────────────

def precipitation_in_window(
    hourly_precipitation: list[HourlyPrecipitation] | WeatherData,
    window: WeatherWindow,
    threshold: int = 30,
) -> tuple[bool, list[str]]:
    """
    Check whether precipitation is expected during a specific window.

    Only considers hourly precipitation blocks that fall WITHIN the window.
    Rain outside the window is ignored.

    Args:
        hourly_precipitation: list of HourlyPrecipitation
        window: WeatherWindow defining the evaluation period
        threshold: min probability % to count as "raining"

    Returns:
        (has_precipitation: bool, reasons: list[str])
    """
    reasons: list[str] = []
    window_start = window.start
    window_end = window.end

    # Unwrap WeatherData if passed directly
    if hasattr(hourly_precipitation, "hourly_precipitation"):
        hourly_precipitation = hourly_precipitation.hourly_precipitation

    for hp in hourly_precipitation:
        hp_time = hp.time
        if hp_time.tzinfo and window_start.tzinfo:
            hp_time = hp_time.astimezone(window_start.tzinfo)
        elif window_start.tzinfo and not hp_time.tzinfo:
            # Assume naive hourly data is in the same timezone as the window
            hp_time = hp_time.replace(tzinfo=window_start.tzinfo)

        if window_start <= hp_time <= window_end and hp.probability >= threshold:
            reasons.append(
                f"rain chance {hp.probability}% at {hp_time:%H:%M}"
            )

    return bool(reasons), reasons


def max_precipitation_in_window(
    weather: WeatherData,
    window: WeatherWindow,
) -> int:
    """Return the maximum precipitation probability % within the window."""
    max_prob = 0
    for hp in weather.hourly_precipitation:
        hp_time = hp.time
        if hp_time.tzinfo and window.start.tzinfo:
            hp_time = hp_time.astimezone(window.start.tzinfo)
        elif window.start.tzinfo and not hp_time.tzinfo:
            hp_time = hp_time.replace(tzinfo=window.start.tzinfo)
        if window.start <= hp_time <= window.end:
            max_prob = max(max_prob, hp.probability)
    return max_prob


def hourly_precip_by_hour(
    weather: WeatherData,
    window: WeatherWindow,
) -> dict[int, int]:
    """Return max precipitation probability by hour inside the window."""
    result: dict[int, int] = {}
    for hp in weather.hourly_precipitation:
        hp_time = hp.time
        if hp_time.tzinfo and window.start.tzinfo:
            hp_time = hp_time.astimezone(window.start.tzinfo)
        elif window.start.tzinfo and not hp_time.tzinfo:
            hp_time = hp_time.replace(tzinfo=window.start.tzinfo)
        if window.start <= hp_time <= window.end:
            result[hp_time.hour] = max(result.get(hp_time.hour, 0), hp.probability)
    return result
