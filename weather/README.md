# weather — Rich Weather Skill

Version 1.3.0 weather forecasting for named locations, commute-aware advice, chat answers, and morning briefing output.

Works out of the box with Open-Meteo. Add `OPENWEATHERMAP_API_KEY` for OpenWeatherMap One Call 3.0, UK postcode geocoding, and weather alerts.

---

## Mandatory Assistant Routing

Any weather-related user request — including forecasts, rain, umbrella decisions, clothing decisions driven by weather, commute weather, travel weather, or best-time-for-weather planning — must route through this skill before the assistant answers. Do not answer such requests generically or ask broad location questions before invoking the skill path.

Operational runbook: `docs/runbooks/weather-request-routing-runbook.md`.

## Quick Start

From the workspace root:

```python
from skills.weather.skill import get_weather_chat, get_weather_briefing

print(get_weather_chat("What's the weather?"))
print(get_weather_chat("Do I need an umbrella for my commute to work tomorrow morning?"))
print(get_weather_briefing(location="home"))
```

Bash example:

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/openclaw/.openclaw/workspace")
from skills.weather.skill import get_weather_chat
print(get_weather_chat("What is the weather in Atlanta?"))
PY
```

---

## What It Does

- Resolves named locations: `home`, `office`, `atlanta`, `curitiba`, `rio`, `saopaulo`.
- Fetches weather through OpenWeatherMap when configured, otherwise Open-Meteo.
- Classifies natural-language weather queries.
- Gives advice for umbrella, sunglasses, jackets, wind, heat, cold, and alerts.
- Handles commute queries by checking both home and office during the commute window.
- Formats interactive chat responses with emoji.
- Formats morning briefing responses as compact one-line summaries.
- Reports rain timing such as `Rain 4pm-12am (100%)`.
- Uses the 23:00 hourly forecast for `Low X°C tonight` rather than tomorrow's daily low.

---

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `OPENWEATHERMAP_API_KEY` | no | unset | Enables OpenWeatherMap, UK postcode geocoding, and alerts |
| `WEATHER_INTENT_LLM_ENABLED` | no | `false` | Enables a configured OpenAI-compatible intent classifier endpoint |
| `WEATHER_INTENT_LLM_BASE_URL` | no | unset | OpenAI-compatible base URL ending in `/v1` |
| `WEATHER_INTENT_LLM_MODEL` | no | unset | Model ID for the configured intent classifier endpoint |
| `WEATHER_INTENT_LLM_API_KEY` | no | unset | API key for the configured intent classifier endpoint |
| `WEATHER_INTENT_LLM_API_KEY_ENV` | no | `WEATHER_INTENT_LLM_API_KEY` | Alternate env var containing the configured endpoint API key |
| `WEATHER_INTENT_LLM_CONFIG_PATH` | no | `intent_llm.json` beside this module | Optional ignored JSON config path; copy from `intent_llm.example.json` |
| `WEATHER_INTENT_LLM_TIMEOUT_SECONDS` | no | `8` | Timeout for configured endpoint calls |
| `OPENAI_API_KEY` | no | unset | (Deprecated — routing now via OmniRoute only) |
| `ANTHROPIC_API_KEY` | no | unset | (Deprecated — routing now via OmniRoute only) |
| `OLLAMA_HOST` | no | `http://localhost:11434` | Ollama endpoint |
| `WEATHER_INTENT_MODEL` | no | `llama3.2:3b` | Ollama classifier model |
| `WEATHER_COMMUTE_MORNING_START` | no | `08:30` | Morning commute start |
| `WEATHER_COMMUTE_MORNING_END` | no | `11:30` | Morning commute end |
| `WEATHER_COMMUTE_EVENING_START` | no | `16:30` | Evening commute start |
| `WEATHER_COMMUTE_EVENING_END` | no | `19:30` | Evening commute end |

No configuration is needed for the default Open-Meteo path.

### Intent LLM local configuration

For local endpoint configuration, copy `intent_llm.example.json` to ignored `intent_llm.json` or `intent_llm.local.json` beside this module and fill in your endpoint, model, and API-key env var. The committed example is disabled and uses placeholders only.


---

## Intent Classifier

The classifier returns location, time reference, intent type, and commute/travel flags. It uses this fallback chain:


1. Configured OpenAI-compatible LLM endpoint when enabled with `WEATHER_INTENT_LLM_*` or an ignored local config file.
2. GPT 5.4-mini via OmniRoute routing (no direct API call required).
3. Claude Haiku via OmniRoute routing (no direct API call required).
4. Ollama using `WEATHER_INTENT_MODEL`, default `llama3.2:3b`; overridden by `intent_llm.local.json` `ollama.model` on your machine.
5. Deterministic keyword/pattern fallback, always available.

