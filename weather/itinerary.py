"""
itinerary.py — Private temporary-location itinerary support for weather.

This module stores temporally constrained travel/current-location context in a
private JSON file. It is deliberately separate from the public location registry:
real travel plans are private runtime data and should never be committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_ITINERARY_PATH = Path.home() / ".config" / "openclaw" / "weather" / "itinerary.json"
LEGACY_WORKSPACE_ITINERARY_PATH = Path(__file__).parent / "itinerary.json"
EXAMPLE_ITINERARY_PATH = Path(__file__).parent / "itinerary.example.json"
ITINERARY_PATH_ENV = "WEATHER_ITINERARY_PATH"


@dataclass(frozen=True)
class ItineraryEntry:
    """A temporary location active for an inclusive date range."""

    label: str
    location: str
    start: date
    end: date
    timezone: Optional[str] = None
    priority: int = 0
    updated_at: Optional[str] = None
    source: str = "user"


@dataclass(frozen=True)
class ItineraryResolution:
    """Result of resolving a date against the itinerary."""

    entry: Optional[ItineraryEntry]
    conflict_entries: list[ItineraryEntry] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflict_entries)


def itinerary_path(default: Path = DEFAULT_ITINERARY_PATH) -> Path:
    """Return the selected itinerary path, honouring runtime env override."""
    override = os.environ.get(ITINERARY_PATH_ENV)
    if override:
        return Path(override).expanduser()
    return Path(default)


def _is_protected_path(path: Path) -> bool:
    """Return True if the path points to the committed example itinerary."""
    try:
        return path.resolve() == EXAMPLE_ITINERARY_PATH.resolve()
    except OSError:
        return False


def load_itinerary(path: Optional[Path] = None) -> dict[str, Any]:
    """Load itinerary JSON, returning an empty itinerary if no private file exists."""
    selected = itinerary_path(path or DEFAULT_ITINERARY_PATH)
    if not selected.exists():
        return {"version": "1.0.0", "entries": []}
    with open(selected, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"version": "1.0.0", "entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        data["entries"] = []
    return data


def save_itinerary(itinerary: dict[str, Any], path: Optional[Path] = None) -> None:
    """Save a private itinerary JSON file.

    The committed example file is protected from writes to avoid accidentally
    committing real travel context.
    """
    selected = itinerary_path(path or DEFAULT_ITINERARY_PATH)
    if _is_protected_path(selected):
        raise RuntimeError(
            f"Cannot write to protected example itinerary: {selected}. "
            f"Set {ITINERARY_PATH_ENV} to a private local file."
        )
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(itinerary, indent=2, ensure_ascii=False) + "\n"
    selected.write_text(payload, encoding="utf-8")


def _coerce_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).date()
    except ValueError:
        return None


def _coerce_entry(raw: Any) -> Optional[ItineraryEntry]:
    if not isinstance(raw, dict):
        return None
    location = str(raw.get("location") or raw.get("location_name") or "").strip()
    label = str(raw.get("label") or location).strip()
    start = _coerce_date(raw.get("start"))
    end = _coerce_date(raw.get("end"))
    if not location or not label or start is None or end is None:
        return None
    if end < start:
        return None
    try:
        priority = int(raw.get("priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0
    timezone = raw.get("timezone")
    updated_at = raw.get("updated_at")
    source = str(raw.get("source") or "user")
    return ItineraryEntry(
        label=label,
        location=location,
        start=start,
        end=end,
        timezone=str(timezone) if timezone else None,
        priority=priority,
        updated_at=str(updated_at) if updated_at else None,
        source=source,
    )


def list_entries(path: Optional[Path] = None) -> list[ItineraryEntry]:
    """Return valid itinerary entries from the selected itinerary file."""
    data = load_itinerary(path)
    entries: list[ItineraryEntry] = []
    for raw in data.get("entries", []):
        entry = _coerce_entry(raw)
        if entry:
            entries.append(entry)
    return entries


def active_entries_for_date(target_date: date, path: Optional[Path] = None) -> list[ItineraryEntry]:
    """Return entries active on target_date. Start/end dates are inclusive."""
    return [entry for entry in list_entries(path) if entry.start <= target_date <= entry.end]


def _sort_key(entry: ItineraryEntry) -> tuple[int, str, str]:
    # Higher priority and later updated_at win; label provides deterministic order.
    return (entry.priority, entry.updated_at or "", entry.label)


def resolve_itinerary_location(target_date: date, path: Optional[Path] = None) -> Optional[ItineraryResolution]:
    """Resolve target_date to a single temporary location or a conflict.

    If multiple entries match, priority desc and updated_at desc disambiguate.
    If two or more top candidates are still tied, return a conflict so the
    caller can ask a clarifying question instead of guessing.
    """
    matches = active_entries_for_date(target_date, path)
    if not matches:
        return None
    if len(matches) == 1:
        return ItineraryResolution(entry=matches[0])

    ranked = sorted(matches, key=_sort_key, reverse=True)
    top = ranked[0]
    tied = [entry for entry in ranked if entry.priority == top.priority and (entry.updated_at or "") == (top.updated_at or "")]
    if len(tied) > 1:
        return ItineraryResolution(entry=None, conflict_entries=sorted(tied, key=lambda e: e.label))
    return ItineraryResolution(entry=top)


def active_entry_for_date(target_date: date, path: Optional[Path] = None) -> Optional[ItineraryEntry]:
    """Return the active itinerary entry for target_date, or None for none/conflict."""
    resolution = resolve_itinerary_location(target_date, path)
    if not resolution or resolution.has_conflict:
        return None
    return resolution.entry


def entry_to_dict(entry: ItineraryEntry) -> dict[str, Any]:
    """Serialize an itinerary entry to JSON-compatible form."""
    return {
        "label": entry.label,
        "location": entry.location,
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat(),
        "timezone": entry.timezone,
        "priority": entry.priority,
        "updated_at": entry.updated_at,
        "source": entry.source,
    }


def quick_add_itinerary(
    location: str,
    start: str | date,
    end: str | date,
    label: Optional[str] = None,
    *,
    priority: int = 0,
    timezone_name: Optional[str] = None,
    path: Optional[Path] = None,
) -> ItineraryEntry:
    """Append a user itinerary entry and save it to the private itinerary file."""
    start_date = _coerce_date(start)
    end_date = _coerce_date(end)
    if start_date is None or end_date is None:
        raise ValueError("Itinerary start/end must be ISO dates or date objects")
    if end_date < start_date:
        raise ValueError("Itinerary end date cannot be before start date")
    clean_location = str(location or "").strip()
    if not clean_location:
        raise ValueError("Itinerary location is required")
    entry = ItineraryEntry(
        label=str(label or clean_location).strip(),
        location=clean_location,
        start=start_date,
        end=end_date,
        timezone=timezone_name,
        priority=int(priority or 0),
        updated_at=datetime.now(timezone.utc).isoformat(),
        source="user",
    )
    data = load_itinerary(path)
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(entry_to_dict(entry))
    data["entries"] = entries
    data.setdefault("version", "1.0.0")
    save_itinerary(data, path)
    return entry


def remove_itinerary_entries(target: str, path: Optional[Path] = None) -> list[ItineraryEntry]:
    """Remove entries whose label/location contains target (case-insensitive)."""
    needle = str(target or "").strip().lower()
    if not needle:
        return []
    data = load_itinerary(path)
    kept: list[dict[str, Any]] = []
    removed: list[ItineraryEntry] = []
    for raw in data.get("entries", []):
        entry = _coerce_entry(raw)
        haystack = f"{getattr(entry, 'label', '')} {getattr(entry, 'location', '')}".lower() if entry else ""
        if entry and needle in haystack:
            removed.append(entry)
        else:
            kept.append(raw)
    if removed:
        data["entries"] = kept
        save_itinerary(data, path)
    return removed
