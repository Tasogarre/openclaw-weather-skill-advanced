"""
intent_classifier.py — Phase 2 Intent Classifier v1.2

Parses natural-language weather queries to extract:
  - location (registry alias or free-text)
  - time_reference (now / today / tomorrow / specific day)
  - intent_type (general / precipitation / advice / commute)
  - is_commute (bool)

Model tiering (fallback chain):
  1. GPT 5.5-mini (OpenAI) via direct API call
  2. Claude Haiku (Anthropic) via direct API call
  3. Ollama (local fallback) - llama3.2:3b or configurable via WEATHER_INTENT_MODEL

Fallback: Deterministic keyword/pattern rules (always available, no external deps)

Environment variables:
  OPENAI_API_KEY       - API key for GPT 5.5-mini (optional)
  ANTHROPIC_API_KEY    - API key for Claude Haiku (optional)
  OLLAMA_HOST          - Ollama endpoint (default: http://localhost:11434)
  WEATHER_INTENT_MODEL - Ollama model fallback (default: llama3.2:3b)

Usage:
    intent = classify_intent("Do I need an umbrella when I go to the office tomorrow?")
    # -> WeatherIntent(location="office", time_reference="tomorrow",
    #                  intent_type="advice", is_commute=True)
"""

import re
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/generate"
OLLAMA_MODEL = os.getenv("WEATHER_INTENT_MODEL", "llama3.2:3b")  # Modern, lightweight default

# ═══════════════════════════════════════════════════════════════════════════
# Intent dataclass
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class WeatherIntent:
    location: str = "home"
    time_reference: str = "now"  # now | today | tomorrow | next-week | YYYY-MM-DD
    intent_type: str = "general"  # general | precipitation | advice | commute
    is_commute: bool = False
    raw_query: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic (fallback)
# ═══════════════════════════════════════════════════════════════════════════

_LOCATIONS = {
    "home", "office", "atlanta", "curitiba", "rio", "saopaulo",
    "london", "paris", "tokyo", "new york", "nyc", "berlin", "amsterdam",
    "são paulo", "sao paulo",
}

_TIME_PATTERNS = {
    "now": [r"\bnow\b", r"\bat the moment\b", r"\bright now\b"],
    "today": [r"\btoday\b"],
    "tomorrow": [r"\btomorrow\b", r"\bovermorrow\b"],
    "next-week": [r"\bnext week\b", r"\bweek ahead\b"],
}

_INTENT_PATTERNS = {
    "precipitation": [r"\brain\b", r"\bsnow\b", r"\bwet\b", r"\bdrizzle\b"],
    "advice": [r"\bneed\b.*\bumbrella\b", r"\bshould i\b", r"\bdo i need\b",
               r"\bbring\b", r"\bpack\b", r"\bwear\b"],
    "commute": [r"\bcommute\b", r"\bgoing to work\b", r"\btraveling to work\b",
                r"\bdriving to\b", r"\bwalking to\b"],
}


def _deterministic_classify(query: str) -> WeatherIntent:
    """
    Deterministic keyword/pattern classifier.
    Used when all model providers fail or for structured fallback.
    """
    location = _extract_location(query)
    time_ref = _extract_time(query)
    intent = _extract_intent(query, time_ref)
    is_commute = _is_commute_query(query)

    return WeatherIntent(
        location=location,
        time_reference=time_ref,
        intent_type=intent,
        is_commute=is_commute,
        raw_query=query,
    )


def _extract_location(query: str) -> str:
    """Extract location from query using keyword matching."""
    q = query.lower()
    for loc in _LOCATIONS:
        if loc in q:
            if loc == "são paulo" or loc == "sao paulo":
                return "saopaulo"
            if loc == "new york" or loc == "nyc":
                return "new york"
            return loc
    return "home"


def _extract_time(query: str) -> str:
    """Extract time reference from query."""
    q = query.lower()
    for time_ref, patterns in _TIME_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, q):
                return time_ref
    return "now"


def _extract_intent(query: str, time_ref: str) -> str:
    """Extract intent type from query."""
    q = query.lower()
    # Check intent patterns in priority order
    if any(re.search(p, q) for p in _INTENT_PATTERNS["advice"]):
        return "advice"
    if any(re.search(p, q) for p in _INTENT_PATTERNS["precipitation"]):
        return "precipitation"
    if any(re.search(p, q) for p in _INTENT_PATTERNS["commute"]):
        return "commute"
    return "general"


def _is_commute_query(query: str) -> bool:
    """Detect commute-related queries."""
    q = query.lower()
    commute_keywords = [
        r"\bcommute\b", r"\bgoing to work\b", r"\btraveling to work\b",
        r"\bdriving to work\b", r"\bwalking to work\b",
        r"\bto the office\b",
    ]
    return any(re.search(kw, q) for kw in commute_keywords)


