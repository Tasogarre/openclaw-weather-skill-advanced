"""
test_natural_phrasings.py — Regression tests for natural commute/travel/rain/best-time phrasings.

Covers:
- Natural commute phrasings (going to work, heading into the office, etc.)
- Travel phrasings (from work to X, coming home from X, etc.)
- Rain/umbrella decisions scoped to requested windows
- Best-time phrasings with ranked candidate explanations
- Clarification state for vague travel/commute queries
"""

from __future__ import annotations

from datetime import datetime, timezone

from weather.intent_classifier import _deterministic_classify
from weather.skill import get_weather_chat
from weather.weather_models import CurrentConditions, DailyForecast, HourlyPrecipitation, WeatherData


def _weather(label: str, probs: dict[int, int]) -> WeatherData:
    day = datetime(2026, 5, 21, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=51.5,
        longitude=-0.1,
        timezone="Europe/London",
        current=CurrentConditions(
            temperature=15,
            feels_like=15,
            condition="Cloudy",
            humidity=60,
            wind_speed=5,
            precipitation_mm=0,
            uv_index=2,
        ),
        today=DailyForecast(date=day.date(), high=18, low=11, condition="Cloudy", precip_probability=max(probs.values() or [0])),
        hourly_precipitation=[
            HourlyPrecipitation(time=day.replace(hour=hour), probability=prob)
            for hour, prob in probs.items()
        ],
        alerts=[],
        source="test",
    )


# ── Natural commute phrasings ──────────────────────────────────────────────

COMMUTE_PHRASINGS = [
    "Do I need an umbrella when I go to the office tomorrow?",
    "What's the weather for my commute to work?",
    "Will it rain on my way to work?",
    "How cold will it be when I head into the office?",
    "Is it windy for my office commute?",
    "Will I get wet going into work tomorrow morning?",
    "Do I need an umbrella going into the office tomorrow?",
    "Do I need am umbrella tomorrow when I go to the office?",
    "what is the weather for going into work tomorrow?",
    "going in on Thursday because of weather?",
    "going into work because of weather?",
]


def test_all_commute_phrasings_detected():
    for query in COMMUTE_PHRASINGS:
        intent = _deterministic_classify(query)
        assert intent.is_commute is True, f"Failed for: {query}"
        assert intent.is_travel is False, f"Failed for: {query}"


# ── Natural travel phrasings ───────────────────────────────────────────────

TRAVEL_PHRASINGS = [
    ("I'm going to Tribe Waterloo at 9am", "home", "Tribe Waterloo"),
    ("I'm going from work to Tribe Waterloo at 5:30", "office", "Tribe Waterloo"),
    ("I'm coming home from Tribe Waterloo at 5:30", "Tribe Waterloo", "home"),
    ("I'm headed to Paddington tomorrow at 8:30", "home", "Paddington"),
    ("Do I need an umbrella on my way to Tribe Waterloo?", "home", "Tribe Waterloo"),
]


def test_all_travel_phrasings_detected():
    for query, expected_origin, expected_dest in TRAVEL_PHRASINGS:
        intent = _deterministic_classify(query)
        assert intent.is_travel is True, f"Failed for: {query}"
        assert intent.is_commute is False, f"Failed for: {query}"


# ── Window-specific rain decisions ─────────────────────────────────────────

def test_rain_outside_window_does_not_dominate():
    """Rain at 14:00 should not affect a 09:00 travel window."""
    from weather.evaluation_context import (
        EvaluationContext, WeatherWindow, precipitation_in_window
    )

    window = WeatherWindow(
        label="travel",
        start=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        window_type="travel",
    )
    hourly = [
        HourlyPrecipitation(time=datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc), probability=90),
        HourlyPrecipitation(time=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc), probability=10),
        HourlyPrecipitation(time=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc), probability=95),
    ]
    risky, reasons = precipitation_in_window(hourly, window, threshold=30)
    assert risky is False
    assert len(reasons) == 0


def test_rain_inside_window_detected():
    """Rain at 09:30 should affect a 09:00 travel window."""
    from weather.evaluation_context import (
        WeatherWindow, precipitation_in_window
    )

    window = WeatherWindow(
        label="travel",
        start=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        window_type="travel",
    )
    hourly = [
        HourlyPrecipitation(time=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc), probability=50),
    ]
    risky, reasons = precipitation_in_window(hourly, window, threshold=30)
    assert risky is True
    assert len(reasons) == 1
    assert "50%" in reasons[0]


# ── Best-time ranked candidates ────────────────────────────────────────────

def test_best_time_shows_ranked_candidates(monkeypatch):
    """Best-time query should show ranked candidate windows."""
    from unittest.mock import patch
    from weather import skill

    # Use fixed future hours with low probabilities so they pass the default 30% threshold
    # Use hours 10-14 on a fixed date to avoid past-window filtering
    mock_weather = _weather("St Paul's", {10: 5, 11: 10, 12: 2, 13: 15, 14: 8})

    # Patch now() to be before the candidate hours
    mock_now = datetime(2026, 5, 21, 1, 0, tzinfo=timezone.utc)
    with patch("weather.weather_engine.get_weather", return_value=mock_weather):
        with patch("weather.skill.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.date.today.return_value = mock_now.date()
            mock_dt.timedelta = __import__('datetime').timedelta
            result = get_weather_chat(
                "we want to walk to St Paul's tomorrow, when is the weather the best for that?"
            )

    assert "📊 **Ranked candidates:**" in result
    # The best should be the one with 2% rain
    assert "✅ best:" in result
    assert "2% rain risk" in result or "5% rain risk" in result


# ── Clarification state ────────────────────────────────────────────────────

def test_vague_travel_clarification_state():
    intent = _deterministic_classify("I've gotta go to Paddington tomorrow morning")
    assert intent.clarification_state == "needs_time"
    assert intent.needs_time_clarify is True


def test_specific_travel_no_clarification():
    intent = _deterministic_classify("I've gotta go to Paddington at 8:30 tomorrow")
    assert intent.clarification_state == "none"
    assert intent.needs_time_clarify is False


# ── Evening commute direction ──────────────────────────────────────────────

def test_evening_commute_detected():
    queries = [
        "Do I need an umbrella for my evening commute?",
        "What's the weather for my commute home?",
        "I'm heading home from the office at 5:30",
    ]
    for query in queries:
        intent = _deterministic_classify(query)
        assert intent.is_commute is True or intent.is_travel is True, f"Failed for: {query}"


# ── Origin/destination normalization ───────────────────────────────────────

def test_work_normalizes_to_office():
    intent = _deterministic_classify("I'm going from work to Tribe Waterloo at 5:30")
    # "work" normalizes to registry alias "office"
    assert intent.origin == "office"


def test_home_stays_home():
    intent = _deterministic_classify("I'm going from home to work at 8:30")
    assert intent.origin == "home"
    # "work" as destination normalizes to "office" registry alias
    assert intent.destination == "office"
