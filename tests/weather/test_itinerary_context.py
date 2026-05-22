from __future__ import annotations

import json
from datetime import date, datetime, timezone

from weather.evaluation_context import build_evaluation_context
from weather.intent_classifier import WeatherIntent
from weather.itinerary import active_entries_for_date, active_entry_for_date, resolve_itinerary_location
from weather.skill import get_weather_chat, get_weather_briefing_for_today
from weather.weather_models import CurrentConditions, DailyForecast, WeatherData


def _write_itinerary(path, entries):
    path.write_text(json.dumps({"version": "1.0.0", "entries": entries}), encoding="utf-8")


def _dummy_weather(label: str) -> WeatherData:
    now = datetime(2026, 5, 30, 9, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=0,
        longitude=0,
        timezone="UTC",
        current=CurrentConditions(
            temperature=15,
            feels_like=15,
            condition="Clear sky",
            humidity=50,
            wind_speed=5,
            wind_gust=None,
            precipitation_mm=0,
            uv_index=3,
        ),
        today=DailyForecast(date=now.date(), high=18, low=10, condition="Clear sky", precip_probability=0),
        hourly_precipitation=[],
        alerts=[],
        source="test",
    )


def test_active_itinerary_dates_are_inclusive(tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [{"label": "santiago", "location": "Santiago, Chile", "start": "2026-05-29", "end": "2026-06-01"}])

    assert active_entries_for_date(date(2026, 5, 29), path)[0].location == "Santiago, Chile"
    assert active_entries_for_date(date(2026, 6, 1), path)[0].location == "Santiago, Chile"
    assert active_entries_for_date(date(2026, 6, 2), path) == []


def test_active_entry_returns_none_when_itinerary_missing(tmp_path):
    assert active_entry_for_date(date(2026, 5, 30), tmp_path / "missing.json") is None


def test_itinerary_priority_breaks_overlaps(tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [
        {"label": "cusco", "location": "Cusco, Peru", "start": "2026-06-02", "end": "2026-06-05", "priority": 10},
        {"label": "lima", "location": "Lima, Peru", "start": "2026-06-05", "end": "2026-06-08", "priority": 20},
    ])

    resolved = resolve_itinerary_location(date(2026, 6, 5), path)
    assert resolved is not None
    assert resolved.entry is not None
    assert resolved.entry.location == "Lima, Peru"


def test_itinerary_tied_overlap_reports_conflict(tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [
        {"label": "cusco", "location": "Cusco, Peru", "start": "2026-06-05", "end": "2026-06-05"},
        {"label": "lima", "location": "Lima, Peru", "start": "2026-06-05", "end": "2026-06-05"},
    ])

    resolved = resolve_itinerary_location(date(2026, 6, 5), path)
    assert resolved is not None
    assert resolved.has_conflict is True
    assert resolved.entry is None


def test_evaluation_context_uses_active_itinerary(monkeypatch, tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [{"label": "santiago", "location": "Santiago, Chile", "start": "2026-05-29", "end": "2026-06-01", "timezone": "America/Santiago"}])
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(path))

    intent = WeatherIntent(location="home", time_reference="today", intent_type="general", raw_query="what's the weather today?")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 30, 9, tzinfo=timezone.utc))

    assert ctx.primary_location == "Santiago, Chile"
    assert ctx.location_source == "itinerary"
    assert ctx.itinerary_label == "santiago"
    assert ctx.itinerary_timezone == "America/Santiago"


def test_explicit_location_overrides_active_itinerary(monkeypatch, tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [{"label": "santiago", "location": "Santiago, Chile", "start": "2026-05-29", "end": "2026-06-01"}])
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(path))

    intent = WeatherIntent(location="London", time_reference="today", intent_type="general", raw_query="weather in London today")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 30, 9, tzinfo=timezone.utc))

    assert ctx.primary_location == "London"
    assert ctx.location_source == "explicit"


def test_evaluation_context_falls_back_home_without_itinerary(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(tmp_path / "missing.json"))

    intent = WeatherIntent(location="home", time_reference="today", intent_type="general", raw_query="what's the weather today?")
    ctx = build_evaluation_context(intent, now=datetime(2026, 5, 30, 9, tzinfo=timezone.utc))

    assert ctx.primary_location == "home"
    assert ctx.location_source == "default"


def test_weather_chat_routes_plain_query_to_itinerary_location(monkeypatch, tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [{"label": "cusco", "location": "Cusco, Peru", "start": "2026-06-02", "end": "2026-06-05"}])
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(path))
    monkeypatch.setattr("weather.skill.classify_intent", lambda _: WeatherIntent(location="home", time_reference="today", intent_type="general", raw_query="weather today"))
    monkeypatch.setattr("weather.evaluation_context.datetime", type("FrozenDateTime", (), {"now": staticmethod(lambda tz=None: datetime(2026, 6, 3, 9, tzinfo=timezone.utc)), "fromisoformat": datetime.fromisoformat}))
    calls = []
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: calls.append(loc) or _dummy_weather(loc))
    monkeypatch.setattr("weather.skill.resolve_location", lambda loc: (None, type("Geo", (), {"name": loc})()))

    result = get_weather_chat("what's the weather today?")

    assert calls == ["Cusco, Peru"]
    assert "Using travel context: Cusco, Peru" in result


def test_morning_briefing_uses_itinerary_location(monkeypatch, tmp_path):
    path = tmp_path / "itinerary.json"
    _write_itinerary(path, [{"label": "santiago", "location": "Santiago, Chile", "start": "2026-05-29", "end": "2026-06-01"}])
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(path))
    calls = []
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: calls.append(loc) or _dummy_weather(loc))

    result = get_weather_briefing_for_today(now=datetime(2026, 5, 30, 9, tzinfo=timezone.utc))

    assert calls == ["Santiago, Chile"]
    assert "Using travel context: Santiago, Chile" in result
