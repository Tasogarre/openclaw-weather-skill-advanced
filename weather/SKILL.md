---
name: weather
version: 1.3.0
description: Rich weather forecasting for named locations with registry-first location resolution, travel-weather queries, commute-aware advice, intent classification, rain timing, tonight-low logic, chat output, and morning briefing output. Works zero-config with Open-Meteo and enhances with OpenWeatherMap when configured.
kind: functional
allowed-tools: Bash, web_fetch
---

# weather — Rich Weather Skill v1.3.0

## Mandatory Assistant Routing

Any weather-related user request — including forecasts, rain, umbrella decisions, clothing decisions driven by weather, commute weather, travel weather, or best-time-for-weather planning — must route through this skill before the assistant answers. Do not answer such requests generically or ask broad location questions before invoking the skill path.

If the OmniRoute skill MCP is unavailable, invoke the local Phase 2 entry point directly via Python from the workspace root:

```python
from skills.weather.skill import get_weather_chat
print(get_weather_chat("<USER WEATHER QUERY>"))
```

Operational runbook: `docs/runbooks/weather-request-routing-runbook.md`.

## Overview

The weather skill provides multi-location weather forecasting with actionable advice for chat and morning briefing surfaces.

- **Zero-config path:** Open-Meteo forecast and geocoding; no API key required.
- **Enhanced path:** OpenWeatherMap One Call 3.0 when `OPENWEATHERMAP_API_KEY` is set; enables UK postcode geocoding and weather alerts.
- **Phase 1:** location registry, geocoding, dual-provider forecast fetch, structured `WeatherData`, commute windows, rain-in-window helper.
- **Phase 2:** intent classifier, advice engine, chat formatter, briefing formatter, dual-location commute advice, rain timing, tonight's low, `skill.py` entry point, and morning briefing integration.

The existing Phase 1 API in `weather_engine.py` remains available. Phase 2 adds the user-facing `skills.weather.skill` interface.

---

## Architecture

```text
User query
  -> classify_intent()
     - GPT 5.5-mini when OPENAI_API_KEY is set
     - Claude Haiku when ANTHROPIC_API_KEY is set
     - Ollama via WEATHER_INTENT_MODEL, default llama3.2:3b
     - deterministic keyword fallback, always available
  -> resolve location
     - registry aliases: home, office, atlanta, curitiba, rio, saopaulo
     - free-text geocoding fallback
     - commute query resolves both home and office
  -> fetch forecast
     - OpenWeatherMap when configured
     - Open-Meteo fallback / zero-config default
  -> WeatherData
  -> advice_for_location() or commute_umbrella_check()
  -> formatter
     - format_chat() for interactive chat
     - format_briefing() for morning briefing
  -> output string
```

Morning briefing integration in `bin/morning_briefing.py` calls `get_weather_briefing(location="home")` first and retains the previous wttr.in path as a fallback.

---

## Provider Selection

| Capability | Open-Meteo, no key | OpenWeatherMap, `OPENWEATHERMAP_API_KEY` |
|---|---:|---:|
| Forecast data | yes | yes |
| Zero-config operation | yes | no, key required |
| UK postcode geocoding | limited/unreliable | yes, via `/zip` |
| Weather alerts | no | yes |
| Hourly precipitation for rain timing | yes | yes |
| Advice engine | yes | yes |
| Chat and briefing formatters | yes | yes |

Provider behaviour:
1. If an OpenWeatherMap key is available, the skill tries OpenWeatherMap first.
2. If OpenWeatherMap is unavailable or fails, it falls back to Open-Meteo.
3. With no key, it uses Open-Meteo directly.

---

## Configuration

### Environment variables

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `OPENWEATHERMAP_API_KEY` | no | unset | Enables OpenWeatherMap forecast, UK postcode geocoding, and alerts |
| `OPENAI_API_KEY` | no | unset | Enables GPT 5.5-mini intent classification tier |
| `ANTHROPIC_API_KEY` | no | unset | Enables Claude Haiku intent classification tier |
| `OLLAMA_HOST` | no | `http://localhost:11434` | Local Ollama endpoint |
| `WEATHER_INTENT_MODEL` | no | `llama3.2:3b` | Ollama classifier model |
| `WEATHER_COMMUTE_MORNING_START` | no | registry/default `08:30` | Morning commute start |
| `WEATHER_COMMUTE_MORNING_END` | no | registry/default `11:30` | Morning commute end |
| `WEATHER_COMMUTE_EVENING_START` | no | registry/default `16:30` | Evening commute start |
| `WEATHER_COMMUTE_EVENING_END` | no | registry/default `19:30` | Evening commute end |
| `WEATHER_LOCATION_REGISTRY_PATH` | no | `skills/weather/location_registry.json` | Runtime registry path override (tests, per-user registries) |

