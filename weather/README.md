# weather — Rich Weather Skill

Weather forecasting for multiple named locations with commute-aware advice.

**Version 1.2.0** · Phase 2 · Zero-config with Open-Meteo

---

## Quick Start

No configuration needed. Works out of the box.

```python
from skills.weather.skill import get_weather_chat, get_weather_briefing

# Interactive chat query
print(get_weather_chat("What's the weather in atlanta?"))

# Morning briefing (compact)
print(get_weather_briefing())

# Commute query
print(get_weather_chat("Do I need an umbrella when I go to the office tomorrow?"))
```

Or in bash:

```bash
python3 -c "
import sys; sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from skill import get_weather_chat
print(get_weather_chat('What is the weather in atlanta?'))
"
```

---

## Features

### Zero-config
Open-Meteo is used by default — no API key required. Add `OPENWEATHERMAP_API_KEY` for UK postcode geocoding and weather alerts.

### Multi-location
Pre-seeded locations: **home**, **office**, **atlanta**, **curitiba**, **rio**, **saopaulo**

```python
get_weather_chat("Weather in rio")
get_weather_chat("Do I need an umbrella for my commute to work?")
```

### Commute-aware advice
Queries mentioning commute or office automatically check rain at both home AND office locations during the morning commute window (08:30–11:30). Advises umbrella if rain is expected at either location.

### Rain timing
Briefing output shows when rain is expected and for how long:

```
Rain 4pm-12am (100%) — bring umbrella
```

### Tonight's low
Tonight's low is taken from the **11pm hourly forecast** (not tomorrow's daily low) so you know the temperature going to bed.

---

## Usage Examples

### Interactive queries

**General weather:**
```
You: What's the weather?
→ 📍 Home
   🌡️ 9°C, feels like 8°C — Overcast clouds
   💧 Rain expected (100% chance today)

   Today: High 18°C / Low 8°C
   Tomorrow: High 16°C / Low 8°C

   💡 Bring an umbrella — rain expected (100% chance)
   🧥 Jacket needed — feels like 7.52°C — cold
```

**Commute query:**
```
You: Do I need an umbrella for my commute to work tomorrow morning?
→ 🏠 **Home**
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

**Advice query:**
```
You: Should I bring sunglasses?
→ (checks UV index and conditions for current location)
```

### Morning briefing

```python
get_weather_briefing()
# → **Weather:** Current 9°C, overcast. High 18°C. Low 10°C tonight. Rain 4pm-12am (100%) — bring umbrella. Warm jacket
```

---

## Configuration

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENWEATHERMAP_API_KEY` | No | — | Enables OpenWeatherMap One Call 3.0, UK postcode geocoding, and weather alerts |
| `OPENAI_API_KEY` | No | — | Enables GPT 5.5-mini for intent classification (tier 1) |
| `ANTHROPIC_API_KEY` | No | — | Enables Claude Haiku for intent classification (tier 2) |
| `OLLAMA_HOST` | No | `http://localhost:11434` | Ollama API endpoint for local intent classification fallback |
| `WEATHER_INTENT_MODEL` | No | `llama3.2:3b` | Ollama model for intent classification (tier 3 fallback) |

Without `OPENWEATHERMAP_API_KEY`, Open-Meteo is used automatically (no key required, no alerts).

### Location registry

Locations are defined in `location_registry.json`. The home and office locations use UK postcodes for precise geocoding via OpenWeatherMap. Other locations use city names.

```json
{
  "home": {
    "display_name": "Home",
    "primary_query": "SW1A 1AA",
    "fallback_query": "Buckingham Palace, London",
    "timezone": "Europe/London",
    "commute_windows": {
      "morning": {"start": "08:30", "end": "11:30"},
      "evening": {"start": "16:30", "end": "19:30"}
    }
  }
}
```

### Intent classifier

The intent classifier (Gemma4 e4b via Ollama) determines query type and location. If Ollama is unavailable, deterministic keyword/pattern rules are used as fallback.

Supported query patterns:
- `"What's the weather?"` → general, home, now
- `"Do I need an umbrella for my commute?"` → advice, commute, home→office
- `"Will it rain tomorrow?"` → precipitation, tomorrow
- `"Weather in atlanta"` → general, atlanta

---

## Installation

The skill is already installed in the Lucca workspace at `skills/weather/`.

To use it from Python:

```python
import sys
sys.path.insert(0, '/Users/openclaw/.openclaw/workspace/skills/weather')
from skills.weather.skill import get_weather_chat, get_weather_briefing
```

---

## Advice Rules

| Advice | Condition |
|--------|----------|
| �️ Umbrella | rain probability >30% OR current precipitation >0mm |
| 🕶️ Sunglasses | UV index ≥6 AND clear/sunny (not overcast/rainy) |
| 🧥 Warm jacket | feels-like <10°C |
| 🧥 Light jacket | feels-like 10–15°C |
| 💨 Wind caution | wind >40 km/h OR gusts >60 km/h |
| 🔥 Heat caution | feels-like >30°C |
| 🥶 Cold alert | feels-like <0°C |

---

## File Index

| File | Purpose |
|------|---------|
| `skill.py` | Entry point: `get_weather_chat()`, `get_weather_briefing()` |
| `intent_classifier.py` | Intent classification (Gemma4 e4b + deterministic fallback) |
| `advice_engine.py` | Advice rules + dual-location commute check |
| `formatters/chat_formatter.py` | Emoji-rich chat output |
| `formatters/briefing_formatter.py` | Compact briefing output |
| `weather_engine.py` | Core: `get_weather()`, location resolution |
| `weather_fetcher.py` | Dual-provider forecast fetch |
| `geocoding.py` | Geocoding (OWM + Open-Meteo) |
| `weather_models.py` | Data models (`WeatherData`, etc.) |
| `location_registry.json` | Named locations + commute windows |
| `SKILL.md` | Full skill documentation |
| `_meta.json` | Registration metadata |

---

## Upgrade Notes

### v1.1.0 → v1.2.0

Phase 2 adds:
- **Intent classifier** — understands query type and commute signals
- **Advice engine** — umbrella/jacket/sunglasses/wind/heat/cold rules
- **Chat formatter** — emoji-rich conversational output
- **Briefing formatter** — compact single-line with rain timing + tonight's low
- **Dual-location commute** — checks both home and office for rain during commute windows
- **Morning briefing integration** — replaces old `wttr.in` path in `bin/morning_briefing.py`

v1.1.0 skill API (`weather_engine.py`) is fully preserved. `skill.py` provides the new Phase 2 interface.
