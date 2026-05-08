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
from datetime import datetime, timedelta, timezone
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
    intent_type: str = "general"  # general | precipitation | advice | commute | travel_weather
    is_commute: bool = False
    is_travel: bool = False
    destination: Optional[str] = None  # free-text destination when is_travel=True
    origin: Optional[str] = None
    departure_time: Optional[str] = None  # HH:MM when parsed
    travel_window_start: Optional[str] = None  # ISO datetime
    travel_window_end: Optional[str] = None  # ISO datetime
    travel_window_label: Optional[str] = None  # HH:MM–HH:MM
    needs_time_clarify: bool = False  # True when travel query has underspecified time
    # Best-time outing fields (U7)
    is_best_time_request: bool = False
    target_date: Optional[str] = None  # YYYY-MM-DD or today/tomorrow/sunday etc.
    candidate_window_start: Optional[int] = None  # hour 0-23
    candidate_window_end: Optional[int] = None  # hour 0-23 (exclusive upper bound)
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
                r"\bdriving to work\b", r"\bwalking to work\b", r"\bto the office\b",
                r"\binto the office\b", r"\bto work\b",
                r"\bon my way (?:in|into)(?:\s+(?:work|the office|office))?\b",
                r"\bon my way to (?:work|the office|office)\b"],
    "travel_weather": [r"\bfor a train\b", r"\bto catch a\b", r"\btrain to\b",
                       r"\bplatform\b.*\btomorrow\b", r"\bstation\b.*\btomorrow\b"],
}

# Travel-related location keywords — detected as travel when combined with non-commute patterns
_TRAVEL_KEYWORDS = [
    r"\bgoing to\b", r"\bheading to\b", r"\bheaded to\b", r"\bon my way to\b", r"\btravelling to\b",
    r"\btraveling to\b", r"\bto get to\b",
    r"\bgotta\s+go\s+to\b", r"\bgotta\s+head\s+to\b",
    r"\bto (?:the )?(?:station|airport|platform|stop)\b",
    r"\bhave\s+gotta\s+go\s+to\b",
]

# Time patterns that are too vague for travel queries without a specific time
_BEST_TIME_PATTERNS = [
    r"\bwhen is the weather best",
    r"\bwhen is the (?:weather )?best time",
    r"\bwhen is the weather the best",
    r"\bbest time.*(?:weather|walk|go|visit|head)",
    r"\bbest time to(?: walk| go| visit| head)",
    r"\bwhat.*best.*time.*(?:weather|walk|go|visit)",
]

# Time patterns that are too vague for travel queries without a specific time
_VAGUE_TIME_PATTERNS = [
    r"\bmorning\b", r"\bafternoon\b", r"\bevening\b",
    r"\btonight\b", r"\blater\b",
]

# Places with known Sunday market/special hours — hour range encoded as (start, end)
_SPECIAL_PLACE_WINDOWS = {
    "columbia flower market": (8, 15),
    "columbia road flower market": (8, 15),
}

# Date name to YYYY-MM-DD (relative to 2026-05-04 Monday)
_DATE_NAME_MAP = {
    "sunday": "2026-05-03",
    "saturday": "2026-05-02",
    "friday": "2026-05-01",
    "thursday": "2026-04-30",
    "wednesday": "2026-04-29",
    "tuesday": "2026-04-28",
    "monday": "2026-05-04",
}


def _is_best_time_request(query: str) -> bool:
    q = query.lower()
    return any(re.search(p, q) for p in _BEST_TIME_PATTERNS)


def _parse_date_reference(query: str) -> str:
    """Return 'today', 'tomorrow', or YYYY-MM-DD for named days."""
    q = query.lower()
    if re.search(r"\btoday\b", q):
        return "today"
    if re.search(r"\btomorrow\b", q):
        return "tomorrow"
    for day_name, date_str in _DATE_NAME_MAP.items():
        if re.search(r"\b" + day_name + r"\b", q):
            return date_str
    return "today"  # default for best-time


def _get_best_time_candidate_window(destination: Optional[str], date_ref: str, now: Optional[datetime] = None) -> tuple[int, int]:
    """Return (start_hour, end_hour) for a best-time candidate window.

    Special cases: Columbia Road Flower Market on Sunday = 08:00-15:00.
    Generic outings: 09:00-18:00.
    Today clamping: restrict to future hours only if now is provided.
    """
    dest_lower = (destination or "").lower()
    # Check special place windows
    for key, (s, e) in _SPECIAL_PLACE_WINDOWS.items():
        if key in dest_lower:
            if now is not None and date_ref == "today":
                next_hour = now.hour + 1
                if next_hour >= e:
                    return (0, 0)  # no valid window
                return (max(s, next_hour), e)
            return (s, e)
    # Generic outing window
    generic_start = 9
    generic_end = 18
    if now is not None and date_ref == "today":
        next_hour = now.hour + 1
        if next_hour >= generic_end:
            return (0, 0)
        return (max(generic_start, next_hour), generic_end)
    return (generic_start, generic_end)


