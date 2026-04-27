---
name: weather
version: 1.2.0
description: Rich weather forecasting for multiple named locations with commute-aware advice, intent classification, and dual-format output. Supports both interactive chat and morning briefing delivery.
kind: functional
allowed-tools: Bash, web_fetch
---

# weather — Rich Weather Skill v1.2

## Overview

Provides rich, multi-location weather forecasting via OpenWeatherMap One Call 3.0 (when key available) or Open-Meteo (always, no key required). Resolves named location aliases (home, office, atlanta, etc.) with full geocoding fallback chain. Returns structured weather data with actionable advice, intent-classified queries, and dual-format output (chat or briefing).

**Phase 1 scope:** location registry, geocoding, forecast fetch, structured weather object, weather alerts (OWM only).
**Phase 2 scope (this version):** advice engine, intent classifier (Gemma4 e4b + deterministic fallback), chat/briefing formatters, dual-location commute logic, rain timing, skill entry point, morning briefing integration.

---

## Architecture

```
User query
  → Intent classifier       (Ollama Gemma4 e4b + deterministic fallback)
  │   └── WeatherIntent: location, time_reference, intent_type, is_commute
  → Location resolution     (registry alias → registry entry + geocoding)
  │   └── Commute query: resolve BOTH home and office
  → Forecast fetch          (dual-provider, auto-fallback)
  │   ├── OWM API key present → OpenWeatherMap One Call 3.0 + alerts
  │   └── No key / OWM fails → Open-Meteo (always available)
  → Structured WeatherData object
  → Advice engine
  │   ├── Single location: advice_for_location()
  │   └── Commute query: commute_umbrella_check() — both home + office
  → Formatter
  │   ├── Chat: emoji-rich, multi-line, per-location (interactive queries)
  │   └── Briefing: compact single-line (morning briefing)
  → Output string
```

### Phase 2 Components

| Component | File | Purpose |
|-----------|------|---------|
| Intent classifier | `intent_classifier.py` | Ollama Gemma4 e4b + deterministic fallback; extracts location, time, intent type, commute flag |
| Advice engine | `advice_engine.py` | Umbrella/sunglasses/jacket/wind/heat/cold rules; `commute_umbrella_check()` for dual-location |
| Chat formatter | `formatters/chat_formatter.py` | Emoji-rich conversational output; per-location block |
| Briefing formatter | `formatters/briefing_formatter.py` | Compact single-line for morning briefing; rain timing + confidence |
| Skill entry point | `skill.py` | Ties classifier → engine → advice → formatter; exposes `get_weather_chat()` and `get_weather_briefing()` |
| Morning briefing | `bin/morning_briefing.py` | Integrated with new weather skill (Phase 2 verified) |

---

## Provider Selection

| Scenario | Provider | Notes |
|----------|----------|-------|
| `OPENWEATHERMAP_API_KEY` set | OpenWeatherMap One Call 3.0 | UK postcode geocoding, weather alerts |
| No OWM key | Open-Meteo | No key required; no alerts |
| OWM request fails | Open-Meteo auto-fallback | Seamless; `source` field reflects actual provider |

Weather alerts are only populated when using OpenWeatherMap — `alerts` is an empty list with Open-Meteo.

---

## Intent Classification

The skill classifies every query before fetching weather. Model tiering (fallback chain):

1. **GPT 5.5-mini** (OpenAI) — requires `OPENAI_API_KEY`
2. **Claude Haiku** (Anthropic) — requires `ANTHROPIC_API_KEY`
3. **Ollama** (local fallback) — configurable via `WEATHER_INTENT_MODEL` (default: `llama3.2:3b`)
4. **Deterministic** (keyword/pattern fallback) — always available, no external deps

If all model providers fail or are unavailable, falls back to deterministic keyword/pattern rules.

### WeatherIntent result

```python
WeatherIntent(
    location: str       # registry alias or free-text city name
    time_reference: str # "now" | "today" | "tomorrow" | "next-week" | "YYYY-MM-DD"
    intent_type: str    # "general" | "precipitation" | "advice" | "commute"
    is_commute: bool    # True if query mentions commute / travel to work/office
    raw_query: str      # original query
)
```

### Deterministic fallback rules

| Signal | Match | Result |
|--------|-------|--------|
| "umbrella/rain/wet/drizzle/shower" | query contains | `intent_type = precipitation` |
| "need/should/bring/wear/advice" | query contains | `intent_type = advice` |
| "commute/going to work/to the office" | query contains | `is_commute = True` |
| "tomorrow/tmrw" | regex match | `time_reference = tomorrow` |
| "home/office/work/atlanta/etc." | registry alias | `location = alias` |
| UK postcode detected + OWM key | `/zip` endpoint | precise geocode |

### Ollama prompt

System prompt instructs the model to output only JSON. Structured examples cover the main query patterns. Temperature is set to 0.1 for deterministic output; `num_predict` capped at 150 tokens.

---

## Location Registry

Pre-seeded locations in `skills/weather/location_registry.json`:

