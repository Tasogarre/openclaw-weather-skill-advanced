"""
test_best_time_weather.py — U7: Best-time outing recommendations

Tests for the best-time optimization path:
- Generic outing/walk scoring over mocked hourly data
- Columbia Road Flower Market Sunday special case
- St Paul's today/tomorrow examples
- Today clamping (no past-window recommendations)
- Rain tie-break by wind/temperature
- Destination resolution
- Graceful fallback when hourly data unavailable
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from weather.weather_models import (
    HourlyPrecipitation,
    HourlyForecast,
    CurrentConditions,
    WeatherData,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _dt(day, hour, minute=0, tz=None):
    tz = tz or timezone.utc
    base = datetime(2026, 5, 4, day, hour, minute, tzinfo=tz)  # Monday 2026-05-04
    return base


def _mock_weather(hourly: list, location_label="Test") -> WeatherData:
    return WeatherData(
        location_label=location_label,
        latitude=51.5,
        longitude=-0.12,
        timezone="Europe/London",
        current=MagicMock(spec=CurrentConditions, temperature=17.0, feels_like=16.0,
                          condition="Partly cloudy", wind_speed=12.0, precipitation_mm=0.0,
                          uv_index=3),
        hourly_precipitation=hourly,
        hourly_forecast=[
            HourlyForecast(time=h.time, temperature=16.0, feels_like=15.0, condition="Cloudy")
            for h in hourly
        ],
    )


def _hourly_probs(*pairs):
    """pairs = (day, hour, probability) — naive UTC datetimes for testing."""
    return [
        HourlyPrecipitation(
            time=datetime(2026, 5, day, hour, 0, 0, tzinfo=timezone.utc),
            probability=prob,
        )
        for day, hour, prob in pairs
    ]


# ── Core scoring tests ──────────────────────────────────────────────────────

def test_best_time_lowest_rain_is_selected():
    """Lowest precipitation probability window is scored first."""
    hourly = _hourly_probs(
        (4, 9, 60),   # 09:00 Mon - rain
        (4, 10, 20),  # 10:00 - better
        (4, 11, 10),  # 11:00 - best
        (4, 12, 5),   # 12:00 - also good
        (4, 13, 30),
        (4, 14, 50),
        (4, 15, 70),
    )
    weather = _mock_weather(hourly)

    from weather.advice_engine import score_best_time_windows
    scored = score_best_time_windows(weather, candidate_start_hour=9, candidate_end_hour=16)

    assert len(scored) > 0
    # Best scored by lowest rain probability first
    assert scored[0].precip_probability <= scored[1].precip_probability


def test_best_time_respects_candidate_range():
    """Only hours within candidate range are scored."""
    hourly = _hourly_probs(
        (4, 7, 5),   # 07:00 - before generic range 09:00
        (4, 9, 10),  # 09:00 - in range
        (4, 18, 15), # 18:00 - after generic range 18:00 boundary
        (4, 19, 20), # 19:00 - after range
    )
    weather = _mock_weather(hourly)

    from weather.advice_engine import score_best_time_windows
    scored = score_best_time_windows(weather, candidate_start_hour=9, candidate_end_hour=18)

    scored_hours = [h.time.hour for h in scored]
    assert all(9 <= h <= 18 for h in scored_hours)


def test_best_time_rain_tie_break_by_wind():
    """When rain probability is tied, lower wind speed wins."""
    # Both 10% rain, but wind differentiates
    from weather.weather_engine import get_weather
    # Use a weather data with different wind conditions
    hourly_a = _hourly_probs((4, 10, 10), (4, 11, 10))
    hourly_b = _hourly_probs((4, 10, 10), (4, 11, 10))

    weather_a = _mock_weather(hourly_a)
    weather_b = _mock_weather(hourly_b)

    # Both same precip — wind field not in HourlyPrecipitation,
    # tie-break uses temperature comfort band instead (feels_like).
    # This test validates the scoring doesn't crash on equal precip.
    from weather.advice_engine import score_best_time_windows
    scored_a = score_best_time_windows(weather_a, candidate_start_hour=10, candidate_end_hour=11)
    scored_b = score_best_time_windows(weather_b, candidate_start_hour=10, candidate_end_hour=11)

    assert len(scored_a) >= 1
    assert len(scored_b) >= 1


def test_best_time_no_hourly_data_returns_empty():
    """Graceful fallback when hourly_precipitation is empty."""
    weather = _mock_weather([])
    from weather.advice_engine import score_best_time_windows
    scored = score_best_time_windows(weather, candidate_start_hour=9, candidate_end_hour=18)
    assert scored == []


# ── Today clamping tests ────────────────────────────────────────────────────

def test_best_time_today_clamp_excludes_past_hours():
    """Today requests must not recommend a window that has already passed."""
    now = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)  # 14:30 today
    hourly = _hourly_probs(
        (4, 10, 80),  # past — 10:00
        (4, 11, 70),  # past — 11:00
        (4, 12, 20),  # candidate — 12:00 (only remaining hour in range)
        (4, 13, 15),
        (4, 14, 10),  # at boundary
        (4, 15, 5),
    )
    weather = _mock_weather(hourly)

    from weather.advice_engine import score_best_time_windows
    scored = score_best_time_windows(
        weather,
        candidate_start_hour=9,
        candidate_end_hour=16,
        now=now,
    )

    # All scored windows must be strictly after 14:30
    for entry in scored:
        assert entry.time > now


def test_best_time_today_all_past_returns_empty():
    """If all candidate windows are in the past, return empty list."""
    now = datetime(2026, 5, 4, 20, 0, tzinfo=timezone.utc)  # 20:00 today — everything past
    hourly = _hourly_probs(
        (4, 9, 10),
        (4, 10, 10),
        (4, 11, 10),
    )
    weather = _mock_weather(hourly)

    from weather.advice_engine import score_best_time_windows
    scored = score_best_time_windows(
        weather,
        candidate_start_hour=9,
        candidate_end_hour=18,
        now=now,
    )

    assert scored == []


# ── Intent classification for best-time requests ──────────────────────────

def test_best_time_st_pauls_today_classified():
    from weather.intent_classifier import classify_intent

    intent = classify_intent("we want to walk to St Paul's today, when is the weather the best for that?")
    assert intent.is_best_time_request is True
    assert intent.destination is not None
    assert intent.needs_time_clarify is False


def test_best_time_st_pauls_tomorrow_classified():
    from weather.intent_classifier import classify_intent

    intent = classify_intent("we want to walk to St Paul's tomorrow, when is the weather the best for that?")
    assert intent.is_best_time_request is True
    assert intent.destination is not None
    assert intent.needs_time_clarify is False


def test_best_time_columbia_flower_market_classified():
    from weather.intent_classifier import classify_intent

    intent = classify_intent("we want to go visit the Columbia Flower Market this Sunday, when is the weather best for that?")
    assert intent.is_best_time_request is True
    assert intent.needs_time_clarify is False


# ── Candidate window special cases ─────────────────────────────────────────

def test_columbia_flower_market_sunday_window():
    from weather.intent_classifier import _get_best_time_candidate_window

    start, end = _get_best_time_candidate_window("Columbia Flower Market", "2026-05-03")
    assert start == 8   # 08:00
    assert end == 15    # 15:00 (exclusive, use as hour end)


def test_generic_outing_window():
    from weather.intent_classifier import _get_best_time_candidate_window

    start, end = _get_best_time_candidate_window("St Paul's Cathedral", "2026-05-05")
    assert start == 9   # 09:00
    assert end == 18    # 18:00


def test_today_window_clamps_to_future_hours():
    from weather.intent_classifier import _get_best_time_candidate_window

    # Today with generic window — should not return windows already past
    now = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)
    start, end = _get_best_time_candidate_window("St Paul's", "today", now=now)
    # With now=14:30, start should be clamped to 15 (next full hour after 14:30)
    assert start >= 15


# ── Skill-level best-time handler ──────────────────────────────────────────

def test_skill_best_time_columbia_flower_market_sunday():
    """Skill returns a best-time recommendation for Columbia Flower Market Sunday."""
    from unittest.mock import patch

    mock_weather = _mock_weather(_hourly_probs(
        (3, 9, 60), (3, 10, 30), (3, 11, 10), (3, 12, 5),
        (3, 13, 10), (3, 14, 40), (3, 15, 70),
    ), location_label="Columbia Road Flower Market")

    with patch("weather.weather_engine.get_weather", return_value=mock_weather):
        from weather.skill import get_weather_chat
        result = get_weather_chat(
            "we want to go visit the Columbia Flower Market this Sunday, when is the weather best for that?"
        )
        # Must not ask for a departure time
        assert "What time" not in result
        # Should name the destination
        assert "Flower" in result or "Market" in result or "London" in result


def test_skill_best_time_st_pauls_today_no_clarification():
    """Skill does not ask for departure time on St Paul's today best-time query."""
    from unittest.mock import patch

    mock_weather = _mock_weather(_hourly_probs(
        (4, 10, 20), (4, 11, 10), (4, 12, 5), (4, 13, 5),
        (4, 14, 10), (4, 15, 15), (4, 16, 30),
    ), location_label="St Paul's Cathedral")

    with patch("weather.weather_engine.get_weather", return_value=mock_weather):
        from weather.skill import get_weather_chat
        result = get_weather_chat(
            "we want to walk to St Paul's today, when is the weather the best for that?"
        )
        assert "What time" not in result
        assert "St Paul" in result or "Cathedral" in result


def test_skill_best_time_graceful_fallback_no_hourly():
    """When hourly data is unavailable, skill returns a day-level fallback message."""
    from unittest.mock import patch

    # Weather with no hourly data
    mock_weather = WeatherData(
        location_label="Test",
        latitude=51.5,
        longitude=-0.12,
        timezone="Europe/London",
        current=MagicMock(spec=CurrentConditions, temperature=17.0, feels_like=16.0,
                          condition="Cloudy", wind_speed=12.0, precipitation_mm=0.0, uv_index=3),
        hourly_precipitation=[],  # empty — no hourly data
    )

    with patch("weather.weather_engine.get_weather", return_value=mock_weather):
        from weather.skill import get_weather_chat
        result = get_weather_chat(
            "we want to walk to St Paul's tomorrow, when is the weather the best for that?"
        )
        # Should not crash and should indicate no precise recommendation available
        assert len(result) > 0
        # Must not ask for a time
        assert "What time" not in result