Copy `intent_llm.example.json` to ignored `intent_llm.json` or `intent_llm.local.json` for local endpoint configuration. Do not commit real endpoints, model routing, or API-key names tied to a private environment.

Recognised patterns include general forecasts, rain/snow questions, umbrella/clothing advice, commute-to-work queries, and a small set of travel-weather questions.

---

## Locations

Locations are configured in `location_registry.json`.

| Alias | Display | Notes |
|---|---|---|
| `home` | Home | default location, UK postcode primary query |
| `office` | Work | The Shard / SE1 9SG |
| `atlanta` | Atlanta | city-name geocoding |
| `curitiba` | Curitiba | city-name geocoding |
| `rio` | Rio de Janeiro | city-name geocoding |
| `saopaulo` | São Paulo | city-name geocoding |

Home and office are used together for commute checks.

---

## Usage Examples

### General chat

```python
get_weather_chat("What's the weather?")
```

Example shape:

```text
📍 Home
🌡️ 9°C, feels like 8°C — Overcast clouds
💧 Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

💡 Bring an umbrella — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold
```

### City query

```python
get_weather_chat("What's the weather in Atlanta?")
```

### Rain query

```python
get_weather_chat("Will it rain tomorrow?")
```

### Advice query

```python
get_weather_chat("Should I bring sunglasses?")
```

### Commute query

```python
get_weather_chat("Do I need an umbrella for my commute to work tomorrow morning?")
```

Example shape:

```text
🏠 **Home**
🌡️ 9°C, feels like 8°C — Overcast clouds
...

🏢 **Office**
🌡️ 9°C, feels like 7°C — Overcast clouds
...

💡 **Commute advice:** Bring an umbrella — rain expected during morning commute
   • Rain expected at Home during morning commute (08:30–11:30)
   • Rain expected at Office during morning commute (08:30–11:30)
```

### Morning briefing

```python
get_weather_briefing(location="home")
```

Example:

```text
**Weather:** Current 9°C, overcast clouds. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

`bin/morning_briefing.py` uses this Phase 2 briefing path first and falls back to wttr.in if the skill path fails.

---

## Advice Rules

| Advice | Condition |
|---|---|
| ☂️ Umbrella | rain probability > 30% or current precipitation > 0 mm |
| 🕶️ Sunglasses | UV index >= 6 and not overcast/rainy/drizzly/thundery |
| 🧥 Warm jacket | feels-like temperature < 10°C |
| 🧥 Light jacket | feels-like temperature 10-15°C |
| 💨 Wind caution | wind > 40 km/h or gusts > 60 km/h |
| 🔥 Heat caution | feels-like temperature > 30°C |
| 🥶 Cold caution | feels-like temperature < 0°C |
| 🚨 Alerts | OpenWeatherMap alerts when available |

Commute umbrella advice checks both home and office for rain risk during the morning window, default `08:30-11:30`.

---

## API Surface

Phase 2 entry points:

```python
from skills.weather.skill import (
    get_weather_chat,
    get_weather_briefing,
    get_weather_briefing_commute,
)
```

Phase 1 API remains available:

```python
from skills.weather.weather_engine import get_weather, get_commute_windows, rain_in_window
```

---

## File Index

| File | Purpose |
|---|---|
| `skill.py` | Phase 2 chat and briefing entry points |
| `intent_classifier.py` | Natural-language intent classifier and deterministic fallback |
| `advice_engine.py` | Advice flags and commute umbrella logic |
| `formatters/chat_formatter.py` | Emoji-rich chat output |
| `formatters/briefing_formatter.py` | Compact briefing output, rain timing, tonight's low |
| `weather_engine.py` | Phase 1 orchestration, registry resolution, commute windows |
| `weather_fetcher.py` | OpenWeatherMap/Open-Meteo forecast fetch |
| `geocoding.py` | OpenWeatherMap/Open-Meteo geocoding |
| `weather_models.py` | Data models |
| `location_registry.json` | Location aliases and commute windows |
| `SKILL.md` | Full skill documentation |
| `_meta.json` | Skill metadata |

---

## Publication Readiness

- v1.2.0 documented.
- Zero-config Open-Meteo path documented.
- Enhanced OpenWeatherMap path documented.
- Intent classifier fallback chain documented as implemented.
- Advice, commute, rain timing, tonight-low, chat, and briefing behaviours documented.
- Morning briefing integration documented.
- Phase 1 backwards compatibility preserved.


## Personal context shorthand

- `WFH`, `working from home`, and `work from home` resolve to the configured `home` location for weather and walk advice.
