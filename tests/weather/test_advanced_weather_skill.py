from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather.intent_classifier import WeatherIntent, _deterministic_classify, _parse_intent_response
from weather.skill import get_weather_chat
from weather.weather_models import CurrentConditions, DailyForecast, WeatherData


def test_travel_train_query_asks_for_specific_time_and_preserves_destination():
    intent = _deterministic_classify("I've gotta go to Paddington tomorrow morning for a train")

    assert intent.is_travel is True
    assert intent.is_commute is False
    assert intent.destination == "Paddington"
    assert intent.needs_time_clarify is True
    assert intent.intent_type == "travel_weather"


def test_specific_travel_time_does_not_pollute_destination_with_time_marker():
    intent = _deterministic_classify("I've gotta go to Paddington at 8:30 tomorrow")

    assert intent.is_travel is True
    assert intent.destination == "Paddington"
    assert intent.needs_time_clarify is False


def test_free_text_place_with_time_is_extracted_as_location_not_home():
    intent = _deterministic_classify("Tribe Waterloo at 9am Tuesday")

    assert intent.location == "Tribe Waterloo"
    assert intent.is_travel is False
    assert intent.needs_time_clarify is False


def test_weather_in_free_text_place_is_extracted_as_location():
    intent = _deterministic_classify("Weather in Mayfair tomorrow")

    assert intent.location == "Mayfair"
    assert intent.time_reference == "tomorrow"


def test_wfh_acronym_resolves_to_home_context_not_free_text_location():
    intent = _deterministic_classify("What's the weather tomorrow? I'm wfh but would like to go for a walk")

    assert intent.location == "home"
    assert intent.is_commute is False
    assert intent.is_travel is False
    assert intent.time_reference == "tomorrow"


def test_working_from_home_resolves_to_home_context():
    intent = _deterministic_classify("What's the weather tomorrow? I'm working from home and want a walk")

    assert intent.location == "home"
    assert intent.is_commute is False
    assert intent.is_travel is False
    assert intent.time_reference == "tomorrow"


def test_on_my_way_to_non_work_destination_is_travel_not_commute():
    intent = _deterministic_classify("Do I need an umbrella on my way to Tribe Waterloo?")

    assert intent.is_travel is True
    assert intent.is_commute is False
    assert intent.destination == "Tribe Waterloo"
    assert intent.origin == "home"
    assert intent.intent_type == "advice"


def test_headed_to_destination_from_office_extracts_origin_and_window():
    intent = _deterministic_classify("I'm headed to Tribe Waterloo from the office at 5:30")

    assert intent.is_travel is True
    assert intent.is_commute is False
    assert intent.destination == "Tribe Waterloo"
    assert intent.origin == "office"
    assert intent.departure_time == "17:30"
    assert intent.travel_window_label == "17:30–18:30"


def test_on_my_way_to_office_remains_commute():
    intent = _deterministic_classify("I'm on my way to the office")

    assert intent.is_commute is True
    assert intent.is_travel is False
    assert intent.destination is None


def test_on_my_way_into_work_remains_commute():
    intent = _deterministic_classify("I'm on my way into work")

    assert intent.is_commute is True
    assert intent.is_travel is False


def test_model_parser_preserves_new_travel_fields():
    intent = _parse_intent_response(
        '{"location":"home","time_reference":"tomorrow","intent_type":"travel_weather",'
        '"is_commute":false,"is_travel":true,"destination":"Paddington",'
        '"needs_time_clarify":true}'
    )

    assert intent == WeatherIntent(
        location="home",
        time_reference="tomorrow",
        intent_type="travel_weather",
        is_commute=False,
        is_travel=True,
        destination="Paddington",
        needs_time_clarify=True,
    )


def test_skill_inline_clarification_for_underspecified_travel(monkeypatch):
    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(
            location="home",
            time_reference="tomorrow",
            intent_type="travel_weather",
            is_travel=True,
            destination="Paddington",
            needs_time_clarify=True,
        ),
    )

    assert get_weather_chat("irrelevant") == "⏰ What time are you leaving for Paddington tomorrow?"


def _dummy_weather(label: str = "Mayfair") -> WeatherData:
    now = datetime(2026, 5, 3, 9, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=51.51,
        longitude=-0.13,
        timezone="Europe/London",
        current=CurrentConditions(
            temperature=15,
            feels_like=15,
            condition="Clear sky",
            humidity=50,
            wind_speed=5,
            wind_gust=None,
            precipitation_mm=0,
            uv_index=3,
        ),
        today=DailyForecast(date=now.date(), high=18, low=10, condition="Clear sky", precip_probability=0),
        hourly_precipitation=[],
        alerts=[],
        source="test",
    )


def test_skill_uses_travel_handler_for_on_my_way_without_specific_time(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(
            location="Tribe Waterloo",
            intent_type="advice",
            is_travel=True,
            destination="Tribe Waterloo",
            origin="home",
        ),
    )
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: calls.append(loc) or _dummy_weather(loc))

    result = get_weather_chat("irrelevant")

    assert calls == ["Tribe Waterloo", "home"]
    assert "Origin: Home" in result
    assert "Destination: Tribe Waterloo" in result
    assert "Travel umbrella" in result


