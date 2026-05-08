from __future__ import annotations

from datetime import datetime, timezone

from weather.intent_classifier import _deterministic_classify, build_travel_window
from weather.weather_engine import rain_in_window
from weather.weather_models import HourlyPrecipitation, WeatherData


def _weather_with_hours(*hours):
    return WeatherData(
        location_label="Test",
        latitude=0,
        longitude=0,
        timezone="UTC",
        hourly_precipitation=[HourlyPrecipitation(time=dt, probability=prob) for dt, prob in hours],
    )


def test_at_9am_produces_one_hour_window_on_requested_date():
    start, end, label = build_travel_window("tomorrow", "09:00", now=datetime(2026, 5, 3, 12, tzinfo=timezone.utc))

    assert start == "2026-05-04T09:00:00+00:00"
    assert end == "2026-05-04T10:00:00+00:00"
    assert label == "09:00–10:00"


def test_compact_530_in_travel_context_produces_evening_window():
    intent = _deterministic_classify("I'm headed to Tribe Waterloo from work at 530")

    assert intent.departure_time == "17:30"
    assert intent.travel_window_label == "17:30–18:30"


def test_date_aware_rain_window_uses_requested_date_not_first_matching_time():
    today_9 = datetime(2026, 5, 3, 9, tzinfo=timezone.utc)
    tomorrow_9 = datetime(2026, 5, 4, 9, tzinfo=timezone.utc)
    weather = _weather_with_hours((today_9, 90), (tomorrow_9, 10))

    assert rain_in_window(
        weather,
        "09:00",
        "10:00",
        probability_threshold=30,
        window_start_dt=tomorrow_9,
        window_end_dt=datetime(2026, 5, 4, 10, tzinfo=timezone.utc),
    ) is False


def test_vague_travel_time_clarifies_instead_of_selecting_morning():
    intent = _deterministic_classify("I've gotta go to Paddington tomorrow morning")

    assert intent.needs_time_clarify is True
    assert intent.travel_window_start is None
