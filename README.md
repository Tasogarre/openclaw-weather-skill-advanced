# openclaw-weather-skill-advanced

Advanced weather skill for OpenClaw with intent classification, advice engine, and dual-location commute support.

## Features

- **Intent Classification**: Tiered model approach (GPT 5.5-mini → Claude Haiku → Ollama → deterministic fallback)
- **Advice Engine**: Smart weather recommendations (umbrella, jacket, sunglasses, wind/heat/cold warnings)
- **Dual-Location Commute**: Automatically checks both home and office for rain during commute hours
- **Rain Timing**: Shows when rain is expected with confidence percentage (e.g., "Rain 4pm-12am (100%)")
- **Tonight's Low**: Pulls 11pm temperature for accurate overnight low
- **Dual Provider**: OpenWeatherMap (enhanced) or Open-Meteo (zero-config default)
- **Morning Briefing**: Compact single-line format for daily briefing integration

## Zero-Config Setup

No API keys required. Works out of the box with Open-Meteo:
- Intent classification via deterministic fallback (keyword/pattern matching)
- Weather via Open-Meteo (free, no key needed)

## Enhanced Setup (optional)

For full capability with intent classification + weather alerts:

```bash
export OPENWEATHERMAP_API_KEY="your-key"      # OpenWeatherMap One Call 3.0
export OPENAI_API_KEY="your-key"              # GPT 5.5-mini for intent
export ANTHROPIC_API_KEY="your-key"           # Claude Haiku fallback
```

For custom intent model via Ollama:
```bash
export WEATHER_INTENT_MODEL="llama3.2:3b"      # Default: llama3.2:3b
```

## Usage

```python
from skills.weather.skill import get_weather_chat, get_weather_briefing

# Chat query
response = get_weather_chat("What's the weather?")
print(response)

# Morning briefing
briefing = get_weather_briefing(location="home")
print(briefing)
```

## Installation

See [SKILL.md](./weather/SKILL.md) for full documentation.

## License

MIT


## Location Registry Privacy

This repository ships `weather/location_registry.example.json` with public London landmark examples only. Copy it to `weather/location_registry.json` for local use. The real `location_registry.json` is ignored and must not be committed because it may contain private home/work coordinates, postcodes, aliases, or company names.

Run the privacy gate before committing:

```bash
scripts/check-private-weather-data.sh tree
scripts/check-private-weather-data.sh staged
```
