"""
intent_classifier.py — Phase 2 Intent Classifier v1.2

Parses natural-language weather queries to extract:
  - location (registry alias or free-text)
  - time_reference (now / today / tomorrow / specific day)
  - intent_type (general / precipitation / advice / commute)
  - is_commute (bool)

Model tiering (fallback chain):
  1. Configured OpenAI-compatible LLM endpoint
  2. GPT 5.4-mini via OmniRoute (routed, not direct)
  3. Claude Haiku via OmniRoute (routed, not direct)
  4. Ollama (local fallback) - gemma4-e4b via local config, llama3.2:3b public default

Fallback: Deterministic keyword/pattern rules (always available, no external deps)

Environment variables:
  WEATHER_INTENT_LLM_ENABLED         - Enable configured OpenAI-compatible LLM tier (true/false)
  WEATHER_INTENT_LLM_BASE_URL        - OpenAI-compatible base URL ending in /v1
  WEATHER_INTENT_LLM_MODEL           - Model ID for configured LLM tier
  WEATHER_INTENT_LLM_API_KEY         - API key for configured LLM tier
  WEATHER_INTENT_LLM_API_KEY_ENV     - Env var name containing the API key (default: WEATHER_INTENT_LLM_API_KEY)
  WEATHER_INTENT_LLM_CONFIG_PATH     - Optional JSON config path
  WEATHER_INTENT_LLM_TIMEOUT_SECONDS - Optional timeout override (default: 8)
  WEATHER_INTENT_OMNIROUTE_MODEL      - OmniRoute model ID for intent classifier (default: omniroute/cx/gpt-5.4-mini)
  WEATHER_OMNIROUTE_URL              - OmniRoute endpoint (default: http://127.0.0.1:20128/v1/chat/completions)
  OLLAMA_HOST                        - Ollama endpoint (default: http://localhost:11434)
  WEATHER_INTENT_MODEL               - Ollama model override via env var (env wins over local config)

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Callable

_SKILL_DIR = Path(__file__).parent

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/generate"

# OmniRoute endpoint for intent classification (primary model routing)
OMNIROUTE_URL = os.getenv("WEATHER_OMNIROUTE_URL", "http://127.0.0.1:20128/v1/chat/completions")
def _get_ollama_model() -> str:
    """Resolve Ollama model with precedence: env var > local config > public default.

    Local config is loaded from intent_llm.local.json (or intent_llm.json), which is
    ignored by Git and should contain real model names for this machine only.
    """
    # 1. Env var wins
    env_model = os.getenv("WEATHER_INTENT_MODEL", "").strip()
    if env_model:
        return env_model

    # 2. Local config file (ignored by Git — safe for real model names)
    config_path = os.getenv(
        "WEATHER_INTENT_LLM_CONFIG_PATH",
        os.path.join(_package_dir(), "intent_llm.local.json"),
    )
    # Fall back to intent_llm.json if intent_llm.local.json doesn't exist
    fallback_path = os.path.join(_package_dir(), "intent_llm.json")
    if config_path == fallback_path and not os.path.exists(config_path):
        # Try the .local variant first if the plain variant is missing
        local_variant = os.path.join(_package_dir(), "intent_llm.local.json")
        if os.path.exists(local_variant):
            config_path = local_variant

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            if isinstance(file_config, dict):
                ollama_cfg = file_config.get("ollama", {})
                if isinstance(ollama_cfg, dict):
                    model = ollama_cfg.get("model", "").strip()
                    if model:
                        return model
        except Exception as e:
            logger.debug(f"Ollama model config load failed: {e}")

    # 3. Public default (safe to commit)
    return "llama3.2:3b"

_INTENT_LLM_DEFAULT_CONFIG = {
    "enabled": False,
    "provider": "openai-compatible",
    "base_url": "",
    "model": "",
    "api_key_env": "WEATHER_INTENT_LLM_API_KEY",
    "timeout_seconds": 8.0,
}

# ═══════════════════════════════════════════════════════════════════════════
# Intent dataclass
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class WeatherIntent:
    location: str = "home"
    time_reference: str = "now"  # now | today | tomorrow | next-week | YYYY-MM-DD
    intent_type: str = "general"  # general | precipitation | advice | commute | travel_weather | itinerary
    is_commute: bool = False
    is_travel: bool = False
    destination: Optional[str] = None  # free-text destination when is_travel=True
    origin: Optional[str] = None
    departure_time: Optional[str] = None  # HH:MM when parsed
    travel_window_start: Optional[str] = None  # ISO datetime
    travel_window_end: Optional[str] = None  # ISO datetime
    travel_window_label: Optional[str] = None  # HH:MM–HH:MM
    needs_time_clarify: bool = False  # True when travel query has underspecified time
    # Clarification state for multi-turn conversations
    clarification_state: str = "none"  # none | needs_time | needs_destination | needs_locations
    # Best-time outing fields (U7)
    is_best_time_request: bool = False
    target_date: Optional[str] = None  # YYYY-MM-DD or today/tomorrow/sunday etc.
    candidate_window_start: Optional[int] = None  # hour 0-23
    candidate_window_end: Optional[int] = None  # hour 0-23 (exclusive upper bound)
    raw_query: str = ""
    # Itinerary management fields
    itinerary_action: str = "none"  # none | add | remove | list
    itinerary_start_date: Optional[str] = None  # YYYY-MM-DD
    itinerary_end_date: Optional[str] = None  # YYYY-MM-DD
    itinerary_label: Optional[str] = None


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
    "commute": [r"\bcommute\b", r"\bgoing to work\b", r"\bgoing in\b", r"\bgo(?:ing)? into work\b",
                r"\bgo(?:ing)? in to work\b", r"\btraveling to work\b",
                r"\bdriving to work\b", r"\bwalking to work\b", r"\bto the office\b",
                r"\bto my office\b", r"\binto the office\b", r"\binto my office\b",
                r"\bgo(?:ing)? into (?:the |my )?office\b", r"\bto work\b",
                r"\boffice commute\b", r"\bwork commute\b",
                r"\bon my way (?:in|into)(?:\s+(?:work|the office|office))?\b",
                r"\bon my way to (?:work|the office|office)\b"],
    "travel_weather": [r"\bfor a train\b", r"\bto catch a\b", r"\btrain to\b",
                       r"\bplatform\b.*\btomorrow\b", r"\bstation\b.*\btomorrow\b"],
}


# Itinerary management commands (private temporary-location context)
_ITINERARY_ADD_PATTERNS = [
    r"\b(?:add|create|set)\b.*\b(?:itinerary|trip|travel plan)\b",
    r"\b(?:i am|i'm|ill|i'll|we are|we're)\s+(?:in|going to|staying in|visiting)\b",
    r"\b(?:going to|staying in|visiting)\s+.+\s+\b(?:from|between)\b",
]
_ITINERARY_REMOVE_PATTERNS = [
    r"\b(?:remove|delete|clear|cancel)\b.*\b(?:itinerary|trip|travel plan)\b",
    r"\b(?:remove|delete|clear|cancel)\b.+\btrip\b",
]
_ITINERARY_LIST_PATTERNS = [
    r"\b(?:list|show)\b.*\b(?:itinerary|trip|travel plan|upcoming travel)\b",
    r"\b(?:what(?:'s| is)|show me)\b.*\b(?:my itinerary|my trips|upcoming travel)\b",
]
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

def _strip_ordinal_suffix(value: str) -> str:
    return re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_itinerary_dates(query: str, now: Optional[datetime] = None) -> tuple[Optional[str], Optional[str]]:
    """Extract explicit inclusive date ranges for itinerary commands."""
    base = now or datetime.now()
    q = _strip_ordinal_suffix(query)
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|through|until|-|–|—)\s*(\d{4}-\d{2}-\d{2})\b", q, flags=re.IGNORECASE)
    if iso:
        try:
            start = datetime.fromisoformat(iso.group(1)).date()
            end = datetime.fromisoformat(iso.group(2)).date()
        except ValueError:
            return None, None
        if end < start:
            start, end = end, start
        return start.isoformat(), end.isoformat()
    month_re = "|".join(sorted(_MONTHS, key=len, reverse=True))
    same_month = re.search(rf"\b({month_re})\s+(\d{{1,2}})\s*(?:to|through|until|-|–|—)\s*(\d{{1,2}})\b", q, flags=re.IGNORECASE)
    if same_month:
        month = _MONTHS[same_month.group(1).lower()]
        start = _safe_date(base.year, month, int(same_month.group(2)))
        end = _safe_date(base.year, month, int(same_month.group(3)))
        if start and end:
            if end < start:
                end = _safe_date(base.year + 1, month, int(same_month.group(3))) or end
            return start.isoformat(), end.isoformat()
    two_months = re.search(rf"\b({month_re})\s+(\d{{1,2}})\s*(?:to|through|until|-|–|—)\s*({month_re})\s+(\d{{1,2}})\b", q, flags=re.IGNORECASE)
    if two_months:
        start = _safe_date(base.year, _MONTHS[two_months.group(1).lower()], int(two_months.group(2)))
        end = _safe_date(base.year, _MONTHS[two_months.group(3).lower()], int(two_months.group(4)))
        if start and end:
            if end < start:
                end = _safe_date(base.year + 1, end.month, end.day) or end
            return start.isoformat(), end.isoformat()
    return None, None


def _extract_itinerary_location(query: str) -> Optional[str]:
    patterns = [
        r"\b(?:i am|i'm|ill|i'll|we are|we're)\s+(?:in|going to|staying in|visiting)\s+(.+?)(?=\s+from\b|\s+between\b|\s+on\b|\s+\d{4}-\d{2}-\d{2}\b|[?.!,;:]|$)",
        r"\b(?:going to|staying in|visiting)\s+(.+?)(?=\s+from\b|\s+between\b|\s+on\b|\s+\d{4}-\d{2}-\d{2}\b|[?.!,;:]|$)",
        r"\b(?:add|create|set)\b.*\b(?:itinerary|trip|travel plan)\b.*\b(?:for|to|in)\s+(.+?)(?=\s+from\b|\s+between\b|\s+on\b|\s+\d{4}-\d{2}-\d{2}\b|[?.!,;:]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, query, flags=re.IGNORECASE)
        if m:
            cleaned = _clean_extracted_place(_strip_time_suffix(m.group(1)))
            if cleaned:
                return cleaned
    return None


def _extract_itinerary_remove_target(query: str) -> Optional[str]:
    patterns = [
        r"\b(?:remove|delete|clear|cancel)\s+(.+?)\s+(?:trip|itinerary|travel plan)\b",
        r"\b(?:remove|delete|clear|cancel)\s+(?:trip|itinerary|travel plan)\s+(?:for|to|in)?\s*(.+?)(?:[?.!,;:]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, query, flags=re.IGNORECASE)
        if m:
            cleaned = _clean_extracted_place(m.group(1))
            if cleaned and cleaned.lower() not in {"my", "the", "a"}:
                return cleaned
    return None


def _classify_itinerary_intent(query: str) -> Optional[WeatherIntent]:
    q = query.lower()
    if any(re.search(p, q) for p in _ITINERARY_LIST_PATTERNS):
        return WeatherIntent(location="home", time_reference="today", intent_type="itinerary", raw_query=query, itinerary_action="list")
    if any(re.search(p, q) for p in _ITINERARY_REMOVE_PATTERNS):
        target = _extract_itinerary_remove_target(query)
        return WeatherIntent(location=target or "home", time_reference="today", intent_type="itinerary", raw_query=query, itinerary_action="remove", destination=target, itinerary_label=target)
    if any(re.search(p, q) for p in _ITINERARY_ADD_PATTERNS):
        start, end = _extract_itinerary_dates(query)
        destination = _extract_itinerary_location(query)
        return WeatherIntent(
            location=destination or "home",
            time_reference=start or "today",
            intent_type="itinerary",
            is_travel=bool(destination),
            destination=destination,
            raw_query=query,
            itinerary_action="add",
            itinerary_start_date=start,
            itinerary_end_date=end,
            itinerary_label=destination,
        )
    return None

# Travel-related location keywords — detected as travel when combined with non-commute patterns
_TRAVEL_KEYWORDS = [
    r"\bgoing to\b", r"\bheading to\b", r"\bheaded to\b", r"\bon my way to\b", r"\btravelling to\b",
    r"\btraveling to\b", r"\bto get to\b",
    r"\bgotta\s+go\s+to\b", r"\bgotta\s+head\s+to\b",
    r"\bto (?:the )?(?:station|airport|platform|stop)\b",
    r"\bhave\s+gotta\s+go\s+to\b",
    r"\bcoming home from\b", r"\bgoing from\b",
    r"\bheading home\b", r"\bheaded home\b",
]

# Time patterns that are too vague for travel queries without a specific time
_BEST_TIME_PATTERNS = [
    r"\bwhen is the weather best",
    r"\bwhen is the (?:weather )?best time",
    r"\bwhen is the weather the best",
    r"\bbest time.*(?:weather|walk|go|visit|head|leave|commute|office|work)",
    r"\bbest time to(?: walk| go| visit| head| leave| commute)",
    r"\bwhat.*best.*time.*(?:weather|walk|go|visit|leave|commute|office|work)",
    r"\bwhen should i (?:leave|go|head|commute)",
    r"\bwhat time should i (?:leave|go|head|commute)",
]

# Time patterns that are too vague for travel queries without a specific time
_VAGUE_TIME_PATTERNS = [
    r"\bmorning\b", r"\bafternoon\b", r"\bevening\b",
    r"\btonight\b", r"\blater\b",
]

# Places with known Sunday market/special hours — loaded from best_time_windows.json
try:
    _btc = json.loads((_SKILL_DIR / "best_time_windows.json").read_text(encoding="utf-8"))
    _SPECIAL_PLACE_WINDOWS = {k: tuple(v) for k, v in _btc["special_places"].items()}
    _GENERIC_OUTING_WINDOW: tuple[int, int] = tuple(_btc["generic_outing_window"])  # type: ignore[assignment]
except FileNotFoundError:
    _SPECIAL_PLACE_WINDOWS = {
        "columbia flower market": (8, 15),
        "columbia road flower market": (8, 15),
    }
    _GENERIC_OUTING_WINDOW = (9, 18)
except (KeyError, ValueError) as e:
    logger.warning(f"best_time_windows.json malformed: {e}; using hardcoded defaults")
    _SPECIAL_PLACE_WINDOWS = {
        "columbia flower market": (8, 15),
        "columbia road flower market": (8, 15),
    }
    _GENERIC_OUTING_WINDOW = (9, 18)

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
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
    today = datetime.now().date()
    for day_name, target_weekday in _WEEKDAY_NAMES.items():
        if re.search(r"\b" + day_name + r"\b", q):
            days_ahead = (target_weekday - today.weekday()) % 7
            return (today + timedelta(days=days_ahead)).isoformat()
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
    generic_start, generic_end = _GENERIC_OUTING_WINDOW
    if now is not None and date_ref == "today":
        next_hour = now.hour + 1
        if next_hour >= generic_end:
            return (0, 0)
        return (max(generic_start, next_hour), generic_end)
    return (generic_start, generic_end)


def _extract_best_time_destination(query: str) -> Optional[str]:
    """Extract destination for best-time queries without requiring travel intent."""
    for pat in [r"\bwalk to\s+([^,?]+)", r"\bgo visit\s+([^,?]+)", r"\bgo to\s+([^,?]+)", r"\bvisit\s+([^,?]+)", r"\bhead to\s+([^,?]+)"]:
        m = re.search(pat, query, flags=re.IGNORECASE)
        if m:
            dest_candidate = _clean_extracted_place(_strip_time_suffix(m.group(1).strip(" ?,.")))
            if dest_candidate and len(dest_candidate) >= 2:
                return dest_candidate
    return None


def _enrich_intent_from_query(result: WeatherIntent, query: str) -> None:
    """Populate best-time fields that LLM tiers omit from their JSON responses.

    Mutates result in place.
    """
    if not _is_best_time_request(query):
        return
    result.is_best_time_request = True
    extracted_destination = _extract_best_time_destination(query)
    if extracted_destination:
        result.destination = extracted_destination
        result.location = extracted_destination
    target_date = _parse_date_reference(query)
    result.target_date = target_date
    start, end = _get_best_time_candidate_window(result.destination, target_date)
    result.candidate_window_start = start
    result.candidate_window_end = end


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
        destination = _extract_best_time_destination(query)
    if is_best_time and not destination:
        destination = _extract_location(query)
    location = destination or _extract_location(query)
    origin = _extract_origin(query) if (is_travel or is_commute) else None
    # Default origin for travel queries when no explicit origin found
    if is_travel and origin is None:
        origin = "home"
    departure_time = _extract_departure_time(query) if is_travel or _has_specific_time(query) else None
    window = build_travel_window(time_ref, departure_time) if departure_time else (None, None, None)
    candidate_start, candidate_end = (0, 0)
    if is_best_time:
        candidate_start, candidate_end = _get_best_time_candidate_window(destination, target_date or "today")


    # Override intent_type for travel queries
    if is_travel and intent not in ("advice", "precipitation"):
        intent = "travel_weather"

    # Determine clarification state
    needs_clarify = _needs_time_clarification(query, is_best_time=is_best_time)
    clarification_state = "none"
    if needs_clarify:
        clarification_state = "needs_time"
    elif is_travel and not destination:
        clarification_state = "needs_destination"

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
        needs_time_clarify=needs_clarify,
        clarification_state=clarification_state,
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
    today = datetime.now().date()
    for day_name, target_weekday in _WEEKDAY_NAMES.items():
        if re.search(r"\b" + day_name + r"\b", q):
            days_ahead = (target_weekday - today.weekday()) % 7
            return (today + timedelta(days=days_ahead)).isoformat()
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
        r"\bcommute\b", r"\bgoing to work\b", r"\bgoing in\b", r"\bgo(?:ing)? into work\b",
        r"\bgo(?:ing)? in to work\b", r"\btraveling to work\b",
        r"\bdriving to work\b", r"\bwalking to work\b",
        r"\bto the office\b", r"\bto my office\b", r"\binto the office\b",
        r"\binto my office\b", r"\bgo(?:ing)? into (?:the |my )?office\b",
        r"\bto work\b", r"\boffice commute\b", r"\bwork commute\b",
        r"\bon my way (?:in|into)(?:\s+(?:work|the office|office))?\b",
        r"\bon my way to (?:work|the office|office)\b",
        r"\bheading home from (?:the )?(?:office|work)\b",
        r"\bcoming home from (?:the )?(?:office|work)\b",
    ]
    return any(re.search(kw, q) for kw in commute_keywords)


def _is_travel_query(query: str) -> bool:
    """Detect travel queries (going to a place that's not home or office)."""
    q = query.lower()
    if re.search(r"\b(?:wfh|working from home|work from home)\b", q) and re.search(r"\bgo for a walk\b", q):
        return False
    # "coming home from X" is travel (origin=X, destination=home)
    if re.search(r"\bcoming\s+home\s+from\b", q):
        return True
    # "going/heading from X to Y" is travel
    if re.search(r"\b(?:going|heading|headed)\s+from\b", q):
        return True
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

    Also handles "coming home from X" where destination is home.
    """
    q = query.lower()

    # "coming home from X" → destination is home
    if re.search(r"\bcoming\s+home\b", q):
        return "home"

    # "from X to Y" → destination is Y
    m = re.search(r"\bto\s+([a-z][a-z0-9'&.\-\s]{1,40}?)(?=\s+(?:from|at|on|by|for|tomorrow|today|tonight|morning|afternoon|evening|night)\b|[?.!,;:]|$)", q, flags=re.IGNORECASE)
    if m:
        cleaned = _clean_extracted_place(_strip_time_suffix(m.group(1)))
        if cleaned and len(cleaned) >= 2:
            if cleaned.lower() in ("work", "the office", "my office", "office"):
                return "office"
            if cleaned.lower() in ("home", "my place"):
                return "home"
            return cleaned

    # Fallback: generic "to [place]" pattern
    match = re.search(
        r"\bto\s+([a-z][a-z0-9'&.\-\s]{1,80}?)(?=\s+(?:from|at|on|by|for|tomorrow|today|tonight|morning|afternoon|evening|night)\b|[?.!,;:]|$)",
        query,
        flags=re.IGNORECASE,
    )
    if match:
        cleaned = _clean_extracted_place(_strip_time_suffix(match.group(1)))
        if cleaned and len(cleaned) >= 2:
            if cleaned.lower() in ("work", "the office", "my office", "office"):
                return "office"
            if cleaned.lower() in ("home", "my place"):
                return "home"
            return cleaned
    return None



def _extract_origin(query: str) -> Optional[str]:
    """
    Extract origin location with support for natural phrases.

    Handles:
    - "from work/office" → office
    - "from home" → home
    - "coming home from X" → X (destination is home, origin is X)
    - "going from X to Y" → X
    """
    q = query.lower()

    # "coming home from X" → origin is X, not home
    if re.search(r"\bcoming\s+home\s+from\b", q):
        # Extract the place after "from"
        m = re.search(r"\bcoming\s+home\s+from\s+([a-z][a-z0-9'&.\-\s]{1,40}?)(?=\s+(?:at|on|by|for|tomorrow|today|tonight|morning|afternoon|evening|night)\b|[?.!,;:]|$)", q, flags=re.IGNORECASE)
        if m:
            return _clean_extracted_place(m.group(1))
        return None

    # "from X to Y" → origin is X
    m = re.search(r"\bfrom\s+([a-z][a-z0-9'&.\-\s]{1,40}?)\s+to\b", q, flags=re.IGNORECASE)
    if m:
        origin_candidate = _clean_extracted_place(m.group(1))
        if origin_candidate:
            # Normalize work/office aliases
            if origin_candidate.lower() in ("work", "the office", "my office", "office"):
                return "office"
            if origin_candidate.lower() in ("home", "my place"):
                return "home"
            return origin_candidate

    if re.search(r"\bfrom\s+(?:my\s+)?(?:office|work|the office)\b", q):
        return "office"
    if re.search(r"\bfrom\s+(?:home|my place)\b", q):
        return "home"
    return None


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

_INTENT_SYSTEM_PROMPT_FALLBACK = """You are a weather query intent classifier. Parse the user query and output ONLY valid JSON with no markdown, no explanation.

Output format:
{
  "location": "<registry_alias or free-text location>",
  "time_reference": "<now|today|tomorrow|next-week|YYYY-MM-DD>",
  "intent_type": "<general|precipitation|advice|commute|travel_weather|itinerary>",
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

try:
    _INTENT_SYSTEM_PROMPT = (_SKILL_DIR / "intent_system_prompt.txt").read_text(encoding="utf-8")
except FileNotFoundError:
    _INTENT_SYSTEM_PROMPT = _INTENT_SYSTEM_PROMPT_FALLBACK


# ═══════════════════════════════════════════════════════════════════════════
# Model providers (fallback chain)
# ═══════════════════════════════════════════════════════════════════════════

def _normalise_model_intent(result: WeatherIntent, query: str) -> WeatherIntent:
    """Fill safety-critical fields that model tiers often omit or misclassify.

    Model output is still useful for open-ended/general location queries, but
    structured personal-weather flows need deterministic guarantees for origin,
    destination, time windows, and clarification state.
    """
    deterministic = _deterministic_classify(query)
    if deterministic.is_best_time_request or deterministic.is_travel or deterministic.is_commute:
        return deterministic
    result.raw_query = query
    _enrich_intent_from_query(result, query)
    if result.location is None:
        result.location = _extract_location(query)
    if result.time_reference is None:
        result.time_reference = _extract_time(query)
    return result


def _model_classify(query: str, clean_query: str) -> Optional[WeatherIntent]:
    """Run configured model providers in order and normalise their result."""
    providers: tuple[Callable[[str], Optional[str]], ...] = (
        _call_configured_llm,
        _call_omniroute,
        _call_anthropic,
        _call_ollama,
    )
    for provider in providers:
        response = provider(clean_query)
        if not response:
            continue
        result = _parse_intent_response(response)
        if result:
            return _normalise_model_intent(result, query)
    return None


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
            itinerary_action=data.get("itinerary_action", "none"),
            itinerary_start_date=data.get("itinerary_start_date"),
            itinerary_end_date=data.get("itinerary_end_date"),
            itinerary_label=data.get("itinerary_label"),
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.debug(f"Intent parse failed: {e}")
        return None


# ── Tier 1: Configured OpenAI-compatible LLM endpoint ─────────────────────

def _truthy(value: object) -> bool:
    """Return True for common string/boolean truthy values."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _package_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _load_intent_llm_config() -> dict:
    """Load optional OpenAI-compatible intent LLM configuration.

    Environment variables override JSON config. The default state is disabled so
    public installs never call a private endpoint unless explicitly configured.
    """
    config = dict(_INTENT_LLM_DEFAULT_CONFIG)
    config_path = os.getenv(
        "WEATHER_INTENT_LLM_CONFIG_PATH",
        os.path.join(_package_dir(), "intent_llm.json"),
    )

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            if isinstance(file_config, dict):
                config.update(file_config)
        except Exception as e:
            logger.debug(f"Intent LLM config load failed: {e}")

    env_map = {
        "enabled": "WEATHER_INTENT_LLM_ENABLED",
        "base_url": "WEATHER_INTENT_LLM_BASE_URL",
        "model": "WEATHER_INTENT_LLM_MODEL",
        "api_key_env": "WEATHER_INTENT_LLM_API_KEY_ENV",
        "timeout_seconds": "WEATHER_INTENT_LLM_TIMEOUT_SECONDS",
    }
    for key, env_name in env_map.items():
        value = os.getenv(env_name)
        if value not in (None, ""):
            config[key] = value

    if os.getenv("WEATHER_INTENT_LLM_API_KEY"):
        config["api_key_env"] = "WEATHER_INTENT_LLM_API_KEY"

    if isinstance(config.get("timeout_seconds"), str):
        try:
            config["timeout_seconds"] = float(config["timeout_seconds"])
        except ValueError:
            config["timeout_seconds"] = _INTENT_LLM_DEFAULT_CONFIG["timeout_seconds"]

    config["enabled"] = _truthy(config.get("enabled"))
    config["base_url"] = str(config.get("base_url") or "").rstrip("/")
    config["model"] = str(config.get("model") or "")
    config["api_key_env"] = str(config.get("api_key_env") or "WEATHER_INTENT_LLM_API_KEY")
    return config


def _call_configured_llm(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call a configured OpenAI-compatible chat completions endpoint."""
    config = _load_intent_llm_config()
    if not config.get("enabled"):
        return None

    base_url = config.get("base_url", "")
    model = config.get("model", "")
    api_key_env = config.get("api_key_env", "WEATHER_INTENT_LLM_API_KEY")
    api_key = os.getenv(api_key_env, "")
    request_timeout = float(config.get("timeout_seconds") or timeout)

    if not base_url or not model or not api_key:
        logger.debug("Configured intent LLM is enabled but missing base_url, model, or API key")
        return None

    import urllib.request

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {prompt}"}
        ],
        "temperature": 0.1,
        "max_tokens": 150,
        "stream": False,
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=request_timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"Configured intent LLM classify failed: {e}")
        return None


