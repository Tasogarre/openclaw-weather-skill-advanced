from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather.geocoding import GeoResult
from weather.location_registry import add_dynamic_location, load_registry, save_registry
from weather.weather_engine import get_weather, WeatherEngineError
from weather.weather_fetcher import WeatherFetchError
from weather.weather_models import CurrentConditions, DailyForecast, WeatherData


def _registry(tmp_path):
    path = tmp_path / "location_registry.json"
    save_registry({"version": "test", "locations": {}, "commute_windows": {}}, path)
    return path


def _weather(label="Tribe Waterloo"):
    now = datetime(2026, 5, 3, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=51.5,
        longitude=-0.11,
        timezone="Europe/London",
        current=CurrentConditions(temperature=15, feels_like=15, condition="Clear"),
        today=DailyForecast(date=now.date(), high=18, low=10, condition="Clear"),
        source="test",
    )


def test_successful_unknown_weather_writes_dynamic_record(tmp_path, monkeypatch):
    path = _registry(tmp_path)
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(path))
    monkeypatch.setattr("weather.geocoding.geocode", lambda query, max_results=1: [GeoResult("Tribe Waterloo", 51.5, -0.11, "GB")])
    monkeypatch.setattr("weather.weather_engine.fetch_forecast", lambda geo: _weather())

    weather = get_weather("Tribe Waterloo")

    data = load_registry(path)
    assert "tribe_waterloo" in data["locations"]
    assert getattr(weather, "registry_confirmation") == "I’ve saved Tribe Waterloo for next time."


def test_failed_forecast_does_not_write_dynamic_record(tmp_path, monkeypatch):
    path = _registry(tmp_path)
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(path))
    monkeypatch.setattr("weather.geocoding.geocode", lambda query, max_results=1: [GeoResult("Tribe Waterloo", 51.5, -0.11, "GB")])
    monkeypatch.setattr("weather.weather_engine.fetch_forecast", lambda geo: (_ for _ in ()).throw(WeatherFetchError("boom")))

    with pytest.raises(WeatherEngineError):
        get_weather("Tribe Waterloo")

    assert load_registry(path)["locations"] == {}


def test_repeating_dynamic_alias_uses_registry_directly(tmp_path, monkeypatch):
    path = _registry(tmp_path)
    monkeypatch.setenv("WEATHER_LOCATION_REGISTRY_PATH", str(path))
    add_dynamic_location("Tribe Waterloo", GeoResult("Tribe Waterloo", 51.5, -0.11, "GB"), path=path)
    monkeypatch.setattr("weather.geocoding.geocode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("geocode should not run")))
    calls = []
    monkeypatch.setattr("weather.weather_engine.fetch_forecast", lambda geo: calls.append(geo) or _weather())

    get_weather("Tribe Waterloo")

    assert calls[0].latitude == 51.5
