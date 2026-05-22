from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from weather.intent_classifier import _deterministic_classify
from weather.evaluation_context import build_evaluation_context
from weather.skill import get_weather_chat, get_weather_briefing_commute
from weather.weather_models import CurrentConditions, DailyForecast, HourlyPrecipitation, WeatherData


def _weather(label: str, probs: dict[int, int], *, day_num: int = 21) -> WeatherData:
    day = datetime(2026, 5, day_num, tzinfo=timezone.utc)
    return WeatherData(
        location_label=label,
        latitude=51.5,
        longitude=-0.1,
        timezone="Europe/London",
        current=CurrentConditions(
            temperature=15,
            feels_like=15,
            condition="Cloudy",
            humidity=60,
            wind_speed=5,
            precipitation_mm=0,
            uv_index=2,
        ),
        today=DailyForecast(date=day.date(), high=18, low=11, condition="Cloudy", precip_probability=max(probs.values() or [0])),
        hourly_precipitation=[
            HourlyPrecipitation(time=day.replace(hour=hour), probability=prob)
            for hour, prob in probs.items()
        ],
        alerts=[],
        source="test",
    )


def test_expanded_office_commute_phrasing_is_detected():
    examples = [
        "what is the weather for going into work tomorrow?",
        "do I need an umbrella going into the office tomorrow?",
        "what's the weather for my commute to my office?",
    ]

    for query in examples:
        intent = _deterministic_classify(query)
        assert intent.is_commute is True
        assert intent.is_travel is False


def test_commute_umbrella_decision_is_first(monkeypatch):
    monkeypatch.setattr(
        "weather.skill.get_weather",
        lambda loc: _weather("Home" if loc == "home" else "Office", {9: 10} if loc == "home" else {9: 80}, day_num=22),
    )

    fixed_now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

    def fixed_context(intent, **kwargs):
        return build_evaluation_context(intent, now=fixed_now, **kwargs)

    monkeypatch.setattr("weather.skill.build_evaluation_context", fixed_context)

    result = get_weather_chat("Do I need an umbrella going into the office tomorrow?")

    first_line = result.splitlines()[0]
    assert first_line.startswith("🌧️ **Rain decision:** Bring an umbrella")
    assert "☂️ **Umbrella:** yes" in result
    assert result.index("Rain decision") < result.index("🏠 **Home**")


def test_commute_best_time_to_leave_scores_home_and_office(monkeypatch):
    monkeypatch.setattr(
        "weather.skill.get_weather",
        lambda loc: _weather("Home" if loc == "home" else "Office", {9: 20, 10: 5, 11: 40} if loc == "home" else {9: 10, 10: 15, 11: 20}, day_num=22),
    )

    fixed_now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

    def fixed_context(intent, **kwargs):
        return build_evaluation_context(intent, now=fixed_now, **kwargs)

    monkeypatch.setattr("weather.skill.build_evaluation_context", fixed_context)

    result = get_weather_chat("When should I leave for my commute to the office tomorrow?")

    assert result.splitlines()[0].startswith("🌧️ **Best time to leave:** 10:00")
    assert "☂️ **Umbrella:** probably not needed" in result
    assert "📍 **Checked:** Home + Office" in result


def test_commute_decision_uses_requested_date_window(monkeypatch):
    def fake_weather(loc):
        # Today is rainy at 09:00, tomorrow is dry at 09:00. A time-only window
        # check would incorrectly recommend an umbrella for tomorrow.
        weather = _weather("Home" if loc == "home" else "Office", {9: 90}, day_num=21)
        weather.hourly_precipitation.extend(_weather(weather.location_label, {9: 5}, day_num=22).hourly_precipitation)
        return weather

    monkeypatch.setattr("weather.skill.get_weather", fake_weather)

    result = get_weather_chat("Do I need an umbrella going into the office tomorrow?")

    assert result.splitlines()[0].startswith("🌧️ **Rain decision:** No umbrella needed")
    assert "☂️ **Umbrella:** no" in result


def test_weekday_commute_phrase_resolves_to_iso_date(monkeypatch):
    """Named weekdays should be concrete dates, not treated as 'now'."""
    from weather import intent_classifier

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
            return base if tz else base.replace(tzinfo=None)

    monkeypatch.setattr(intent_classifier, "datetime", FixedDateTime)

    intent = _deterministic_classify("going in on Thursday because of weather?")

    assert intent.is_commute is True
    assert intent.is_travel is False
    assert intent.time_reference == "2026-05-28"
    assert intent.clarification_state == "none"


def test_commute_briefing_uses_context_window_for_tomorrow(monkeypatch):
    """Rain outside tomorrow's commute window should not trigger briefing umbrella advice."""
    fixed_now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

    def fixed_context(intent, **kwargs):
        return build_evaluation_context(intent, now=fixed_now, **kwargs)

    def fake_weather(loc):
        # Tomorrow has heavy rain outside the default 08:30–11:30 commute window
        # and low rain inside it. A whole-day check would incorrectly advise umbrella.
        return _weather("Home" if loc == "home" else "Office", {9: 5, 14: 90}, day_num=23)

    monkeypatch.setattr("weather.skill.build_evaluation_context", fixed_context)
    monkeypatch.setattr("weather.skill.get_weather", fake_weather)

    result = get_weather_briefing_commute("Do I need an umbrella going into the office tomorrow?")

    assert "**Commute:** No rain expected during morning commute." in result
    assert "**Commute:** Rain expected" not in result


def test_commute_briefing_flags_rain_at_office_inside_window(monkeypatch):
    """Rain at either Home or Office inside the commute window should trigger advice."""
    fixed_now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

    def fixed_context(intent, **kwargs):
        return build_evaluation_context(intent, now=fixed_now, **kwargs)

    def fake_weather(loc):
        if loc == "office":
            return _weather("Office", {9: 80}, day_num=23)
        return _weather("Home", {9: 5}, day_num=23)

    monkeypatch.setattr("weather.skill.build_evaluation_context", fixed_context)
    monkeypatch.setattr("weather.skill.get_weather", fake_weather)

    result = get_weather_briefing_commute("Do I need an umbrella going into the office tomorrow?")

    assert "**Commute:** Rain expected during morning commute — bring umbrella." in result


def test_commute_briefing_default_query_uses_morning_commute(monkeypatch):
    """Scheduled briefing path with no raw query should default to morning commute semantics."""
    fixed_now = datetime(2026, 5, 22, 6, 0, tzinfo=timezone.utc)

    def fixed_context(intent, **kwargs):
        return build_evaluation_context(intent, now=fixed_now, **kwargs)

    monkeypatch.setattr("weather.skill.build_evaluation_context", fixed_context)
    monkeypatch.setattr(
        "weather.skill.get_weather",
        lambda loc: _weather("Home" if loc == "home" else "Office", {9: 5}, day_num=22),
    )

    result = get_weather_briefing_commute("")

    assert "**Home:**" in result
    assert "**Office:**" in result
    assert "**Commute:** No rain expected during morning commute." in result