def test_travel_response_preserves_weather_based_advisories_beyond_window(monkeypatch):
    now = datetime(2026, 5, 3, 9, tzinfo=timezone.utc)

    def weather_for(loc: str) -> WeatherData:
        if loc == "home":
            return WeatherData(
                location_label="home",
                latitude=51.5014,
                longitude=-0.1419,
                timezone="Europe/London",
                current=CurrentConditions(
                    temperature=-1,
                    feels_like=-2,
                    condition="Clear sky",
                    humidity=45,
                    wind_speed=8,
                    wind_gust=None,
                    precipitation_mm=0,
                    uv_index=1,
                ),
                today=DailyForecast(date=now.date(), high=3, low=-3, condition="Clear sky", precip_probability=0),
                hourly_precipitation=[],
                alerts=[],
                source="test",
            )

        return WeatherData(
            location_label="Tribe Waterloo",
            latitude=51.5,
            longitude=-0.11,
            timezone="Europe/London",
            current=CurrentConditions(
                temperature=31,
                feels_like=32,
                condition="Clear sky",
                humidity=50,
                wind_speed=45,
                wind_gust=None,
                precipitation_mm=0,
                uv_index=7,
            ),
            today=DailyForecast(date=now.date(), high=33, low=19, condition="Clear sky", precip_probability=60),
            hourly_precipitation=[],
            alerts=[],
            source="test",
        )

    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(
            location="Tribe Waterloo",
            intent_type="advice",
            is_travel=True,
            destination="Tribe Waterloo",
            origin="home",
            travel_window_label="17:30–18:30",
        ),
    )
    monkeypatch.setattr("weather.skill.get_weather", weather_for)

    result = get_weather_chat("irrelevant")

    assert "☔ Umbrella: bring one — rain expected (60% chance)" in result
    assert "🕶️ Sunglasses: UV index 7" in result
    assert "🔥 Heat caution: feels like 32°C" in result
    assert "🥶 Cold alert: feels like -2°C" in result
    assert "💨 Wind caution — wind 45 km/h" in result
    assert "☔ **Travel umbrella:** Not needed for 17:30–18:30 at origin or destination." in result


def test_chat_formatter_includes_no_umbrella_decision_without_emoji_soup(monkeypatch):
    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(location="Mayfair", time_reference="today", intent_type="general"),
    )
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: _dummy_weather(loc))
    monkeypatch.setattr("weather.skill.resolve_location", lambda loc: (None, type("Geo", (), {"name": loc})()))

    result = get_weather_chat("Weather in Mayfair today")

    assert "☔ Umbrella: not needed based on current forecast" in result
    assert "🌡️" in result
    assert "☀️ Clear sky" in result
    assert "💨" not in result
    assert "🕶️" not in result


def test_chat_formatter_condition_emojis_distinguish_sun_rain_overcast(monkeypatch):
    cases = [
        ("Clear sky", 0, "☀️ Clear sky"),
        ("Overcast clouds", 0, "☁️ Overcast clouds"),
        ("Light rain", 20, "🌧️ Light rain"),
        ("Cloudy", 60, "🌧️ Cloudy"),
    ]

    for condition, precip_probability, expected in cases:
        weather = _dummy_weather("Mayfair")
        weather.current.condition = condition
        weather.current.precipitation_mm = 1 if "rain" in condition.lower() else 0
        weather.today.condition = condition
        weather.today.precip_probability = precip_probability
        monkeypatch.setattr(
            "weather.skill.classify_intent",
            lambda _: WeatherIntent(location="Mayfair", time_reference="today", intent_type="general"),
        )
        monkeypatch.setattr("weather.skill.get_weather", lambda loc, weather=weather: weather)
        monkeypatch.setattr("weather.skill.resolve_location", lambda loc: (None, type("Geo", (), {"name": loc})()))

        result = get_weather_chat("Weather in Mayfair today")

        assert expected in result


def test_commute_uses_home_office_and_umbrella_label(monkeypatch):
    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(location="office", time_reference="tomorrow", intent_type="commute", is_commute=True),
    )
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: _dummy_weather(loc))
    monkeypatch.setattr("weather.skill.commute_umbrella_check", lambda *args, **kwargs: (False, []))

    result = get_weather_chat("Do I need an umbrella when I go to the office tomorrow?")

    assert "🏠 **Home**" in result
    assert "🏢 **Office**" in result
    assert "☔ **Commute umbrella:** Not needed during morning commute" in result


def test_skill_uses_extracted_free_text_location(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "weather.skill.classify_intent",
        lambda _: WeatherIntent(location="Mayfair", time_reference="tomorrow", intent_type="general"),
    )
    monkeypatch.setattr("weather.skill.get_weather", lambda loc: calls.append(loc) or _dummy_weather(loc))
    monkeypatch.setattr("weather.skill.resolve_location", lambda loc: (None, type("Geo", (), {"name": loc})()))

    result = get_weather_chat("Weather in Mayfair tomorrow")

    assert calls == ["Mayfair"]
    assert "Mayfair" in result
