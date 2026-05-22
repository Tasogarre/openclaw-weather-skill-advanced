---
title: Advanced Weather Skill — Week and Multi-Day Forecast Ranges
status: active
created: 2026-05-19
origin: Discord thread #enhance-advanced-weather-skill
skill: ce-plan
---

# Advanced Weather Skill — Week and Multi-Day Forecast Ranges

## 1. Problem frame

Marcus wants the advanced weather skill to answer week-range forecast questions naturally and predictably. The skill should distinguish calendar-week wording from rolling-day wording:

- “What does the weather look like this week?” means **today through the last day of the current configured week**.
- “What does the weather look like for the rest of the week?” means the same thing: **today through the last day of the current configured week**.
- “What does the weather look like for the next week?” means **the next 7 days**.
- “What does the weather look like for the next 10 days?” means **the next 10 days**.
- “What does the weather look like for the next 2 weeks?” means **the next 14 days**.

The start-of-week setting defines the week boundary. Monday is the default start of week; therefore Sunday is the default last day. If the configured start day changes, the calculated last day changes accordingly.

## 2. Source requirements and traceability

User-request traceability:

| Requirement | Source wording | Planned handling |
|---|---|---|
| R1 | “full week forecast” | Add explicit forecast-range support for week/rest-of-week and rolling multi-day horizons. |
| R2 | “including rest of the week” | Treat `this week` and `rest of week` as today → configured week end. |
| R3 | “Monday should be flagged as the start of the week” | Keep Monday as default `week_start_day`. Use settings/env override for configurability. |
| R4 | “make this configurable in the skill or plugin settings” | Use `weather_settings.py` + optional settings file + env override. Document it. |
| R5 | “next 7 days and next 10 and next 14 days” | Support `next_7_days`, `next_10_days`, and `next_14_days` range modes. |
| R6 | “this week” vs “next week” | `this week` = today → week end. `next week` / `next 7 days` = rolling 7-day horizon. |
| R7 | “next 2 weeks” | Parse as rolling 14-day horizon. |

Clarification captured after initial draft:

- `this week` and `rest of week` are equivalent. They both mean today → the last day of the current configured week, not a full Monday-Sunday calendar block that may include past days.

## 3. Current implementation findings

Relevant existing code:

- `weather/skill.py`
  - `_detect_forecast_range_mode()` already detects `next_7_days`, `next_10_days`, and `rest_of_week` for some phrases.
  - `_handle_forecast_range_query()` already routes range modes to `format_forecast_range()`.
- `weather/formatters/chat_formatter.py`
  - `format_forecast_range()` already formats range forecast output.
  - `_rest_of_week_days()` already uses `get_week_start_day()` to calculate the configured week boundary.
  - Existing modes: `rest_of_week`, `next_7_days`, `next_10_days`.
- `weather/weather_settings.py`
  - Already has default `week_start_day: monday`.
  - Already supports env override via `WEATHER_WEEK_START_DAY`.
  - Already supports optional local/settings-file loading through `WEATHER_SETTINGS_PATH`, `weather_settings.local.json`, or `weather_settings.json`.
- `weather/weather_fetcher.py`
  - Currently uses `DEFAULT_FORECAST_DAYS = 10` and `MAX_FORECAST_DAYS = 10`.
  - Open-Meteo URL builder sends `forecast_days` from `_forecast_days()`.
  - `_forecast_days()` currently reads only `WEATHER_FORECAST_DAYS`, not `weather_settings.py`.

Main gaps:

1. No `next_14_days` mode.
2. No parsing for “next 2 weeks” / “next two weeks”.
3. Fetch horizon is clamped to 10 days, which prevents true 14-day output from Open-Meteo.
4. `forecast_days` setting exists in `weather_settings.py` defaults but is not wired into `weather_fetcher.py`.
5. Documentation does not yet describe week boundary settings or 14-day forecasts.

## 4. Scope

### In scope

1. Add deterministic phrase detection for:
   - `this week`
   - `rest of the week`
   - `next week`
   - `next 7 days`
   - `next 10 days`
   - `next 14 days`
   - `next 2 weeks`
   - `next two weeks`
2. Preserve `this week` and `rest of week` as equivalent: today → configured week end.
3. Add `next_14_days` formatting support.
4. Increase Open-Meteo forecast horizon support to 14 days.
5. Wire default/configured `forecast_days` setting through `weather_settings.py` into `weather_fetcher.py`.
6. Keep Monday as the default configured week start.
7. Document week boundary and forecast horizon configuration.
8. Add tests for range detection, range formatting, settings, and 14-day fetch horizon.