| Alias | Display | Primary Query | Fallback Query | Timezone |
|-------|---------|---------------|----------------|----------|
| `home` | Home | SW1A 1AA (UK postcode) | Buckingham Palace, London | Europe/London |
| `office` | Work | SE1 9SG (UK postcode) | City of London, England | Europe/London |
| `atlanta` | Atlanta | Atlanta | Atlanta | America/New_York |
| `curitiba` | Curitiba | Curitiba, Brazil | Curitiba | America/Sao_Paulo |
| `rio` | Rio de Janeiro | Rio de Janeiro, Brazil | Rio de Janeiro | America/Sao_Paulo |
| `saopaulo` | São Paulo | São Paulo, Brazil | São Paulo | America/Sao_Paulo |

**Default:** bare/empty query → `home`.

---

## Geocoding

| Provider | Endpoint | UK Postcodes | Key Required |
|----------|----------|-------------|--------------|
| OpenWeatherMap | `api.openweathermap.org/geo/1.0` | ✓ Via `/zip` endpoint | ✓ |
| Open-Meteo | `geocoding-api.open-meteo.com/v1/search` | ✗ Unreliable | ✗ |

Resolution chain:
1. UK postcode detected + OWM key available → OWM `/zip` endpoint (precise)
2. OWM key available → OWM `/direct` (primary)
3. Open-Meteo fallback (always available)

---

## Weather Data

Structured output via `WeatherData` dataclass (see `weather_models.py`):

- `current`: temperature, feels-like, condition, wind, precipitation, humidity, cloud, UV, visibility, pressure
- `today`: high/low, condition, precip probability, wind gusts, UV max, sunrise/sunset
- `tomorrow`, `day_after`: same fields as `today`
- `hourly_precipitation`: list of `{time, probability, mm}` for rain timing
- `alerts`: list of `WeatherAlert` — **OpenWeatherMap only**; empty list from Open-Meteo
- `source`: `"openweathermap"` or `"open-meteo"`

---

## Commute Windows

From registry:
- Morning: `08:30` – `11:30` (going to work)
- Evening: `16:30` – `19:30` (leaving work)

---

## Advice Engine

Generates actionable advice flags from `WeatherData`:

| Flag | Condition |
|------|-----------|
| `umbrella` | rain probability >30% **OR** current precipitation >0mm |
| `sunglasses` | UV index ≥6 **AND** condition NOT overcast/rainy/drizzle/thunder |
| `light_jacket` | feels-like 10–15°C |
| `warm_jacket` | feels-like <10°C |
| `wind_caution` | wind speed >40 km/h **OR** gusts >60 km/h |
| `heat_caution` | feels-like >30°C |
| `cold_caution` | feels-like <0°C |

Alerts from OpenWeatherMap are surfaced in `alerts_text`.

### Dual-location commute logic

For `is_commute = True` queries, the skill fetches weather for **both** `home` and `office`, then calls `commute_umbrella_check()`:

```
commute_umbrella_check(home_weather, office_weather,
                       window_start="08:30", window_end="11:30", threshold=30)
→ (needs_umbrella: bool, reasons: list[str])
```

Returns `needs_umbrella = True` if **either** location shows rain probability >30% during the morning commute window. Reasons list identifies which location(s) have rain risk.

---

## Output Formats

### Chat formatter (`format_chat()`)

Emoji-rich, multi-line, per-location. Used for interactive queries.

```
📍 Home
🌡️ 9°C, feels like 8°C — Overcast clouds
💧 Rain expected (100% chance today)

Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

💡 Bring an umbrella — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold
```

For commute queries, outputs both home and office blocks plus a commute advice summary.

### Briefing formatter (`format_briefing()`)

Compact single-line (or short paragraph), no emoji. Used for morning briefing.

```
**Weather:** Current 9°C (feels like 8°C), overcast. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

### Rain timing feature

The briefing formatter extracts rain timing from `hourly_precipitation` and formats it as:

```
Rain 4pm-12am (100%)
```

Format: `Rain {start_time}-{end_time} ({max_probability}%)`

- Start/end times derived from first/last hour with >30% probability or >0mm
- Times shown as `4pm`, `12am` etc. (12-hour, stripped leading zero)
- Short rain (<3h window): `Rain starting 4pm (100%)`
- Immediate rain (<30 min): `Rain now (100%)`
- Maximum probability shown (aggregated across the rain period)

### Tonight's low

Tonight's low is extracted from the **11pm hourly forecast** for today's date — not tomorrow's daily low. This is the temperature you'd experience going to bed.

```python
def _get_tonight_11pm_temp(weather: WeatherData) -> Optional[float]:
    """Extract tonight's 11pm temperature from hourly forecast."""
    today = datetime.now().date()
    for hf in weather.hourly_forecast:
        if hf.time.date() == today and hf.time.hour == 23:
            return hf.temperature
    return None
```

---

## Skill Entry Points

```python
from skills.weather.skill import get_weather_chat, get_weather_briefing

# Interactive chat query (auto-classifies intent, handles commute)
result = get_weather_chat("Do I need an umbrella when I go to the office tomorrow?")
print(result)