# ═══════════════════════════════════════════════════════════════════════════
# Intent system prompt (used by all model providers)
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_SYSTEM_PROMPT = """You are a weather query intent classifier. Parse the user query and output ONLY valid JSON with no markdown, no explanation.

Output format:
{
  "location": "<registry_alias or free-text location>",
  "time_reference": "<now|today|tomorrow|next-week|YYYY-MM-DD>",
  "intent_type": "<general|precipitation|advice|commute>",
  "is_commute": <true|false>
}

Registry aliases: home, office, atlanta, curitiba, rio, saopaulo
Intent types:
  - general: weather conditions, forecast
  - precipitation: rain/snow/wet conditions
  - advice: umbrella, clothing, gear recommendations
  - commute: commute-related weather queries

Examples:
Query: "Weather in Atlanta"           → {"location": "atlanta", "time_reference": "now", "intent_type": "general", "is_commute": false}
Query: "What's the weather in Curitiba?" → {"location": "curitiba", "time_reference": "now", "intent_type": "general", "is_commute": false}
"""


# ═══════════════════════════════════════════════════════════════════════════
# Model providers (fallback chain)
# ═══════════════════════════════════════════════════════════════════════════

def _parse_intent_response(response: str) -> Optional[WeatherIntent]:
    """Parse JSON from model response. Returns None on parse failure."""
    try:
        clean = re.sub(r"```json\s*", "", response.strip())
        clean = re.sub(r"```\s*", "", clean).strip()
        data = json.loads(clean)
        return WeatherIntent(
            location=data.get("location", "home"),
            time_reference=data.get("time_reference", "now"),
            intent_type=data.get("intent_type", "general"),
            is_commute=bool(data.get("is_commute", False)),
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.debug(f"Intent parse failed: {e}")
        return None


# ── Tier 1: GPT 5.5-mini (OpenAI) ────────────────────────────────────────

def _call_openai(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call GPT 5.5-mini via OpenAI API. Returns None on failure."""
    if not OPENAI_API_KEY:
        logger.debug("OpenAI API key not set, skipping GPT 5.5-mini")
        return None

    import urllib.request
    import urllib.error

    payload = {
        "model": "gpt-5.5-mini",
        "messages": [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {prompt}"}
        ],
        "temperature": 0.1,
        "max_tokens": 150,
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"OpenAI intent classify failed: {e}")
        return None


# ── Tier 2: Claude Haiku (Anthropic) ─────────────────────────────────────

def _call_anthropic(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call Claude Haiku via Anthropic API. Returns None on failure."""
    if not ANTHROPIC_API_KEY:
        logger.debug("Anthropic API key not set, skipping Claude Haiku")
        return None

    import urllib.request

    payload = {
        "model": "claude-haiku-2025-11-07",  # Latest as of 2026-04-27
        "messages": [
            {"role": "user", "content": f"{_INTENT_SYSTEM_PROMPT}\n\nQuery: {prompt}"}
        ],
        "temperature": 0.1,
        "max_tokens": 150,
    }

    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.debug(f"Anthropic intent classify failed: {e}")
        return None


# ── Tier 3: Ollama (local fallback) ──────────────────────────────────────

def _call_ollama(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call Ollama for intent classification. Returns None on failure."""
    import urllib.request

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{_INTENT_SYSTEM_PROMPT}\n\nQuery: {prompt}",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 150},
    }

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            return result.get("response", "").strip()
    except Exception as e:
        logger.debug(f"Ollama intent classify failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def classify_intent(query: str) -> WeatherIntent:
    """
    Classify a weather query into structured intent.

    Tries in order: GPT 5.5-mini → Claude Haiku → Ollama → deterministic.

    Args:
        query: natural-language weather query

    Returns:
        WeatherIntent with location, time_reference, intent_type, is_commute
    """
    # Strip common prefixes
    clean_query = re.sub(r"^(weather|what's the weather|forecast)\s*", "", query.lower())
    clean_query = clean_query.strip("?.,!")

    # Tier 1: GPT 5.5-mini
    if OPENAI_API_KEY:
        response = _call_openai(clean_query)
        if response:
            result = _parse_intent_response(response)
            if result:
                result.raw_query = query
                return result

    # Tier 2: Claude Haiku
    if ANTHROPIC_API_KEY:
        response = _call_anthropic(clean_query)
        if response:
            result = _parse_intent_response(response)
            if result:
                result.raw_query = query
                return result

    # Tier 3: Ollama (local fallback)
    response = _call_ollama(clean_query)
    if response:
        result = _parse_intent_response(response)
        if result:
            result.raw_query = query
            return result

    # Final fallback: deterministic
    logger.debug("All model providers failed, using deterministic fallback")
    return _deterministic_classify(query)
