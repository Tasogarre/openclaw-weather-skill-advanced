from __future__ import annotations

import json

from weather.intent_classifier import classify_intent
from weather.skill import get_weather_chat
from weather.itinerary import list_entries


def test_classify_itinerary_add_explicit_dates():
    intent = classify_intent("I'm going to Santiago from June 1-5")
    assert intent.intent_type == "itinerary"
    assert intent.itinerary_action == "add"
    assert intent.destination == "Santiago"
    assert intent.itinerary_start_date.endswith("-06-01")
    assert intent.itinerary_end_date.endswith("-06-05")


def test_itinerary_add_list_remove_flow(monkeypatch, tmp_path):
    path = tmp_path / "itinerary.json"
    monkeypatch.setenv("WEATHER_ITINERARY_PATH", str(path))

    added = get_weather_chat("I'm going to Santiago from June 1-5")
    assert "Added travel context" in added
    assert list_entries(path)[0].location == "Santiago"

    listed = get_weather_chat("What's my itinerary?")
    assert "Santiago" in listed

    removed = get_weather_chat("Remove Santiago trip")
    assert "Removed travel context" in removed
    assert list_entries(path) == []
