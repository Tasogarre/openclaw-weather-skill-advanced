from __future__ import annotations

import json

from weather.geocoding import GeoResult
from weather.location_registry import save_registry
from weather.weather_engine import get_weather, resolve_location
from weather.weather_models import CurrentConditions, DailyForecast, WeatherData


def _registry(tmp_path):
    path = tmp_path / "location_registry.json"
    save_registry(
        {
            "version": "test",
            "locations": {
                "home": {
                    "canonical": "home",
                    "display_name": "Home",
                    "is_default": True,
                    "aliases": ["home"],
                    "lookup": {"type": "coordinates", "latitude": 51.5, "longitude": -0.1},
                },
                "office": {
                    "canonical": "office",
                    "display_name": "The Shard",
                    "aliases": ["office", "work", "the office"],
                    "lookup": {"type": "coordinates", "latitude": 51.5045, "longitude": -0.0865, "postcode": "SE1 9SG"},
                },
            },
            "commute_windows": {},
        },
        path,
    )
    return path


def _weather(label="Test"):
    from datetime import datetime, timezone

    now = datetime(2026, 5, 3, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=1,
        longitude=2,
        timezone="Europe/London",
        current=CurrentConditions(temperature=15, feels_like=15, condition="Clear"),
        today=DailyForecast(date=now.date(), high=18, low=10, condition="Clear"),
        source="test",
    )


def test_get_weather_office_uses_registry_coordinates_without_geocoding(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(_registry(tmp_path)))
    monkeypatch.setattr("weather.geocoding.geocode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("geocode should not run")))
    calls = []
    monkeypatch.setattr("weather.weather_engine.fetch_forecast", lambda geo: calls.append(geo) or _weather("Office"))

    weather = get_weather("office")

    assert weather.location_label == "Office"
    assert calls[0].latitude == 51.5045
    assert calls[0].longitude == -0.0865


def test_unknown_free_text_calls_geocode_once(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(_registry(tmp_path)))
    calls = []
    monkeypatch.setattr("weather.geocoding.geocode", lambda query, max_results=1: calls.append(query) or [GeoResult("Mayfair", 51.51, -0.14, "GB")])

    entry, geo = resolve_location("Mayfair")

    assert entry is None
    assert geo.name == "Mayfair"
    assert calls == ["Mayfair"]


def test_default_empty_query_resolves_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(_registry(tmp_path)))
    monkeypatch.setattr("weather.geocoding.geocode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("geocode should not run")))

    entry, geo = resolve_location("")

    assert entry["canonical"] == "home"
    assert geo.latitude == 51.5