Example commute override:

```bash
export WEATHER_COMMUTE_MORNING_START="07:30"
export WEATHER_COMMUTE_MORNING_END="10:00"
```

---

## Location Registry

Locations are defined in `skills/weather/location_registry.json` (v2.0.0 schema).

Registry entries are structured records with direct provider-ready coordinates. Known aliases resolve without geocoding — the skill constructs a `GeoResult` directly from stored latitude/longitude. Legacy entries without coordinates fall back to `geocode_with_fallback()` using postcode/address lookup fields.

| Alias | Display | Coordinates | Timezone |
|---|---|---|---|
| `home` | Home | 51.5014, −0.1419 (`SW1A 1AA`) | Europe/London |
| `office` | Office | 51.5045, −0.0865 (`SE1 9SG`) | Europe/London |
| `atlanta` | Atlanta | 33.7490, −84.3880 | America/New_York |
| `curitiba` | Curitiba | −25.4296, −49.2713 | America/Sao_Paulo |
| `rio` | Rio de Janeiro | −22.9068, −43.1729 | America/Sao_Paulo |
| `saopaulo` | São Paulo | −23.5558, −46.6396 | America/Sao_Paulo |


**Office / work convention:** `office`, `work`, and `the office` all resolve to the template office location, The Shard, SE1 9SG, without geocoding. Keep real employer names and private office aliases in your ignored local registry only.


**Dynamic location saving:** Unknown free-text locations that successfully fetch weather are saved to the registry with `source: dynamic` and `confidence: high`. A confirmation sentence is appended to the chat output (e.g. `I've saved Tribe Waterloo for next time.`). Low/medium confidence results are not auto-saved. Writes are atomic (temp-file + replace) and target the configured registry path via `WEATHER_LOCATION_REGISTRY_PATH`.

**`last_used_at`:** Every registry hit updates the `last_used_at` timestamp on the matched entry.


Bare or empty location input defaults to `home`.

---

## Intent Classification

`classify_intent(query)` returns a `WeatherIntent`:

```python
WeatherIntent(
    location="home",
    time_reference="now",        # now | today | tomorrow | next-week | YYYY-MM-DD
    intent_type="general",       # general | precipitation | advice | commute | travel_weather
    is_commute=False,
    is_travel=False,           # True for origin/destination travel queries
    destination=None,            # free-text destination when is_travel=True
    origin=None,                 # home | office (defaults to home for travel)
    departure_time=None,         # HH:MM when a specific departure time is detected
    travel_window_start=None,    # ISO datetime — start of travel window
    travel_window_end=None,      # ISO datetime — end of travel window (start + 1 hour)
    travel_window_label=None,   # human label e.g. "09:00–10:00"
    needs_time_clarify=False,    # True when travel query has vague time (e.g. "tomorrow morning")
    raw_query="...",
)
```

Classifier tiering exactly follows the implementation:
1. GPT 5.5-mini via OpenAI when `OPENAI_API_KEY` is set.
2. Claude Haiku via Anthropic when `ANTHROPIC_API_KEY` is set.
3. Ollama local model via `WEATHER_INTENT_MODEL`, default `llama3.2:3b`.
4. Deterministic keyword/pattern fallback, always available.

Deterministic rules recognise weather, precipitation, advice, commute, simple travel weather, known aliases, and coarse time words such as `today` and `tomorrow`.

---

## Advice Engine

`advice_for_location(weather)` produces `WeatherAdvice` flags.

| Advice flag | Condition |
|---|---|
| `umbrella` | today's rain probability > 30%, hourly rain probability > 30%, or current precipitation > 0 mm |
| `sunglasses` | UV index >= 6 and condition is not overcast/rainy/drizzle/thunder |
| `light_jacket` | feels-like temperature 10-15°C |
| `warm_jacket` | feels-like temperature < 10°C |
| `wind_caution` | wind speed > 40 km/h or gusts > 60 km/h |
| `heat_caution` | feels-like temperature > 30°C |
| `cold_caution` | feels-like temperature < 0°C |
| `alerts_text` | OpenWeatherMap alerts, when available |