def _deterministic_classify(query: str) -> WeatherIntent:
    """
    Deterministic keyword/pattern classifier.
    Used when all model providers fail or for structured fallback.
    """
    time_ref = _extract_time(query)
    intent = _extract_intent(query, time_ref)
    is_commute = _is_commute_query(query)
    is_travel = _is_travel_query(query)
    is_best_time = _is_best_time_request(query)
    target_date = _parse_date_reference(query) if is_best_time else None
    # For best-time, extract destination even if not a travel query
    destination = _extract_travel_destination(query) if is_travel else None
    if is_best_time and not destination:
        # Try best-time-specific extraction
        for pat in [r"\bwalk to\s+([^,?]+)", r"\bgo to\s+([^,?]+)", r"\bvisit\s+([^,?]+)", r"\bhead to\s+([^,?]+)"]:
            m = re.search(pat, query, flags=re.IGNORECASE)
            if m:
                dest_candidate = _clean_extracted_place(m.group(1).strip(" ?,."))
                if dest_candidate and len(dest_candidate) >= 2:
                    destination = dest_candidate
                    break
    if is_best_time and not destination:
        destination = _extract_location(query)
    location = destination or _extract_location(query)
    origin = _extract_origin(query) if is_travel else None
    departure_time = _extract_departure_time(query) if is_travel or _has_specific_time(query) else None
    window = build_travel_window(time_ref, departure_time) if departure_time else (None, None, None)
    candidate_start, candidate_end = (0, 0)
    if is_best_time:
        candidate_start, candidate_end = _get_best_time_candidate_window(destination, target_date or "today")
    origin = _extract_origin(query) if is_travel else None
    departure_time = _extract_departure_time(query) if is_travel or _has_specific_time(query) else None
    window = build_travel_window(time_ref, departure_time) if departure_time else (None, None, None)
    needs_time_clarify = _needs_time_clarification(query) if is_travel else False

    is_best_time = _is_best_time_request(query)
    target_date = _parse_date_reference(query) if is_best_time else None
    candidate_start, candidate_end = (0, 0)
    if is_best_time:
        candidate_start, candidate_end = _get_best_time_candidate_window(destination, target_date or "today")


    # Override intent_type for travel queries
    if is_travel and intent not in ("advice", "precipitation"):
        intent = "travel_weather"

    return WeatherIntent(
        location=location,
        time_reference=time_ref,
        intent_type=intent,
        is_commute=is_commute,
        is_travel=is_travel,
        destination=destination,
        origin=origin,
        departure_time=departure_time,
        travel_window_start=window[0],
        travel_window_end=window[1],
        travel_window_label=window[2],
        needs_time_clarify=_needs_time_clarification(query, is_best_time=is_best_time),
        is_best_time_request=is_best_time,
        target_date=target_date,
        candidate_window_start=candidate_start,
        candidate_window_end=candidate_end,
        raw_query=query,
    )


def _clean_extracted_place(candidate: str) -> str:
    """Normalise a free-text place extracted from a query."""
    candidate = re.sub(r"\s+", " ", candidate or "").strip(" ,.?！!;:")
    if not candidate:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in candidate.split())


