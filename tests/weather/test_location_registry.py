from __future__ import annotations

import json

from weather.geocoding import GeoResult
from weather.location_registry import add_dynamic_location, load_registry, resolve_registry_location, save_registry


def _registry(tmp_path, locations):
    path = tmp_path / "location_registry.json"
    save_registry({"version": "test", "locations": locations, "commute_windows": {}}, path)
    return path


def test_known_alias_with_coordinates_resolves_without_geocoding_and_updates_last_used(tmp_path, monkeypatch):
    path = _registry(
        tmp_path,
        {
            "office": {
                "canonical": "office",
                "display_name": "The Shard",
                "country": "GB",
                "aliases": ["office", "work", "the office"],
                "lookup": {"type": "coordinates", "latitude": 51.5045, "longitude": -0.0865, "postcode": "SE1 9SG"},
            }
        },
    )
    monkeypatch.setattr("weather.location_registry.geocode_with_fallback", lambda *args: (_ for _ in ()).throw(AssertionError("geocoding should not run")))

    for alias in ("office", "work", "the office"):
        result = resolve_registry_location(alias, path=path)
        assert result is not None
        assert result.used_direct_coordinates is True
        assert result.geo.latitude == 51.5045
        assert result.geo.longitude == -0.0865
        assert result.entry["last_used_at"]
        assert result.geo.timezone == result.entry.get("timezone", "")


def test_legacy_record_uses_geocode_with_fallback(tmp_path, monkeypatch):
    path = _registry(
        tmp_path,
        {
            "old": {
                "canonical": "old",
                "display_name": "Old Style",
                "aliases": ["old"],
                "primary_query": "SE1 9SG",
                "fallback_query": "The Shard",
            }
        },
    )
    calls = []
    monkeypatch.setattr(
        "weather.location_registry.geocode_with_fallback",
        lambda primary, fallback: calls.append((primary, fallback)) or GeoResult("Old Style", 1.0, 2.0, "GB"),
    )

    result = resolve_registry_location("old", path=path)

    assert result is not None
    assert result.used_direct_coordinates is False
    assert result.geo.latitude == 1.0
    assert calls == [("SE1 9SG", "The Shard")]


def test_structured_record_missing_coordinates_falls_back_to_lookup_text(tmp_path, monkeypatch):
    path = _registry(
        tmp_path,
        {
            "partial": {
                "canonical": "partial",
                "display_name": "Partial",
                "aliases": ["partial"],
                "lookup": {"type": "coordinates", "postcode": "SE1 9SG", "address": "The Shard"},
            }
        },
    )
    calls = []
    monkeypatch.setattr(
        "weather.location_registry.geocode_with_fallback",
        lambda primary, fallback: calls.append((primary, fallback)) or GeoResult("Partial", 3.0, 4.0, "GB"),
    )

    result = resolve_registry_location("partial", path=path)

    assert result is not None
    assert result.used_direct_coordinates is False
    assert calls == [("SE1 9SG", "The Shard")]


def test_dynamic_location_write_is_atomic_and_uses_selected_path(tmp_path):
    path = _registry(tmp_path, {})

    confirmation = add_dynamic_location("Tribe Waterloo", GeoResult("Tribe Waterloo", 51.5, -0.11, "GB"), path=path)

    data = load_registry(path)
    assert "saved Tribe Waterloo for next time" in confirmation
    assert "tribe_waterloo" in data["locations"]
    assert data["locations"]["tribe_waterloo"]["lookup"]["latitude"] == 51.5
    assert not list(tmp_path.glob("*.tmp"))


def test_georesult_timezone_exists_and_registry_populates_it(tmp_path):
    path = _registry(
        tmp_path,
        {
            "home": {
                "canonical": "home",
                "display_name": "Home",
                "country": "GB",
                "timezone": "Europe/London",
                "aliases": ["home"],
                "lookup": {"type": "coordinates", "latitude": 51.5014, "longitude": -0.1419},
            }
        },
    )

    result = resolve_registry_location("home", path=path)

    assert result is not None
    assert hasattr(result.geo, "timezone")
    assert result.geo.timezone == "Europe/London"


def test_dynamic_location_preserves_geo_timezone(tmp_path):
    path = _registry(tmp_path, {})

    add_dynamic_location("Tribe Waterloo", GeoResult("Tribe Waterloo", 51.5, -0.11, "GB", timezone="Europe/London"), path=path)

    data = load_registry(path)
    assert data["locations"]["tribe_waterloo"]["timezone"] == "Europe/London"
