"""Provider registry / factory.

SerpAPI is the sole flight provider. The factory keeps the call sites provider-agnostic so a
viable provider could slot in later without touching the ranker or adapters. Two candidates
were evaluated + dropped as non-viable for an individual dev: Amadeus (free self-service tier
decommissioned 2026-07-17) and Duffel (enterprise/seller-onboarding only).
"""

from __future__ import annotations

from harness.travel.config.settings import Settings, get_settings
from harness.travel.providers.air_quality_provider import build_air_quality_provider
from harness.travel.providers.base import (
    AirQualityProvider,
    EarthquakeProvider,
    EventProvider,
    FlightProvider,
    HotelProvider,
    ImageProvider,
    ProviderError,
    WeatherProvider,
)
from harness.travel.providers.open_meteo_provider import build_open_meteo_provider
from harness.travel.providers.serpapi_hotels_provider import build_serpapi_hotel_provider
from harness.travel.providers.serpapi_provider import build_serpapi_provider
from harness.travel.providers.ticketmaster_provider import build_ticketmaster_provider
from harness.travel.providers.trip_prep_provider import TripPrepProvider, build_trip_prep_provider
from harness.travel.providers.usgs_provider import build_usgs_provider
from harness.travel.providers.wikimedia_provider import WikimediaImageProvider
from harness.travel.providers.wsdot_provider import (
    WsdotTrafficProvider,
    build_wsdot_traffic_provider,
)
from harness.travel.providers.wsf_provider import WsfFerryProvider, build_wsf_ferry_provider

__all__ = [
    "AirQualityProvider",
    "EarthquakeProvider",
    "EventProvider",
    "FlightProvider",
    "HotelProvider",
    "ImageProvider",
    "ProviderError",
    "WeatherProvider",
    "get_air_quality_provider",
    "get_earthquake_provider",
    "get_event_provider",
    "get_flight_provider",
    "get_hotel_provider",
    "get_ferry_provider",
    "get_image_provider",
    "get_traffic_provider",
    "get_trip_prep_provider",
    "get_weather_provider",
]


def get_flight_provider(
    name: str = "serpapi",
    *,
    settings: Settings | None = None,
    avoid_iata: list[str] | None = None,
) -> FlightProvider:
    settings = settings or get_settings()
    if name == "serpapi":
        return build_serpapi_provider(settings.serpapi_key, avoid_iata=avoid_iata)
    raise ProviderError(f"unknown flight provider: {name!r}")


def get_hotel_provider(
    name: str = "serpapi", *, settings: Settings | None = None
) -> HotelProvider:
    # serpapi = Google Hotels (the lodging-research layer). ONE search = a whole property list;
    # a date-keyed cache makes re-views cost zero quota. Shares the SERPAPI_KEY with flights.
    settings = settings or get_settings()
    if name == "serpapi":
        return build_serpapi_hotel_provider(settings.serpapi_key)
    raise ProviderError(f"unknown hotel provider: {name!r}")


def get_weather_provider(
    name: str = "open-meteo", *, settings: Settings | None = None
) -> WeatherProvider:
    # open-meteo = keyless + free (default). NWS-alerts / climate-normals slot in later.
    if name == "open-meteo":
        return build_open_meteo_provider()
    raise ProviderError(f"unknown weather provider: {name!r}")


def get_air_quality_provider(
    name: str = "open-meteo-aq", *, settings: Settings | None = None
) -> AirQualityProvider:
    # open-meteo-aq = keyless + free (default). The wildfire-smoke / AQI sense.
    if name == "open-meteo-aq":
        return build_air_quality_provider()
    raise ProviderError(f"unknown air-quality provider: {name!r}")


def get_earthquake_provider(
    name: str = "usgs", *, settings: Settings | None = None
) -> EarthquakeProvider:
    # usgs = keyless + free (default). The geological-screen seismic sense.
    if name == "usgs":
        return build_usgs_provider()
    raise ProviderError(f"unknown earthquake provider: {name!r}")


def get_image_provider(
    name: str = "wikimedia", *, settings: Settings | None = None
) -> ImageProvider:
    # wikimedia = keyless + quota-free (default). google_hotels / pexels / unsplash slot in later.
    if name == "wikimedia":
        return WikimediaImageProvider()
    raise ProviderError(f"unknown image provider: {name!r}")


def get_event_provider(
    name: str = "ticketmaster", *, settings: Settings | None = None
) -> EventProvider:
    # ticketmaster = the dynamic-events layer (free Discovery tier; Consumer Key from .env).
    settings = settings or get_settings()
    if name == "ticketmaster":
        return build_ticketmaster_provider(settings.ticketmaster_key)
    raise ProviderError(f"unknown event provider: {name!r}")


def get_traffic_provider(
    name: str = "wsdot", *, settings: Settings | None = None
) -> WsdotTrafficProvider:
    # wsdot = WA-regional traffic (keyed-free AccessCode). The WA-corridor congestion + alerts layer;
    # a bundled multi-method provider (like trip_prep) — no swappable alternative for WA-gov data.
    settings = settings or get_settings()
    if name == "wsdot":
        return build_wsdot_traffic_provider(settings.wsdot_api_key)
    raise ProviderError(f"unknown traffic provider: {name!r}")


def get_ferry_provider(
    name: str = "wsf", *, settings: Settings | None = None
) -> WsfFerryProvider:
    # wsf = Washington State Ferries (the WSDOT ferry slice; reuses WSDOT_API_KEY as apiaccesscode).
    settings = settings or get_settings()
    if name == "wsf":
        return build_wsf_ferry_provider(settings.wsdot_api_key)
    raise ProviderError(f"unknown ferry provider: {name!r}")


def get_trip_prep_provider(name: str = "default") -> TripPrepProvider:
    # The keyless trip-prep enricher bundle (FX / holidays / sun / country facts). No key, no
    # alternative provider — a single bundle of public-data GETs.
    if name == "default":
        return build_trip_prep_provider()
    raise ProviderError(f"unknown trip-prep provider: {name!r}")