### Dual-location commute logic

Commute queries fetch **both** `home` and `office` forecasts. `commute_umbrella_check(home_weather, office_weather, window_start="08:30", window_end="11:30", threshold=30)` advises an umbrella when either location has rain risk during the commute window.

Default windows:
- Morning commute / going to work: `08:30-11:30`
- Evening commute / leaving work: `16:30-19:30`

---

## Output Formats

### Chat formatter

`format_chat()` returns emoji-rich, multi-line output for interactive weather queries. Emoji scan markers are a **weather-output requirement for Marcus**, not a global assistant reply-formatting preference.

Weather chat output should prioritise precipitation/umbrella first where possible and use these scan markers consistently:

- 🌧️ precipitation / rain probability
- ☂️ umbrella decision
- 🌡️ temperature
- 💨 wind
- 🚨 alerts
- 🚶/🚇/🚗 commute or travel impact

```text
📍 Home
🌧️ Rain expected (100% chance today)
🌡️ 9°C, feels like 8°C — Overcast clouds
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

☂️ Umbrella: bring one — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold
```

For commute queries, `get_weather_chat()` returns separate Home and Office blocks plus a commute advice summary.

### Briefing formatter

`format_briefing()` returns compact single-line output for morning briefing:

```text
**Weather:** Current 9°C, overcast clouds. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

### Rain timing

The briefing formatter scans the next 24 hours of `hourly_precipitation` and reports the first rain window where probability is > 30% or precipitation is > 0 mm.

Examples:
- `Rain now (100%) — bring umbrella`
- `Rain starting 4pm (80%) — bring umbrella`
- `Rain 4pm-12am (100%) — bring umbrella`

### Tonight's low

Tonight's low is taken from the hourly forecast at **23:00 today**, not from tomorrow's daily low. This is intentionally a bedtime-temperature indicator for the current evening.

---

## Entry Points

```python
from skills.weather.skill import get_weather_chat, get_weather_briefing, get_weather_briefing_commute

print(get_weather_chat("Do I need an umbrella for my commute to work tomorrow morning?"))
print(get_weather_briefing(location="home"))
print(get_weather_briefing_commute())
```

Backwards-compatible Phase 1 API:

```python
from skills.weather.weather_engine import get_weather, rain_in_window, get_commute_windows

weather = get_weather("office")
windows = get_commute_windows()
print(rain_in_window(weather, windows["morning"]["start"], windows["morning"]["end"]))
```

Reliable bash invocation from the workspace root:

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/openclaw/.openclaw/workspace")
from skills.weather.skill import get_weather_chat
print(get_weather_chat("What is the weather in Atlanta?"))
PY
```

---

## Phase 2 Usage Examples

The exact temperatures and precipitation values are live-data dependent; examples show the intended output shape.

### 1. General home weather

Query: `What's the weather?`

```text
📍 Home
🌡️ 9°C, feels like 8°C — Overcast clouds
🌧️ Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

☂️ Umbrella: bring one — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold
```

### 2. City weather

Query: `What's the weather in Atlanta?`

```text
📍 Atlanta
🌡️ 22°C — Clear sky
Today: High 26°C / Low 17°C
Tomorrow: High 27°C / Low 18°C
```

### 3. Rain query

Query: `Will it rain tomorrow?`

```text
📍 Home
🌡️ 9°C, feels like 8°C — Overcast clouds
🌧️ Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

☂️ Umbrella: bring one — rain expected (100% chance)
```

### 4. Advice query

Query: `Should I bring sunglasses?`

```text
📍 Home
🌡️ 18°C — Clear sky
Today: High 22°C / Low 12°C
Tomorrow: High 21°C / Low 11°C

🕶️ Sunglasses: UV index 6 — high sun exposure
```

### 5. Commute umbrella query

Query: `Do I need an umbrella for my commute to work tomorrow morning?`

```text
🏠 **Home**
🌡️ 9°C, feels like 8°C — Overcast clouds
🌧️ Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

☂️ Umbrella: bring one — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold

🏢 **Office**
🌡️ 9°C, feels like 7°C — Overcast clouds
🌧️ Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

☂️ Umbrella: bring one — rain expected (100% chance)
🧥 Jacket needed — feels like 7.42°C — cold

☂️ **Commute umbrella:** Bring one — rain expected during morning commute
   • Rain expected at Home during morning commute (08:30–11:30)
   • Rain expected at Office during morning commute (08:30–11:30)
```

