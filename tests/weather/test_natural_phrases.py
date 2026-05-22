"""
test_natural_phrases.py — Regression tests for natural commute/travel/rain/best-time phrasings.

Covers:
- Commute phrasings (going into work, to the office, etc.)
- Travel phrasings (from X to Y, coming home from X, etc.)
- Rain/umbrella phrasings
- Best-time phrasings
- Clarification state for vague requests
"""

from __future__ import annotations

from weather.intent_classifier import _deterministic_classify


# ── Commute phrasings ───────────────────────────────────────────────────────

def test_commute_phrases():
    phrases = [
        "what is the weather for going into work tomorrow?",
        "do I need an umbrella going into the office tomorrow?",
        "what's the weather for my commute to my office?",
        "I'm on my way to the office",
        "I'm on my way into work",
        "Do I need an umbrella when I go to the office tomorrow?",
    ]
    for phrase in phrases:
        intent = _deterministic_classify(phrase)
        assert intent.is_commute is True, f"Failed for: {phrase}"
        assert intent.is_travel is False, f"Failed for: {phrase}"


# ── Travel phrasings ────────────────────────────────────────────────────────

def test_travel_phrases():
    phrases = [
        ("I'm going to Tribe Waterloo at 9am", "Tribe Waterloo"),
        ("I'm headed to Paddington tomorrow at 8:30", "Paddington"),
        ("I'm coming home from Tribe Waterloo at 5:30", "home"),
        ("I'm going from work to Tribe Waterloo at 5:30", "Tribe Waterloo"),
        ("I'm going from home to work at 8:30", "office"),
    ]
    for phrase, expected_dest in phrases:
        intent = _deterministic_classify(phrase)
        assert intent.is_travel is True, f"Failed for: {phrase}"
        assert intent.destination == expected_dest, f"Expected {expected_dest}, got {intent.destination} for: {phrase}"


def test_travel_with_origin():
    intent = _deterministic_classify("I'm headed to Tribe Waterloo from the office at 5:30")
    assert intent.is_travel is True
    assert intent.origin == "office"
    assert intent.destination == "Tribe Waterloo"


def test_coming_home_from_place():
    intent = _deterministic_classify("I'm coming home from Tribe Waterloo at 5:30")
    assert intent.is_travel is True
    assert intent.origin == "Tribe Waterloo"
    assert intent.destination == "home"


# ── Vague travel needs clarification ────────────────────────────────────────

def test_vague_travel_needs_time_clarification():
    phrases = [
        "I've gotta go to Paddington tomorrow morning",
        "I'm going to Tribe Waterloo tomorrow afternoon",
        "I'm headed to Paddington tomorrow evening",
    ]
    for phrase in phrases:
        intent = _deterministic_classify(phrase)
        assert intent.needs_time_clarify is True, f"Failed for: {phrase}"
        assert intent.clarification_state == "needs_time", f"Failed for: {phrase}"


def test_specific_travel_no_clarification():
    intent = _deterministic_classify("I've gotta go to Paddington at 8:30 tomorrow")
    assert intent.needs_time_clarify is False
    assert intent.clarification_state == "none"


# ── Best-time phrasings ─────────────────────────────────────────────────────

def test_best_time_phrases():
    phrases = [
        "we want to walk to St Paul's today, when is the weather the best for that?",
        "when is the best time to go to the office tomorrow?",
        "what time should I leave for my commute to the office?",
    ]
    for phrase in phrases:
        intent = _deterministic_classify(phrase)
        assert intent.is_best_time_request is True, f"Failed for: {phrase}"


# ── Rain/umbrella phrasings ─────────────────────────────────────────────────

def test_rain_phrases():
    phrases = [
        "do I need an umbrella?",
        "will it rain today?",
        "should I bring a raincoat?",
    ]
    for phrase in phrases:
        intent = _deterministic_classify(phrase)
        assert intent.intent_type in ("advice", "precipitation"), f"Failed for: {phrase}"


# ── Non-commute travel detection ────────────────────────────────────────────

def test_on_my_way_to_non_work_is_travel():
    intent = _deterministic_classify("Do I need an umbrella on my way to Tribe Waterloo?")
    assert intent.is_travel is True
    assert intent.is_commute is False
    assert intent.destination == "Tribe Waterloo"


def test_wfh_not_travel():
    intent = _deterministic_classify("What's the weather tomorrow? I'm wfh but would like to go for a walk")
    assert intent.location == "home"
    assert intent.is_commute is False
    assert intent.is_travel is False
