"""
weather_models.py — Internal structured schema for Weather Skill v1

All weather data from external APIs is normalised into these dataclasses
before being passed to advice engines or formatters.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class CurrentConditions:
    # Required core fields (no defaults — must come first)
    temperature: float          # °C
    feels_like: float           # °C
    condition: str              # e.g. "Clear", "Light rain"
    # Optional fields (with defaults)
    condition_code: Optional[int] = None
    wind_speed: float = 0.0    # km/h
    wind_gust: Optional[float] = None
    wind_direction: Optional[str] = None
    precipitation_mm: float = 0.0
    humidity: Optional[int] = None
    cloud_cover: Optional[int] = None
    uv_index: Optional[int] = None
    visibility_km: Optional[float] = None
    pressure_hPa: Optional[float] = None


@dataclass
class DailyForecast:
    date: date
    high: float      # °C max
    low: float       # °C min
    condition: str
    condition_code: Optional[int] = None
    precip_probability: int = 0   # 0–100 %
    precip_mm: float = 0.0
    wind_speed: float = 0.0        # km/h
    wind_gust: Optional[float] = None
    uv_index_max: Optional[int] = None
    sunrise: Optional[str] = None  # ISO-8601 time local
    sunset: Optional[str] = None  # ISO-8601 time local


@dataclass
class HourlyPrecipitation:
    time: datetime
    probability: int = 0  # 0–100 %
    mm: float = 0.0


@dataclass
class HourlyForecast:
    """Hourly temperature and conditions (for tonight's low calculation)."""
    time: datetime
    temperature: float  # °C
    feels_like: float   # °C
    condition: str = ""
    precipitation_probability: int = 0  # 0–100 %


@dataclass
class WeatherAlert:
    """A weather alert or warning (OpenWeatherMap only; empty list when using Open-Meteo)."""
    event: str                    # e.g. "Heat Warning", "Thunderstorm"
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    description: str = ""
    sender_name: Optional[str] = None   # None when sender == event


@dataclass
class WeatherData:
    """Root structured weather object."""
    location_label: str
    latitude: float
    longitude: float
    timezone: str
    current: Optional[CurrentConditions] = None
    today: Optional[DailyForecast] = None
    tomorrow: Optional[DailyForecast] = None
    day_after: Optional[DailyForecast] = None
    daily_forecast: list[DailyForecast] = field(default_factory=list)
    hourly_precipitation: list[HourlyPrecipitation] = field(default_factory=list)
    hourly_forecast: list[HourlyForecast] = field(default_factory=list)
    alerts: list[WeatherAlert] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.now)
    source: str = "open-meteo"

    def has_usable_data(self) -> bool:
        return self.current is not None or self.today is not None or bool(self.daily_forecast)

    def sync_legacy_daily_fields(self) -> None:
        """Populate today/tomorrow/day_after from daily_forecast for legacy callers."""
        if self.daily_forecast:
            self.today = self.daily_forecast[0]
        if len(self.daily_forecast) > 1:
            self.tomorrow = self.daily_forecast[1]
        if len(self.daily_forecast) > 2:
            self.day_after = self.daily_forecast[2]