### 6. Morning briefing

Call: `get_weather_briefing(location="home")`

```text
**Weather:** Current 9°C, overcast clouds. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

### 7. Travel weather — vague time (asks for departure time)


Query: `I've gotta go to Paddington tomorrow morning for a train`
```text
⏰ What time are you leaving for Paddington tomorrow?
```

### 8. Travel weather — specific time (fetches both endpoints)


Query: `I'm headed to Tower Bridge from the office at 5:30`

Weather is fetched for both the origin (Office) and destination (Tribe Waterloo) with the one-hour travel window (17:30–18:30). The output names the evaluated window and which endpoint triggered umbrella advice.
```text
🕒 **Travel window:** 17:30–18:30

📍 **Origin: Office**
… weather for The Shard, 17:30–18:30 window …

📍 **Destination: Tribe Waterloo**
… weather for Tribe Waterloo, 17:30–18:30 window …


💡 **Travel advice:** Bring an umbrella for 17:30–18:30.
   • destination: rain chance 45% at 17:45
```

### 9. Dynamic location saving

Query: `Weather at Tribe Waterloo` (first time, not yet in registry)


After a successful forecast the location is saved to the registry and the confirmation sentence is appended:
```text
📍 **Tribe Waterloo**
… weather data …


I've saved Tribe Waterloo for next time.
```
Subsequent queries for Tribe Waterloo resolve from the registry without geocoding.



---

## Error Handling

| Error class | Typical cause | User-facing behaviour |
|---|---|---|
| `LocationNotFoundError` | unknown location/geocoding failure | asks the user to try a city name or known alias |
| `WeatherEngineError` | forecast/provider failure | reports weather temporarily unavailable |
| unexpected exception | integration or runtime failure | returns a friendly failure string instead of raising to chat |

OpenWeatherMap failures fall back to Open-Meteo where possible. A hard error is surfaced only when the complete provider path fails.

---

## File Index

| File | Phase | Purpose |
|---|---:|---|
| `weather_models.py` | 1 | dataclasses for current, daily, hourly, alerts, and `WeatherData` |
| `geocoding.py` | 1 | OpenWeatherMap/Open-Meteo geocoding with fallback |
| `weather_fetcher.py` | 1 | forecast provider access and fallback |
| `weather_engine.py` | 1 | location resolution, forecast orchestration, commute windows, `rain_in_window()` |
| `location_registry.json` | 1 | named locations and commute window defaults |
| `intent_classifier.py` | 2 | `WeatherIntent` and classifier fallback chain |
| `advice_engine.py` | 2 | advice flags and dual-location commute umbrella check |
| `formatters/chat_formatter.py` | 2 | emoji-rich chat formatter |
| `formatters/briefing_formatter.py` | 2 | compact briefing formatter with rain timing and tonight's low |
| `skill.py` | 2 | user-facing chat and briefing entry points |
| `__init__.py` | 1+2 | package exports and version |
| `_meta.json` | 1+2 | skill metadata |
| `bin/morning_briefing.py` | integration | consumes `get_weather_briefing()` with wttr.in fallback |

---

## Publication Readiness Checklist

- [x] v1.3.0 version documented in `SKILL.md`, `README.md`, `_meta.json`, and package exports.
- [x] Registry-first resolution: known aliases with coordinates resolve without geocoding.
- [x] Public office/work SE1 9SG template convention documented.
- [x] Structured registry records with coordinates documented.
- [x] Dynamic location saving after validated weather documented.
- [x] `WEATHER_LOCATION_REGISTRY_PATH` environment variable documented.
- [x] Travel weather: origin/destination, one-hour window, specific-hour, inline clarification documented.
- [x] Travel query examples added (vague-time clarification, specific-time dual-endpoint, dynamic save confirmation).
- [x] `WeatherIntent` dataclass fields fully documented.

## Future Scope, Not Phase 2

- Extended 5+ day outlooks.
- User-editable location registry.
- Multi-user/per-user location registries.
- Additional travel-weather clarification flows beyond the current implemented patterns.


## Personal context shorthand

- `WFH`, `working from home`, and `work from home` resolve weather context to the configured `home` location.
