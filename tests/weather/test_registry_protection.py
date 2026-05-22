"""
test_registry_protection.py — Tests for public/sample vs local/private location handling.

Covers:
- Example registry entries are marked as example source
- Runtime writes are blocked to the example registry path
- Dynamic locations can still be written to local registry
- Gartner Salisbury Square remains in local registry, The Shard in example
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from weather.geocoding import GeoResult
from weather.location_registry import (
    EXAMPLE_REGISTRY_PATH,
    DEFAULT_REGISTRY_PATH,
    add_dynamic_location,
    load_registry,
    save_registry,
    _is_example_source,
    _is_protected_path,
)


def test_example_entries_marked_as_example():
    """The committed example registry should have example/sample source values."""
    registry = load_registry(EXAMPLE_REGISTRY_PATH)
    for key, entry in registry.get("locations", {}).items():
        assert _is_example_source(entry), f"Entry {key} should be marked as example source"


def test_local_registry_has_private_office():
    """Local registry contains the real office (Gartner Salisbury Square)."""
    if not DEFAULT_REGISTRY_PATH.exists():
        pytest.skip("Local registry not present")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    locations = registry.get("locations", {})
    # The local office should NOT be marked as example
    office = locations.get("office")
    if office:
        assert not _is_example_source(office), "Local office should not be example source"
        assert office.get("display_name") == "Gartner Salisbury Square"
        lookup = office.get("lookup", {})
        assert lookup.get("postcode") == "EC4Y 8BB"
        assert lookup.get("address") == "Gartner Salisbury Square, London EC4Y 8BB"


def test_cannot_write_to_example_registry():
    """save_registry() must raise RuntimeError when targeting the example file."""
    with pytest.raises(RuntimeError) as exc_info:
        save_registry({"version": "test"}, path=EXAMPLE_REGISTRY_PATH)
    assert "protected example registry" in str(exc_info.value).lower()


def test_cannot_add_dynamic_to_example_registry():
    """add_dynamic_location() must raise RuntimeError when targeting the example file."""
    with pytest.raises(RuntimeError) as exc_info:
        add_dynamic_location(
            "Test Place",
            GeoResult("Test Place", 51.5, -0.1, "GB"),
            path=EXAMPLE_REGISTRY_PATH,
        )
    assert "protected example registry" in str(exc_info.value).lower()


def test_can_write_to_local_registry(tmp_path):
    """Dynamic writes to a non-example path should succeed."""
    path = tmp_path / "location_registry.json"
    save_registry({"version": "test", "locations": {}, "commute_windows": {}}, path=path)
    result = add_dynamic_location(
        "Test Place",
        GeoResult("Test Place", 51.5, -0.1, "GB"),
        path=path,
    )
    assert "saved" in result.lower()
    data = load_registry(path)
    assert "test_place" in data["locations"]


def test_is_protected_path_detects_example():
    assert _is_protected_path(EXAMPLE_REGISTRY_PATH) is True
    assert _is_protected_path(Path("/tmp/location_registry.json")) is False