### Out of scope

- Calendar-week output that includes past days.
- Historical weather.
- Route-aware multi-day travel forecasts.
- UI/plugin settings panel beyond file/env configuration already supported by the skill.
- Provider replacement or paid-provider evaluation.
- Changing existing commute/travel/best-time behavior except where classifier ordering must avoid accidental interception.

## 5. Key technical decisions

### D1 — Keep `this week` and `rest of week` as one mode

Use a single internal mode, `rest_of_week`, for both `this week` and `rest of week`. The mode means “from the first available forecast day, normally today, until the day before the next configured week-start day”.

Rationale: this exactly matches Marcus’s clarification and keeps implementation simple. The label can remain “Rest of week forecast” or be mildly generalised later, but the behavior is the source of truth.

### D2 — Rolling horizons use explicit day-count modes

Use day-count modes for rolling ranges:

- `next_7_days`
- `next_10_days`
- `next_14_days`

Rationale: this avoids ambiguity between “this week” and “next week”. `next week` should route to `next_7_days` per Marcus’s examples, not a next calendar week block.

### D3 — Provider horizon should be configured, but formatter should degrade gracefully

Set default forecast horizon to 14 days and clamp to provider-supported bounds. If a provider returns fewer days, the formatter should show what it has and include the existing provider-returned-days note.

Rationale: the skill should support 14-day output when the provider can supply it, without failing when OpenWeatherMap or a fallback path returns fewer days.

### D4 — Keep configuration in the existing weather settings layer

`weather_settings.py` remains the source for skill/plugin-style settings. `WEATHER_FORECAST_DAYS` and `WEATHER_WEEK_START_DAY` remain env override names.

Rationale: the settings abstraction already exists and avoids adding a second configuration path.

### D5 — Deterministic detection should run before generic single-location formatting

Range phrases should be detected early in `get_weather_chat()` before best-time, commute, travel, and single-location handling unless a more specific travel/commute query clearly applies.

Rationale: “what does weather look like this week?” is a range forecast intent, not a normal current-weather summary.

## 6. Implementation units

### U1 — Forecast range intent detection

Files:

- `weather/skill.py`
- `tests/weather/test_advanced_weather_skill.py`

Work:

- Extend `_detect_forecast_range_mode(query)` to detect:
  - `next 14 days`, `14 day`, `14-day`, `fourteen day`, `fourteen-day`
  - `next 2 weeks`, `next two weeks`, `coming 2 weeks`, `coming two weeks`
- Keep `next week`, `week ahead`, `next 7 days`, `7 day`, and `seven day` mapped to `next_7_days`.
- Keep `this week`, `rest of the week`, and `rest of week` mapped to `rest_of_week`.
- Put 14-day checks before 7-day checks so `next 2 weeks` cannot be swallowed by generic `week` matching.

Test scenarios:

- `get_weather_chat("What does the weather look like this week?")` routes to `rest_of_week`.
- `get_weather_chat("What does the weather look like for the rest of the week?")` routes to `rest_of_week`.
- `get_weather_chat("What does the weather look like for the next week?")` routes to `next_7_days`.
- `get_weather_chat("What does the weather look like for the next 2 weeks?")` routes to `next_14_days`.
- `get_weather_chat("next fourteen days forecast")` routes to `next_14_days`.

### U2 — Forecast formatter 14-day support

Files:

- `weather/formatters/chat_formatter.py`
- `tests/weather/test_advanced_weather_skill.py`

Work:

- Extend `format_forecast_range()` to support `mode="next_14_days"`.
- Add title `Next 14 days forecast`.
- Select `days[:14]`.
- Generalise the “Provider returned N daily forecasts” calculation so it works for 7, 10, and 14 day modes.
- Preserve current wettest/warmest summary behavior.

Test scenarios:

- A 14-day dummy forecast renders exactly 14 daily `High` lines.
- The title is `📅 **Next 14 days forecast**`.
- Day 14 is included and day 15 is excluded.
- If only 10 days are supplied for `next_14_days`, output includes a provider-returned-days note.

### U3 — Week boundary settings behavior

Files:

- `weather/formatters/chat_formatter.py`
- `weather/weather_settings.py`
- `tests/weather/test_advanced_weather_skill.py`

Work:

- Preserve `get_week_start_day()` default of Monday (`0`).
- Add/confirm test coverage that Tuesday + Monday week start yields Tuesday→Sunday (6 days).
- Add test coverage that a Sunday week start changes the week end correctly.
- Document accepted weekday names and env var override.

