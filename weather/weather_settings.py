"""Runtime settings for the weather skill."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_SKILL_DIR = Path(__file__).parent
_DEFAULTS: dict[str, Any] = {
    "week_start_day": "monday",
    "forecast_days": 14,
}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _load_settings_file() -> dict[str, Any]:
    path = Path(os.environ.get("WEATHER_SETTINGS_PATH", _SKILL_DIR / "weather_settings.local.json"))
    if not path.exists():
        path = _SKILL_DIR / "weather_settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_weather_setting(name: str, default: Any = None) -> Any:
    settings = {**_DEFAULTS, **_load_settings_file()}
    env_name = f"WEATHER_{name.upper()}"
    if env_name in os.environ:
        return os.environ[env_name]
    return settings.get(name, default)


def get_week_start_day() -> int:
    """Return configured week start as Python weekday index (Monday=0)."""
    value = str(get_weather_setting("week_start_day", "monday")).strip().lower()
    return _WEEKDAYS.get(value, 0)


def get_forecast_days() -> int:
    """Return configured forecast horizon in days, clamped to 1-14."""
    try:
        days = int(get_weather_setting("forecast_days", 14))
    except ValueError:
        days = 14
    return max(1, min(14, days))
