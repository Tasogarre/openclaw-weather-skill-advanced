"""
test_evaluation_context.py — Regression tests for evaluation context layer.

Covers:
- Date/time/location context extraction from intents
- Window-scoped rain decisions
- Origin/destination resolution for natural phrases
- Clarification state handling
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from weather.evaluation_context import (
    build_evaluation_context,
    coerce_iso_datetime,
    detect_commute_direction,
    hourly_precip_by_hour,
    EvaluationContext,
    WeatherWindow,
)
from weather.intent_classifier import WeatherIntent, _deterministic_classify
from weather.weather_models import HourlyPrecipitation, WeatherData


def _make_hourly_precip(day: int, hour: int, prob: int):
    return HourlyPrecipitation(
        time=datetime(2026, 5, day, hour, 0, tzinfo=timezone.utc),
        probability=prob,
    )


def test_context_extracts_tomorrow_date():
    intent = _deterministic_classify("Do I need an umbrella going to the office tomorrow?")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 21, 12, tzinfo=timezone.utc))
    assert ctx.time_reference == "tomorrow"
    assert ctx.is_commute is True


def test_context_extracts_today_date():
    intent = _deterministic_classify("Weather at home today")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 21, 12, tzinfo=timezone.utc))
    assert ctx.time_reference == "today"


def test_commute_has_window_from_registry():
    intent = _deterministic_classify("Do I need an umbrella going to the office tomorrow?")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 21, 12, tzinfo=timezone.utc))
    assert ctx.has_window() is True
    w = ctx.primary_window()
    assert w is not None
    assert w.start.hour == 8
    assert w.end.hour == 11


def test_travel_window_from_departure_time():
    intent = _deterministic_classify("I'm going to Paddington tomorrow at 9:30")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 21, 12, tzinfo=timezone.utc))
    assert ctx.has_window() is True
    w = ctx.primary_window()
    assert w is not None
    assert w.start.hour == 9
    assert w.end.hour == 10


def test_vague_travel_needs_clarification():
    intent = _deterministic_classify("I've gotta go to Paddington tomorrow morning")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 21, 12, tzinfo=timezone.utc))
    assert ctx.needs_clarification is True
    assert "What time" in (ctx.clarification_prompt or "")


def test_context_rain_in_window_only_counts_inside_window():
    ctx = EvaluationContext(
        windows=[WeatherWindow(
            label="test",
            start=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc),
        )]
    )
    hourly = [
        _make_hourly_precip(22, 8, 90),   # before window
        _make_hourly_precip(22, 9, 10),   # in window
        _make_hourly_precip(22, 11, 90),  # after window
    ]
    will_rain, reasons = ctx.rain_in_window(hourly)
    assert will_rain is False
    assert len(reasons) == 0


def test_context_rain_in_window_detects_risk():
    ctx = EvaluationContext(
        windows=[WeatherWindow(
            label="test",
            start=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc),
        )]
    )
    hourly = [
        _make_hourly_precip(22, 9, 50),   # in window, above threshold
    ]
    will_rain, reasons = ctx.rain_in_window(hourly)
    assert will_rain is True
    assert len(reasons) == 1
    assert "50%" in reasons[0]


def test_origin_destination_from_work_to_place():
    intent = _deterministic_classify("I'm going from work to Tribe Waterloo at 5:30")
    ctx = build_evaluation_context(intent)
    # "work" normalizes to registry alias "office" for consistent resolution
    assert ctx.origin == "office"
    assert ctx.destination == "Tribe Waterloo"


def test_origin_destination_coming_home_from_place():
    intent = _deterministic_classify("I'm coming home from Tribe Waterloo at 5:30")
    ctx = build_evaluation_context(intent)
    assert ctx.origin == "Tribe Waterloo"
    assert ctx.destination == "home"


def test_origin_destination_from_home_to_work():
    intent = _deterministic_classify("I'm going from home to work at 8:30")
    ctx = build_evaluation_context(intent)
    assert ctx.origin == "home"
    assert ctx.destination == "office"


def test_travel_defaults_origin_to_home():
    intent = _deterministic_classify("I'm going to Tribe Waterloo at 9:00")
    ctx = build_evaluation_context(intent)
    assert ctx.origin == "home"
    assert ctx.destination == "Tribe Waterloo"


def test_window_specific_true_for_short_windows():
    ctx = EvaluationContext(
        windows=[WeatherWindow(
            label="test",
            start=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc),
        )]
    )
    assert ctx.is_window_specific() is True


def test_window_specific_false_for_broad_windows():
    ctx = EvaluationContext(
        windows=[WeatherWindow(
            label="test",
            start=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, 19, 0, tzinfo=timezone.utc),
        )]
    )
    assert ctx.is_window_specific() is False


def test_clarification_state_needs_time_for_vague_travel():
    intent = _deterministic_classify("I've gotta go to Paddington tomorrow morning")
    assert intent.clarification_state == "needs_time"


def test_clarification_state_none_for_specific_travel():
    intent = _deterministic_classify("I've gotta go to Paddington at 8:30 tomorrow")
    assert intent.clarification_state == "none"


def test_coerce_iso_datetime_handles_bad_model_fields():
    assert coerce_iso_datetime(None) is None
    assert coerce_iso_datetime(123) is None
    assert coerce_iso_datetime("not a datetime") is None


def test_central_commute_direction_detects_evening_variants():
    assert detect_commute_direction("What's the weather for my commute home?") == "evening"
    assert detect_commute_direction("I'm heading home from the office at 5:30") == "evening"
    assert detect_commute_direction("Do I need an umbrella going into the office tomorrow?") == "morning"


def test_hourly_precip_by_hour_uses_resolved_date_not_time_only():
    weather = WeatherData(
        location_label="Home",
        latitude=51.5,
        longitude=-0.1,
        timezone="Europe/London",
        hourly_precipitation=[
            _make_hourly_precip(21, 9, 90),
            _make_hourly_precip(22, 9, 10),
        ],
    )
    window = WeatherWindow(
        label="tomorrow commute",
        start=datetime(2026, 5, 22, 8, 30, tzinfo=timezone.utc),
        end=datetime(2026, 5, 22, 11, 30, tzinfo=timezone.utc),
    )
    assert hourly_precip_by_hour(weather, window) == {9: 10}