# ── Tier 2: OmniRoute (routed GPT 5.4-mini) ─────────────────────────────────

def _call_omniroute(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call GPT 5.4-mini via OmniRoute. Returns None on failure."""
    import urllib.request

    model = os.getenv("WEATHER_INTENT_OMNIROUTE_MODEL", "omniroute/cx/gpt-5.4-mini").strip()
    if not model:
        logger.debug("No OmniRoute model configured, skipping")
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {prompt}"}
        ],
        "temperature": 0.1,
        "max_tokens": 150,
    }

    try:
        req = urllib.request.Request(
            OMNIROUTE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"OmniRoute intent classify failed: {e}")
        return None


# ── Tier 3: Claude Haiku (Anthropic) ─────────────────────────────────────

def _call_anthropic(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call Claude Haiku via OmniRoute. Returns None on failure."""
    import urllib.request

    model = os.getenv("WEATHER_INTENT_OMNIROUTE_MODEL", "omniroute/cx/gpt-5.4-mini").strip()
    # Swap to Claude Haiku via OmniRoute if preferred
    haiku_model = "omniroute/cc/claude-haiku-4.5-20251001"

    payload = {
        "model": haiku_model,
        "messages": [
            {"role": "user", "content": f"{_INTENT_SYSTEM_PROMPT}\n\nQuery: {prompt}"}
        ],
        "temperature": 0.1,
        "max_tokens": 150,
    }

    try:
        req = urllib.request.Request(
            OMNIROUTE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug(f"OmniRoute Claude Haiku classify failed: {e}")
        return None


# ── Tier 4: Ollama (local fallback) ──────────────────────────────────────

def _call_ollama(prompt: str, timeout: float = 8.0) -> Optional[str]:
    """Call Ollama for intent classification. Returns None on failure."""
    import urllib.request

    model = _get_ollama_model()
    payload = {
        "model": model,
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

    Tries in order: configured OpenAI-compatible LLM endpoint → GPT 5.4-mini via OmniRoute → Claude Haiku via OmniRoute → Gemma4 via Ollama → deterministic.

    Args:
        query: natural-language weather query

    Returns:
        WeatherIntent with location, time_reference, intent_type, is_commute

    No direct OpenAI or Anthropic API calls are made. All external model calls are routed
    through OmniRoute (http://127.0.0.1:20128) unless a custom endpoint is explicitly
    configured in intent_llm.local.json.
    """
    # Itinerary management is privacy/stateful; keep it deterministic and local.
    itinerary_intent = _classify_itinerary_intent(query)
    if itinerary_intent:
        return itinerary_intent

    # Strip common prefixes
    clean_query = re.sub(r"^(weather|what's the weather|forecast)\s*", "", query.lower())
    clean_query = clean_query.strip("?.,!")

    result = _model_classify(query, clean_query)
    if result:
        return result

    # Final fallback: deterministic
    logger.debug("All model providers failed, using deterministic fallback")
    return _deterministic_classify(query)
