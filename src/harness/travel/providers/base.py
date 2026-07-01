"""Provider abstraction (structural Protocols, so providers are swappable for comparison)."""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from harness.errors import ProviderError  # re-exported for back-compat (was defined here)
from harness.travel.models import (
    DailyAirQuality,
    DailyWeather,
    Earthquake,
    EventResult,
    FlightItinerary,
    FlightOffer,
    FlightQuery,
    GeoLocation,
    HotelQuery,
    HotelSearch,
    ImageCandidate,
    PriceInsight,
)

__all__ = [
    "AirQualityProvider",
    "EarthquakeProvider",
    "EventProvider",
    "FlightProvider",
    "HotelProvider",
    "ImageProvider",
    "ProviderError",
    "WeatherProvider",
]


@runtime_checkable
class FlightProvider(Protocol):
    name: str
    last_insight: PriceInsight | None  # price_insights from the most recent search (report fare-context)

    def search_flights(self, query: FlightQuery) -> list[FlightOffer]:
        """Return flight offers for the query, one logical best-offer per origin tried."""
        ...

    def search_itineraries(
        self, query: FlightQuery, *, cabin: str = "economy", limit: int = 3
    ) -> list[FlightItinerary]:
        """Return rich, single-cabin flight options for the query's first origin (report-grade:
        per-leg times/carrier/aircraft/legroom + layovers + the top-`limit` pool, not just best)."""
        ...


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    def search_images(self, query: str, limit: int = 1) -> list[ImageCandidate]:
        """Return image candidates (metadata only; bytes fetched separately by media.store_image).

        Source selection across providers is relevance × quota-cost (images are personal-use).
        Wikimedia = keyless/free (landmarks); google_hotels = specific rooms (rides the pricing
        call); Pexels/Unsplash = ambiance (config-gated).
        """
        ...


@runtime_checkable
class EventProvider(Protocol):
    name: str

    def search_events(
        self,
        city: str,
        start_date: date,
        end_date: date,
        *,
        classification: str | None = None,
        size: int = 200,
    ) -> list[EventResult]:
        """Return events in `city` within [start_date, end_date], chronological.

        Raw + classified; the maintainer's centerpiece/perk tiering is applied downstream
        (EventWeights.tier_for), so the provider stays a generic events source.
        """
        ...


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    def geocode(self, query: str) -> GeoLocation | None:
        """Resolve a place name to its top geocoding match (lat/lon + context), or None."""
        ...

    def daily_forecast(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        *,
        fahrenheit: bool = True,
    ) -> tuple[list[DailyWeather], str]:
        """Daily forecast over [start_date, end_date]. Returns (days, resolved_timezone)."""
        ...


@runtime_checkable
class AirQualityProvider(Protocol):
    name: str

    def daily_air_quality(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> tuple[list[DailyAirQuality], str, int | None]:
        """Daily AQI over [start_date, end_date]. Returns (days, resolved_timezone, current_us_aqi)."""
        ...


@runtime_checkable
class EarthquakeProvider(Protocol):
    name: str

    def recent_quakes(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_km: int = 300,
        since: date,
        min_magnitude: float = 2.5,
        limit: int = 20,
    ) -> list[Earthquake]:
        """Earthquakes within radius_km of (lat, lon) since `since`, magnitude-desc."""
        ...


@runtime_checkable
class HotelProvider(Protocol):
    name: str

    def search_hotels(self, query: HotelQuery, *, refresh: bool = False) -> HotelSearch:
        """Return a ranked list of bookable properties for the query — ONE search yields a whole
        list (not one per hotel). A date-keyed cache makes re-views cost zero quota; opt-in by design.
        """
        ...


# BookingProvider lands in v2 (the gated execution escalation); defined-when-built. The abstraction
# boundary here keeps adapters + the ranker from ever importing a concrete provider.