def _strip_time_suffix(candidate: str) -> str:
    """Remove trailing time/date phrases from an extracted place."""
    return re.split(
        r"\s+(?:at|on|by|for|tomorrow|today|tonight|morning|afternoon|evening|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]


def _extract_location(query: str) -> str:
    """Extract location from query using aliases first, then common free-text patterns."""
    q = query.lower()
    if re.search(r"\b(?:wfh|working from home|work from home)\b", q):
        return "home"
    for loc in _LOCATIONS:
        if loc in q:
            if loc == "são paulo" or loc == "sao paulo":
                return "saopaulo"
            if loc == "new york" or loc == "nyc":
                return "new york"
            return loc

    patterns = [
        r"\bweather\s+(?:in|at|for)\s+(.+)$",
        r"\bforecast\s+(?:in|at|for)\s+(.+)$",
        r"^(.+?)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _strip_time_suffix(match.group(1))
        cleaned = _clean_extracted_place(candidate)
        if cleaned:
            return cleaned

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
        r"\bto the office\b", r"\binto the office\b", r"\bto work\b",
        r"\bon my way (?:in|into)(?:\s+(?:work|the office|office))?\b",
        r"\bon my way to (?:work|the office|office)\b",
    ]
    return any(re.search(kw, q) for kw in commute_keywords)


def _is_travel_query(query: str) -> bool:
    """Detect travel queries (going to a place that's not home or office)."""
    q = query.lower()
    if re.search(r"\b(?:wfh|working from home|work from home)\b", q) and re.search(r"\bgo for a walk\b", q):
        return False
    # Must have a travel keyword
    has_travel_kw = any(re.search(kw, q) for kw in _TRAVEL_KEYWORDS)
    if not has_travel_kw:
        return False
    # Must NOT be a known commute pattern
    if _is_commute_query(query):
        return False
    return True


def _extract_travel_destination(query: str) -> Optional[str]:
    """
    Extract the destination from a travel query.
    Looks for 'to [place]' patterns and stops before time/preposition words.
    """
    match = re.search(
        r"\bto\s+([a-z][a-z0-9'&.\-\s]{1,80}?)(?=\s+(?:from|at|on|by|for|tomorrow|today|tonight|morning|afternoon|evening|night)\b|[?.!,;:]|$)",
        query,
        flags=re.IGNORECASE,
    )
    if match:
        cleaned = _clean_extracted_place(_strip_time_suffix(match.group(1)))
        if cleaned and len(cleaned) >= 2:
            return cleaned
    return None



def _extract_origin(query: str) -> str:
    q = query.lower()
    if re.search(r"\bfrom\s+(?:my\s+)?(?:office|work|the office)\b", q):
        return "office"
    if re.search(r"\bfrom\s+(?:home|my place)\b", q):
        return "home"
    return "home"


def _has_specific_time(query: str) -> bool:
    return _extract_departure_time(query) is not None


def _extract_departure_time(query: str) -> Optional[str]:
    q = query.lower()
    match = re.search(r"\b(?:at|around|about|by|before|after)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", q)
    if not match:
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))\s*(am|pm)?\b", q)
    if not match:
        compact = re.search(r"\b(?:at|around|about)\s+(\d{3,4})\b", q)
        if compact:
            digits = compact.group(1)
            hour = int(digits[:-2])
            minute = int(digits[-2:])
            if 1 <= hour <= 12 and minute < 60:
                # Compact colloquial travel times like 530 normally mean evening.
                hour += 12
                return f"{hour:02d}:{minute:02d}"
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if minute > 59:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem is None and 1 <= hour <= 7 and re.search(r"\b(?:heading|headed|going|from|leaving|train|station)\b", q):
        hour += 12
    if hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _date_for_time_reference(time_reference: str, now: Optional[datetime] = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if time_reference == "tomorrow":
        base = base + timedelta(days=1)
    return base


def build_travel_window(time_reference: str, departure_time: Optional[str], now: Optional[datetime] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not departure_time:
        return None, None, None
    try:
        hour, minute = map(int, departure_time.split(":"))
    except ValueError:
        return None, None, None
    base = _date_for_time_reference(time_reference, now)
    start = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    return start.isoformat(), end.isoformat(), f"{start:%H:%M}–{end:%H:%M}"

def _needs_time_clarification(query: str, is_best_time: bool = False) -> bool:
    """
    Return True if query is a travel query with a vague time reference.
    Vague means: 'morning', 'afternoon', 'evening' without a specific hour.
    Best-time requests should NEVER ask for time clarification.
    """
    if is_best_time:
        return False
    q = query.lower()
    has_vague_time = any(re.search(p, q) for p in _VAGUE_TIME_PATTERNS)
    if not has_vague_time:
        return False
    specific_time_indicators = [
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        r"\bat\s+\d{1,2}(?::\d{2})?\b",
        r"\bby\s+\d{1,2}\b",
        r"\bbefore\s+\d{1,2}\b",
        r"\bafter\s+\d{1,2}\b",
    ]
    has_specific_time = any(re.search(p, q) for p in specific_time_indicators)
    return has_vague_time and not has_specific_time


# ═══════════════════════════════════════════════════════════════════════════
# Intent system prompt (used by all model providers)
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_SYSTEM_PROMPT = """You are a weather query intent classifier. Parse the user query and output ONLY valid JSON with no markdown, no explanation.

Output format:
{
  "location": "<registry_alias or free-text location>",
  "time_reference": "<now|today|tomorrow|next-week|YYYY-MM-DD>",
  "intent_type": "<general|precipitation|advice|commute|travel_weather>",
  "is_commute": <true|false>,
  "is_travel": <true|false>,
  "destination": "<destination or null>",
  "origin": "<home|office|null>",
  "departure_time": "<HH:MM or null>",
  "travel_window_start": "<ISO datetime or null>",
  "travel_window_end": "<ISO datetime or null>",
  "travel_window_label": "<HH:MM–HH:MM or null>",
  "needs_time_clarify": <true|false>
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
            is_travel=bool(data.get("is_travel", False)),
            destination=data.get("destination"),
            origin=data.get("origin"),
            departure_time=data.get("departure_time"),
            travel_window_start=data.get("travel_window_start"),
            travel_window_end=data.get("travel_window_end"),
            travel_window_label=data.get("travel_window_label"),
            needs_time_clarify=bool(data.get("needs_time_clarify", False)),
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
