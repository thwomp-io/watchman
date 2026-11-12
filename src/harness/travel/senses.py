"""Live reads about a place, all keyless: the daily forecast, air quality, recent earthquakes."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from harness.travel.models import AirQualityReport, SeismicReport, WeatherForecast
from harness.travel.providers import (
    get_air_quality_provider,
    get_earthquake_provider,
    get_weather_provider,
)
from harness.travel.providers.air_quality_provider import aqi_category
from harness.travel.providers.base import ProviderError
from harness.travel.providers.geocoding import geocode, geocode_label

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def get_weather(svc: TravelService, city: str,
    start_date: date,
    end_date: date,
    *,
    fahrenheit: bool = True,
    provider_name: str = "open-meteo",
) -> WeatherForecast:
    """Live daily forecast for `city` over [start_date, end_date]. Keyless (Open-Meteo):
    geocode the name → fetch the daily forecast. Raises ProviderError if the city can't be
    resolved or the window is past the forecast horizon."""
    provider = get_weather_provider(provider_name)
    loc = provider.geocode(city)
    if loc is None:
        raise ProviderError(f"no location match for {city!r}")
    days, tz = provider.daily_forecast(
        loc.latitude, loc.longitude, start_date, end_date, fahrenheit=fahrenheit
    )
    label = ", ".join(p for p in (loc.name, loc.admin1, loc.country_code) if p)
    return WeatherForecast(
        location=label,
        latitude=loc.latitude,
        longitude=loc.longitude,
        timezone=tz or (loc.timezone or ""),
        temperature_unit="°F" if fahrenheit else "°C",
        precipitation_unit="inch" if fahrenheit else "mm",
        wind_unit="mph" if fahrenheit else "km/h",
        days=days,
    )


def get_air_quality(svc: TravelService, city: str,
    start_date: date,
    end_date: date,
    *,
    provider_name: str = "open-meteo-aq",
) -> AirQualityReport:
    """Daily air-quality (US AQI / PM2.5 max) for `city` over a window — the wildfire-smoke sense.
    Keyless (Open-Meteo). Raises ProviderError if the city can't be resolved."""
    loc = geocode(city)
    if loc is None:
        raise ProviderError(f"no location match for {city!r}")
    days, tz, current = get_air_quality_provider(provider_name).daily_air_quality(
        loc.latitude, loc.longitude, start_date, end_date
    )

    return AirQualityReport(
        location=geocode_label(loc),
        latitude=loc.latitude,
        longitude=loc.longitude,
        timezone=tz or (loc.timezone or ""),
        current_us_aqi=current,
        current_category=aqi_category(current),
        days=days,
    )


def get_earthquakes(svc: TravelService, city: str,
    *,
    radius_km: int = 300,
    days_back: int = 90,
    min_magnitude: float = 2.5,
    limit: int = 20,
    provider_name: str = "usgs",
) -> SeismicReport:
    """Recent earthquakes within radius_km of `city` over the last `days_back` — the geological-
    screen sense (USGS, keyless). Calibrates the screen with real data, not categorical priors."""

    loc = geocode(city)
    if loc is None:
        raise ProviderError(f"no location match for {city!r}")
    since = date.today() - timedelta(days=days_back)
    quakes = get_earthquake_provider(provider_name).recent_quakes(
        loc.latitude, loc.longitude,
        radius_km=radius_km, since=since, min_magnitude=min_magnitude, limit=limit,
    )
    return SeismicReport(
        location=geocode_label(loc),
        latitude=loc.latitude,
        longitude=loc.longitude,
        radius_km=radius_km,
        since=since.isoformat(),
        min_magnitude=min_magnitude,
        count=len(quakes),
        quakes=quakes,
    )
