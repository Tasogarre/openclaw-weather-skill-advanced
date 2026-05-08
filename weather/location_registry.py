"""
location_registry.py — Structured registry helpers for the weather skill.

The registry is the weather skill's canonical source for known places. Known
entries with coordinates resolve directly to GeoResult objects; legacy entries
without coordinates remain readable through geocode_with_fallback(). Dynamic
locations are written only through atomic saves so tests and runtime overrides
cannot partially corrupt the registry file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional

from .geocoding import GeoResult, geocode_with_fallback, GeocodingError

DEFAULT_REGISTRY_PATH = Path(__file__).parent / "location_registry.json"
EXAMPLE_REGISTRY_PATH = Path(__file__).parent / "location_registry.example.json"
REGISTRY_PATH_ENV = "WEATHER_LOCATION_REGISTRY_PATH"


@dataclass
class RegistryResolution:
    key: str
    entry: dict[str, Any]
    geo: GeoResult
    used_direct_coordinates: bool = False


def registry_path(default: Path = DEFAULT_REGISTRY_PATH) -> Path:
    """Return the selected registry path, honouring the runtime override env var."""
    override = os.environ.get(REGISTRY_PATH_ENV)
    if override:
        return Path(override).expanduser()
    return Path(default)


def load_registry(path: Optional[Path] = None) -> dict[str, Any]:
    selected = registry_path(path or DEFAULT_REGISTRY_PATH)
    if not selected.exists() and selected == DEFAULT_REGISTRY_PATH and EXAMPLE_REGISTRY_PATH.exists():
        selected = EXAMPLE_REGISTRY_PATH
    with open(selected, encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry: dict[str, Any], path: Optional[Path] = None) -> None:
    """Atomically save a registry JSON file in-place."""
    selected = registry_path(path or DEFAULT_REGISTRY_PATH)
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{selected.name}.", suffix=".tmp", dir=str(selected.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, selected)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _normalise_alias(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _valid_coordinate(value: Any, *, latitude: bool) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if latitude and -90 <= numeric <= 90:
        return numeric
    if not latitude and -180 <= numeric <= 180:
        return numeric
    return None


def geo_from_entry(entry: dict[str, Any]) -> Optional[GeoResult]:
    """Construct a GeoResult from a structured entry when coordinates are valid."""
    lookup = entry.get("lookup") if isinstance(entry.get("lookup"), dict) else {}
    lat = _valid_coordinate(lookup.get("latitude"), latitude=True)
    lon = _valid_coordinate(lookup.get("longitude"), latitude=False)
    if lat is None or lon is None:
        return None
    return GeoResult(
        name=str(entry.get("display_name") or entry.get("canonical") or lookup.get("address") or "Location"),
        latitude=lat,
        longitude=lon,
        country=str(entry.get("country") or lookup.get("country") or ""),
        admin1=str(entry.get("admin1") or lookup.get("admin1") or ""),
        timezone=str(entry.get("timezone") or lookup.get("timezone") or ""),
    )


def _legacy_geo_from_entry(entry: dict[str, Any]) -> GeoResult:
    primary = str(entry.get("primary_query") or "").strip()
    fallback = str(entry.get("fallback_query") or "").strip()
    if not primary and isinstance(entry.get("lookup"), dict):
        lookup = entry["lookup"]
        primary = str(lookup.get("postcode") or lookup.get("address") or "").strip()
        fallback = str(lookup.get("address") or "").strip()
    if not primary and not fallback:
        raise GeocodingError("Registry entry has neither coordinates nor lookup text")
    return geocode_with_fallback(primary, fallback)


def _entry_aliases(key: str, entry: dict[str, Any]) -> set[str]:
    aliases = {key, str(entry.get("canonical") or "")}
    aliases.update(str(alias) for alias in entry.get("aliases", []) if alias)
    return {_normalise_alias(alias) for alias in aliases if _normalise_alias(alias)}


def find_entry(registry: dict[str, Any], location_input: str) -> tuple[str, dict[str, Any]] | None:
    locations = registry.get("locations", {})
    query = _normalise_alias(location_input)

    if query in ("", "."):
        for key, entry in locations.items():
            if entry.get("is_default"):
                return key, entry
        return None

    for key, entry in locations.items():
        if query in _entry_aliases(key, entry):
            return key, entry
    return None


def resolve_registry_location(
    location_input: str,
    *,
    path: Optional[Path] = None,
    update_last_used: bool = True,
) -> Optional[RegistryResolution]:
    """Resolve a known registry location, or return None for unknown free text."""
    selected = registry_path(path or DEFAULT_REGISTRY_PATH)
    registry = load_registry(selected)
    found = find_entry(registry, location_input)
    if not found:
        return None

    key, entry = found
    geo = geo_from_entry(entry)
    used_direct = geo is not None
    if geo is None:
        geo = _legacy_geo_from_entry(entry)

    if update_last_used:
        locations = registry.setdefault("locations", {})
        locations[key]["last_used_at"] = _now_iso()
        save_registry(registry, selected)
        entry = locations[key]

    return RegistryResolution(key=key, entry=entry, geo=geo, used_direct_coordinates=used_direct)


def slugify_location(query: str, existing: Optional[set[str]] = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", _normalise_alias(query)).strip("_") or "location"
    existing = existing or set()
    if base not in existing:
        return base
    counter = 2
    while f"{base}_{counter}" in existing:
        counter += 1
    return f"{base}_{counter}"


def add_dynamic_location(
    query: str,
    geo: GeoResult,
    *,
    path: Optional[Path] = None,
    confidence: str = "high",
) -> Optional[str]:
    """
    Add a high-confidence dynamic location if it is not already present.

    Returns a human confirmation sentence when a new record is written, otherwise
    None. Low/medium confidence results are deliberately not auto-saved in this
    implementation slice.
    """
    if confidence != "high":
        return None
    selected = registry_path(path or DEFAULT_REGISTRY_PATH)
    registry = load_registry(selected)
    if find_entry(registry, query):
        return None

    locations = registry.setdefault("locations", {})
    key = slugify_location(query, set(locations.keys()))
    display_name = re.sub(r"\s+", " ", (query or geo.name).strip()) or geo.name
    now = _now_iso()
    locations[key] = {
        "canonical": key,
        "display_name": display_name,
        "country": geo.country,
        "timezone": geo.timezone,
        "aliases": [display_name, _normalise_alias(display_name)],
        "is_default": False,
        "lookup": {
            "type": "coordinates",
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "address": geo.name,
        },
        "source": "dynamic",
        "confidence": confidence,
        "last_used_at": now,
    }
    save_registry(registry, selected)
    return f"I’ve saved {display_name} for next time."