Test scenarios:

- Start date Tuesday 2026-05-19, `week_start_day=0` selects Tue 19 → Sun 24.
- Start date Tuesday 2026-05-19, `week_start_day=6` selects Tue 19 → Sat 23.
- Invalid `WEATHER_WEEK_START_DAY` falls back to Monday.

### U4 — Forecast horizon configuration and provider fetch

Files:

- `weather/weather_fetcher.py`
- `weather/weather_settings.py`
- tests under `tests/weather/`

Work:

- Change `MAX_FORECAST_DAYS` from 10 to 14.
- Change default forecast days from 10 to 14, either by:
  - importing `get_weather_setting("forecast_days", 14)` from `weather_settings.py`, or
  - adding a small local helper that honours the settings layer without creating an import cycle.
- Preserve `WEATHER_FORECAST_DAYS` env var override.
- Clamp invalid values to safe bounds: min 1, max 14.
- Ensure `_om_build_forecast_url()` emits `forecast_days=14` by default.
- Accept that OpenWeatherMap One Call may return fewer daily rows; parser already slices only what exists.

Test scenarios:

- `_forecast_days()` returns 14 by default.
- `WEATHER_FORECAST_DAYS=10` returns 10.
- `WEATHER_FORECAST_DAYS=30` clamps to 14.
- `WEATHER_FORECAST_DAYS=bad` falls back to default.
- `_om_build_forecast_url(..., forecast_days=14)` includes `forecast_days=14`.

### U5 — Documentation update

Files:

- `weather/SKILL.md`
- `weather/README.md`
- optionally `docs/runbooks/weather-request-routing-runbook.md`

Work:

- Document supported range phrases and behavior:
  - `this week` / `rest of week` = today → configured week end.
  - `next week` / `next 7 days` = rolling 7 days.
  - `next 10 days` = rolling 10 days.
  - `next 2 weeks` / `next 14 days` = rolling 14 days.
- Document settings:
  - `week_start_day`, default `monday`.
  - env override `WEATHER_WEEK_START_DAY`.
  - `forecast_days`, default 14.
  - env override `WEATHER_FORECAST_DAYS`.
- Add example outputs or concise examples to the skill documentation.

Test/verification scenarios:

- Grep docs for old “forecast_days default 10” wording and correct if found.
- Ensure docs do not imply `this week` includes past days.

## 7. Testing plan

Run targeted tests after implementation:

```bash
python3 -m pytest tests/weather/test_advanced_weather_skill.py tests/weather/test_weather_time_windows.py
```

Run syntax validation:

```bash
python3 -m compileall weather
```

Optional smoke checks with mocked/live provider as available:

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/YOUR/WORKSPACE/PATH")
from weather.skill import get_weather_chat
print(get_weather_chat("What does the weather look like this week?"))
print(get_weather_chat("What does the weather look like for the next 2 weeks?"))
PY
```

Live smoke output is useful but should not be the only verification because provider availability can vary.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Provider returns fewer than 14 days | Formatter already handles shorter selected ranges; add explicit test for provider-returned-days note. |
| OpenWeatherMap One Call does not supply 14 daily rows | Accept graceful degradation; Open-Meteo is the zero-config path and supports requested `forecast_days`. |
| “next week” ambiguity | Follow Marcus’s explicit rule: “next week” = next 7 days. Document it. |
| Settings path drift | Reuse existing `weather_settings.py`; do not add a new config mechanism. |
| Range detection collides with travel/commute queries | Keep phrase checks targeted. Existing commute/travel tests must still pass. |

## 9. Acceptance criteria

Implementation is complete when:

1. `this week` and `rest of week` return today → configured week end.
2. Default configured week start is Monday.
3. Changing week start changes the selected week end in tests.
4. `next week` and `next 7 days` return 7 daily forecasts when available.
5. `next 10 days` returns 10 daily forecasts when available.
6. `next 2 weeks` and `next 14 days` return 14 daily forecasts when available.
7. Open-Meteo fetch URL defaults to `forecast_days=14` unless configured otherwise.
8. Env/settings override for forecast horizon is honoured and clamped.
9. Existing commute, travel, best-time, and normal weather tests still pass.
10. Weather docs describe the new range behavior and configuration.

## 10. Recommended execution order

1. Implement U1 and U2 together: detection + formatting.
2. Implement U4: fetch horizon default/configuration.
3. Add/adjust tests across U1–U4.
4. Update documentation in U5.
5. Run targeted pytest + compileall.
6. Run one optional smoke invocation if provider access is available.