# Morning briefing (compact, single-line)
briefing = get_weather_briefing(location="home")
print(briefing)
```

Both functions return strings. Errors surface as user-friendly messages (not raw exceptions).

---

## Phase 2 Verified Outputs

### Complex commute query
Query: *"Do I need an umbrella for my commute to work tomorrow morning?"*

```
🏠 **Home**
🌡️ 9°C, feels like 8°C — Overcast clouds
💧 Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

💡 Bring an umbrella — rain expected (100% chance)
🧥 Jacket needed — feels like 7.52°C — cold

🏢 **Office**
🌡️ 9°C, feels like 7°C — Overcast clouds
💧 Rain expected (100% chance today)
Today: High 18°C / Low 8°C
Tomorrow: High 16°C / Low 8°C

💡 Bring an umbrella — rain expected (100% chance)
🧥 Jacket needed — feels like 7.42°C — cold

💡 **Commute advice:** Bring an umbrella — rain expected during morning commute
   • Rain expected at Home during morning commute (08:30–11:30)
   • Rain expected at Office during morning commute (08:30–11:30)
```

### Morning briefing
```
**Weather:** Current 9°C, overcast. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

---

## Usage in Bash

```bash
# Interactive chat query
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from skill import get_weather_chat
print(get_weather_chat('What is the weather in atlanta?'))
"

# Commute query
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from skill import get_weather_chat
print(get_weather_chat('Do I need an umbrella for my commute to work tomorrow?'))
"

# Morning briefing
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from skill import get_weather_briefing
print(get_weather_briefing())
"

# Default location (home)
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from weather_engine import get_weather
w = get_weather()
print(f'{w.location_label}: {w.current.temperature}°C, {w.current.condition}')
print(f'Source: {w.source}')
"

# Rain risk in morning commute window
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from weather_engine import get_weather, rain_in_window, get_commute_windows
w = get_weather('office')
windows = get_commute_windows()
morning = windows['morning']
risky = rain_in_window(w, morning['start'], morning['end'])
print(f'Rain risk during morning commute: {risky}')
"
```

---

## Error Handling

| Error | Cause | Surface |
|-------|-------|---------|
| `LocationNotFoundError` | No geocoding results for query | "Couldn't find that location" |
| `WeatherEngineError` | Network/API failure | "Weather temporarily unavailable" |
| `WeatherFetchError` | Open-Meteo API error (no OWM fallback) | "Weather temporarily unavailable" |

When OWM API key is present but OWM request fails, the skill auto-falls back to Open-Meteo transparently — the user only sees an error if both providers fail.

---

## File Index

| File | Phase | Purpose |
|------|-------|---------|
| `weather_models.py` | 1 | `WeatherData`, `CurrentConditions`, `DailyForecast`, `HourlyPrecipitation`, `WeatherAlert` dataclasses |
| `geocoding.py` | 1 | `geocode()`, `geocode_with_fallback()`, `GeoResult`, `GeocodingError` — dual provider |
| `weather_fetcher.py` | 1 | `fetch_forecast()`, `WeatherFetchError` — dual provider with auto-fallback |
| `weather_engine.py` | 1 | `get_weather()`, `resolve_location()`, `rain_in_window()`, `get_commute_windows()` |
| `location_registry.json` | 1 | Seed data for 6 named locations + commute windows (UK postcodes for home/office) |
| `intent_classifier.py` | 2 | `classify_intent()`, `WeatherIntent` — Ollama Gemma4 e4b + deterministic fallback |
| `advice_engine.py` | 2 | `advice_for_location()`, `commute_umbrella_check()`, `WeatherAdvice` dataclass |
| `formatters/chat_formatter.py` | 2 | `format_chat()` — emoji-rich conversational output |
| `formatters/briefing_formatter.py` | 2 | `format_briefing()` — compact single-line with rain timing + tonight's low |
| `skill.py` | 2 | `get_weather_chat()`, `get_weather_briefing()` — skill entry points |
| `bin/morning_briefing.py` | 2 | Integrated with new weather skill |
| `__init__.py` | 1+2 | Skill module init + version + exports |
| `_meta.json` | 1+2 | Skill registration metadata (version 1.2.0, phase 2) |

---

## Zero-Config vs Enhanced

| Feature | Open-Meteo (no key) | OpenWeatherMap (+ key) |
|---------|---------------------|------------------------|
| Weather data | ✓ | ✓ (One Call 3.0) |
| UK postcode geocoding | ✗ (unreliable) | ✓ via `/zip` |
| Weather alerts | ✗ | ✓ |
| Forecast days | Hourly + daily | Hourly + daily + minutely |
| Intent classifier | ✓ (Ollama) | ✓ (Ollama) |
| Advice engine | ✓ | ✓ |
| Chat formatter | ✓ | ✓ |
| Briefing formatter | ✓ | ✓ |

Zero-config works out of the box with Open-Meteo. An `OPENWEATHERMAP_API_KEY` enables UK postcode geocoding and weather alerts.

---

## Phase 3 Items (NOT yet built)

- Extended forecast support (5+ day outlook)
- Non-UK location geocoding improvements
- User-configurable location registry (add/remove locations)
- Multi-user / per-user location registry
