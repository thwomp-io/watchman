"""`TravelService`: the one surface the CLI and the MCP server both call, so the two can't drift.

It holds the corpus reader and the weights and little else. Each area's work lives in its own
module (`events`, `planning`, `visits`, `nudge_digest`, `senses`, `conditions`, `roads`, `food`,
`flights`, `explore`, `lodging`, `media`) as functions that take the service; the methods here
are the typed index of them. A function that needs another area's read calls it through the
service, which is also the seam a test replaces.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from harness.travel import bank as _bank
from harness.travel import (
    conditions,
    events,
    explore,
    flights,
    food,
    media,
    nudge_digest,
    planning,
    roads,
    senses,
    visits,
)
from harness.travel.bank import VisitBank
from harness.travel.conditions import ConditionsReport
from harness.travel.config.settings import get_settings
from harness.travel.corpus.reader import CorpusReader, PreferencesDigest, TripPlan
from harness.travel.imaging import ShrinkReport, shrink_tree
from harness.travel.models import (
    AirQualityReport,
    CityEvents,
    CountryFacts,
    EventResult,
    ExploreSearch,
    FerryReport,
    FlightOffer,
    FlightQuery,
    FlightSearch,
    FoodReport,
    FxRates,
    GeoLocation,
    Holidays,
    HotelQuery,
    HotelSearch,
    ImageResult,
    SeismicReport,
    Shortlist,
    SunTimes,
    TrafficReport,
    WeatherForecast,
)
from harness.travel.providers import (
    get_flight_provider,
    get_hotel_provider,
    get_trip_prep_provider,
    get_weather_provider,
)
from harness.travel.providers.base import FlightProvider, ProviderError
from harness.travel.providers.geocoding import geocode
from harness.travel.ranking.ranker import rank_candidates
from harness.travel.ranking.weights import WeightConfig, load_weights
from harness.travel.schedule import VisitSchedule


class TravelService:
    def __init__(
        self,
        reader: CorpusReader | None = None,
        weights: WeightConfig | None = None,
        flight_provider_name: str = "serpapi",
    ) -> None:
        self.weights = weights or load_weights()
        self.reader = reader or CorpusReader(weights=self.weights)
        self._provider_name = flight_provider_name

    def _provider(self) -> FlightProvider:
        return get_flight_provider(
            self._provider_name, avoid_iata=self.weights.flight.airline_avoid_iata
        )

    # ---- corpus passthroughs ----
    def read_preferences(self) -> PreferencesDigest:
        return self.reader.read_preferences()

    def read_trip_plan(self, trip_slug: str) -> TripPlan:
        return self.reader.build_trip_plan(trip_slug)

    # ---- search ----
    def search_flights(
        self, origins: list[str], destination: str, depart: date, return_: date | None = None
    ) -> list[FlightOffer]:
        query = FlightQuery(
            origins=origins, destination=destination, depart=depart, return_=return_
        )
        return self._provider().search_flights(query)

    # ---- flight research: the cabin-aware deepen-after-pick artifact (economy vs first) ----
    def research_flights(
        self,
        destination: str,
        depart: date,
        return_: date | None = None,
        *,
        origins: list[str] | None = None,
        cabins: tuple[str, ...] = ("economy", "first"),
        limit: int = 3,
        provider_name: str = "serpapi",
    ) -> FlightSearch:
        """See `harness.travel.flights.research_flights`."""
        return flights.research_flights(
            self,
            destination,
            depart,
            return_,
            origins=origins,
            cabins=cabins,
            limit=limit,
            provider_name=provider_name,
        )

    def write_flights_report(
        self,
        search: FlightSearch,
        dest: str,
        *,
        force: bool = False,
        vault_root: Path | None = None,
    ) -> Path:
        """Write a `{dest}/flights/` report from a FlightSearch — the deepen-after-pick artifact that
        turns live cabin-compared fares into a shareable trip-pitch corpus doc (mirrors lodging)."""
        from harness.travel.flights import write_flights_report

        root = vault_root or get_settings().tracker_path
        return write_flights_report(search, dest, root, force=force)

    # ---- lodging: tangible bookable properties (the luxe-stay texture; opt-in, quota-cheap) ----
    def search_hotels(
        self,
        location: str,
        check_in: date,
        check_out: date,
        *,
        adults: int = 2,
        min_hotel_class: int = 4,
        min_rating: float | None = None,
        max_price: int | None = None,
        limit: int = 5,
        vacation_rentals: bool = False,
        refresh: bool = False,
        provider_name: str = "serpapi",
    ) -> HotelSearch:
        """Ranked list of bookable properties for `location` over the window — the lodging-research
        layer that turns a luxe-stay pitch into named, priced, photographed, next-step-is-booking
        options. **ONE search returns the whole list** (not one per hotel); a date-keyed cache makes
        re-views cost zero quota (`refresh=True` forces a fresh search). Defaults to the 4-5★ lens."""
        provider = get_hotel_provider(provider_name)
        query = HotelQuery(
            location=location,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            min_hotel_class=min_hotel_class,
            min_rating=min_rating,
            max_price=max_price,
            limit=limit,
            vacation_rentals=vacation_rentals,
        )
        return provider.search_hotels(query, refresh=refresh)

    def explore_destinations(
        self,
        *,
        origins: list[str] | None = None,
        outbound: date | None = None,
        return_: date | None = None,
        month: int = 0,
        duration: str = "week",
        travel_class: str = "economy",
        stops: str = "any",
        max_price: int | None = None,
        to: str | None = None,
        limit: int = 25,
        refresh: bool = False,
    ) -> ExploreSearch:
        """See `harness.travel.explore.explore_destinations`."""
        return explore.explore_destinations(
            self,
            origins=origins,
            outbound=outbound,
            return_=return_,
            month=month,
            duration=duration,
            travel_class=travel_class,
            stops=stops,
            max_price=max_price,
            to=to,
            limit=limit,
            refresh=refresh,
        )

    def write_lodging_report(
        self,
        search: HotelSearch,
        dest: str,
        *,
        photos_per: int = 2,
        force: bool = False,
        vault_root: Path | None = None,
    ) -> Path:
        """Write a `{dest}/lodging/` report from a HotelSearch (+ download property photos) — the
        deepen-after-pick artifact that turns live results into a shareable trip-pitch corpus doc."""
        from harness.travel.lodging import write_lodging_report

        root = vault_root or get_settings().tracker_path
        return write_lodging_report(search, dest, root, photos_per=photos_per, force=force)

    # ---- the headline: rank a trip's candidates against live flight data ----
    def rank_trip(self, trip_slug: str) -> Shortlist:
        plan = self.reader.build_trip_plan(trip_slug)
        provider = self._provider()
        items = []
        for cand in plan.candidates:
            query = FlightQuery(
                origins=cand.origins,
                destination=cand.dest_iata,
                depart=plan.depart,
                return_=plan.return_,
            )
            offers = provider.search_flights(query)
            items.append((cand, offers))
        return rank_candidates(items, self.weights, window=plan.window)

    # ---- events: the dynamic-characteristics layer (what's ON during the window) ----
    def find_events(
        self,
        city: str,
        start: date,
        end: date,
        *,
        classification: str | None = None,
        provider_name: str = "ticketmaster",
    ) -> list[EventResult]:
        """See `harness.travel.events.find_events`."""
        return events.find_events(
            self,
            city,
            start,
            end,
            classification=classification,
            provider_name=provider_name,
        )

    def event_tier(self, event: EventResult) -> str:
        """See `harness.travel.events.event_tier`."""
        return events.event_tier(self, event)

    dedupe_events = staticmethod(events.dedupe_events)

    def calendar_data(
        self, start: date, end: date, *, city: str | None = None, variant: str = "grid"
    ) -> dict[str, object]:
        """See `harness.travel.events.calendar_data`."""
        return events.calendar_data(self, start, end, city=city, variant=variant)

    def scan_events(
        self,
        cities: list[str],
        start: date,
        end: date,
        *,
        classification: str | None = None,
        provider_name: str = "ticketmaster",
        radius_mi: int = 0,
    ) -> list[CityEvents]:
        """See `harness.travel.events.scan_events`."""
        return events.scan_events(
            self,
            cities,
            start,
            end,
            classification=classification,
            provider_name=provider_name,
            radius_mi=radius_mi,
        )

    def scan_trip_events(
        self,
        trip_slug: str,
        *,
        classification: str | None = None,
        provider_name: str = "ticketmaster",
    ) -> list[CityEvents]:
        """See `harness.travel.events.scan_trip_events`."""
        return events.scan_trip_events(
            self,
            trip_slug,
            classification=classification,
            provider_name=provider_name,
        )

    def scan_reference(
        self, start: date, end: date, *, city: str | None = None
    ) -> list[EventResult]:
        """See `harness.travel.events.scan_reference`."""
        return events.scan_reference(self, start, end, city=city)

    # ---- proactive nudge ----
    def nudge(
        self,
        today: date,
        *,
        horizon_days: int = 182,
        top: int = 5,
        live: bool = True,
        include_taken: bool = False,
        lanes: list[str] | None = None,
    ) -> dict[str, Any]:
        """See `harness.travel.nudge_digest.nudge`."""
        return nudge_digest.nudge(
            self,
            today,
            horizon_days=horizon_days,
            top=top,
            live=live,
            include_taken=include_taken,
            lanes=lanes,
        )



    def active_decision(self) -> dict[str, Any]:
        """See `harness.travel.planning.active_decision`."""
        return planning.active_decision(self)

    def visit_bank(
        self, slug: str | None = None, kind: str | None = None, *, scope: str = "visit"
    ) -> VisitBank:
        """See `harness.travel.visits.visit_bank`."""
        return visits.visit_bank(self, slug, kind, scope=scope)

    def visit_bank_enrich(
        self, slug: str | None = None, *, scope: str = "visit", dry_run: bool = True,
        refresh: bool = False, cached_only: bool = False,
    ) -> dict[str, Any]:
        """See `harness.travel.visits.visit_bank_enrich`."""
        return visits.visit_bank_enrich(
            self,
            slug,
            scope=scope,
            dry_run=dry_run,
            refresh=refresh,
            cached_only=cached_only,
        )

    def visit_bank_photos(
        self,
        slug: str | None = None,
        kind: str | None = None,
        *,
        ids: set[str] | None = None,
        per_item: int = 8,
        refresh: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """See `harness.travel.visits.visit_bank_photos`."""
        return visits.visit_bank_photos(
            self,
            slug,
            kind,
            ids=ids,
            per_item=per_item,
            refresh=refresh,
            dry_run=dry_run,
        )

    def visit_schedule(
        self, slug: str | None = None, *, weather: bool = True, today: date | None = None,
        scope: str = "visit",
    ) -> VisitSchedule:
        """See `harness.travel.visits.visit_schedule`."""
        return visits.visit_schedule(self, slug, weather=weather, today=today, scope=scope)

    def trip_banks(self, scope: str = "visit", today: date | None = None) -> _bank.TripBanks:
        """See `harness.travel.planning.trip_banks`."""
        return planning.trip_banks(self, scope, today)


    def planning_calendar(self, today: date | None = None) -> _bank.PlanningCalendar:
        """See `harness.travel.planning.planning_calendar`."""
        return planning.planning_calendar(self, today)

    # ---- trip pipeline (the command-center spine) ----
    def trip_pipeline(
        self, today: date, *, include_past: bool = False, phase: str = "live"
    ) -> list[dict[str, str]]:
        """See `harness.travel.planning.trip_pipeline`."""
        return planning.trip_pipeline(self, today, include_past=include_past, phase=phase)


    # ---- map: geocode + build `map` viz data (d3-geo pins + great-circle arcs from home) ----
    def geocode_place(self, city: str, *, provider_name: str = "open-meteo") -> GeoLocation | None:
        """Resolve a place name → lat/lon (keyless, via the weather provider's geocoder). Powers the
        `map` viz; the same geocoder the weather/air/quakes senses use."""
        return get_weather_provider(provider_name).geocode(city)

    def map_data_for_trip(
        self,
        trip_slug: str,
        *,
        origin_label: str | None = None,
        origin_lat: float | None = None,
        origin_lon: float | None = None,
    ) -> dict[str, Any]:
        """See `harness.travel.planning.map_data_for_trip`."""
        return planning.map_data_for_trip(
            self,
            trip_slug,
            origin_label=origin_label,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
        )

    # ---- weather: the dynamic 'sense' — live forecast vs the corpus's climate-averages ----
    def get_weather(
        self,
        city: str,
        start_date: date,
        end_date: date,
        *,
        fahrenheit: bool = True,
        provider_name: str = "open-meteo",
    ) -> WeatherForecast:
        """See `harness.travel.senses.get_weather`."""
        return senses.get_weather(
            self,
            city,
            start_date,
            end_date,
            fahrenheit=fahrenheit,
            provider_name=provider_name,
        )

    def get_air_quality(
        self,
        city: str,
        start_date: date,
        end_date: date,
        *,
        provider_name: str = "open-meteo-aq",
    ) -> AirQualityReport:
        """See `harness.travel.senses.get_air_quality`."""
        return senses.get_air_quality(self, city, start_date, end_date, provider_name=provider_name)

    def conditions_pulse(self, *, today: date | None = None) -> ConditionsReport:
        """See `harness.travel.conditions.conditions_pulse`."""
        return conditions.conditions_pulse(self, today=today)

    def get_earthquakes(
        self,
        city: str,
        *,
        radius_km: int = 300,
        days_back: int = 90,
        min_magnitude: float = 2.5,
        limit: int = 20,
        provider_name: str = "usgs",
    ) -> SeismicReport:
        """See `harness.travel.senses.get_earthquakes`."""
        return senses.get_earthquakes(
            self,
            city,
            radius_km=radius_km,
            days_back=days_back,
            min_magnitude=min_magnitude,
            limit=limit,
            provider_name=provider_name,
        )

    def find_food(
        self,
        place: str,
        *,
        radius_m: int = 1500,
        live_ratings: bool = False,
        yelp: bool = False,
        yelp_query: str | None = None,
        refresh: bool = False,
        hero_images: bool = False,
    ) -> FoodReport:
        """See `harness.travel.food.find_food`."""
        return food.find_food(
            self,
            place,
            radius_m=radius_m,
            live_ratings=live_ratings,
            yelp=yelp,
            yelp_query=yelp_query,
            refresh=refresh,
            hero_images=hero_images,
        )

    def food_photos(
        self, place: str, names: list[str], *, radius_m: int = 1500, limit_per: int = 12,
        refresh: bool = False,
    ) -> dict[str, list[str]]:
        """See `harness.travel.food.food_photos`."""
        return food.food_photos(self, place, names, radius_m=radius_m, limit_per=limit_per, refresh=refresh)

    # ---- trip-prep enrichers (keyless): FX · holidays · sun · country facts ----
    def fx_rates(self, to: list[str] | None = None, *, base: str = "USD") -> FxRates:
        """USD-base (default) exchange rates — `rates[X]` = units of X per 1 base (open.er-api.com)."""
        return get_trip_prep_provider().fx_rates(to, base=base)

    def public_holidays(self, country: str, year: int) -> Holidays:
        """Public holidays for a country-year (date.nager.at) — crowds/closures timing."""
        return get_trip_prep_provider().public_holidays(country, year)

    def sun_times(
        self,
        place: str | None = None,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        date_str: str,
    ) -> SunTimes:
        """Sunrise/sunset/twilight + golden-hour approximations for a place-day (sunrise-sunset.org).
        Pass a `place` name (geocoded) or explicit `latitude`/`longitude`."""
        if place:
            loc = geocode(place)
            if loc is None:
                raise ProviderError(f"no location match for {place!r}")
            latitude, longitude = loc.latitude, loc.longitude
        if latitude is None or longitude is None:
            raise ProviderError("sun_times needs a place name or explicit latitude/longitude")
        return get_trip_prep_provider().sun_times(latitude, longitude, date_str)

    def country_facts(self, name: str) -> CountryFacts:
        """Trip-prep country facts (restcountries.com): currency / language / region / driving side."""
        return get_trip_prep_provider().country_facts(name)

    # ---- WA-regional traffic (keyed-free WSDOT): live congestion + construction/closure alerts ----
    def get_traffic(
        self,
        *,
        near: str | None = None,
        road: str | None = None,
        category: str | None = None,
        congested_only: bool = False,
        include_times: bool = True,
        include_alerts: bool = True,
        congestion_threshold: int = 5,
        provider_name: str = "wsdot",
    ) -> TrafficReport:
        """See `harness.travel.roads.get_traffic`."""
        return roads.get_traffic(
            self,
            near=near,
            road=road,
            category=category,
            congested_only=congested_only,
            include_times=include_times,
            include_alerts=include_alerts,
            congestion_threshold=congestion_threshold,
            provider_name=provider_name,
        )

    # ---- WSF ferries (keyed-free WSDOT slice): schedule + live drive-up space + vessel positions ----
    def get_ferry(
        self,
        *,
        route: str | None = None,
        space: bool = False,
        vessels: bool = False,
        only_remaining: bool = True,
        provider_name: str = "wsf",
    ) -> FerryReport:
        """See `harness.travel.roads.get_ferry`."""
        return roads.get_ferry(
            self,
            route=route,
            space=space,
            vessels=vessels,
            only_remaining=only_remaining,
            provider_name=provider_name,
        )

    # ---- images: fetch + locally store for embedding in a destination report ----
    def fetch_destination_images(
        self,
        dest: str,
        subjects: list[str],
        provider_name: str = "wikimedia",
        vault_root: Path | None = None,
        delay_s: float = 1.0,
        keep_original: bool = False,
        errors: dict[str, str] | None = None,
    ) -> list[ImageResult]:
        """See `harness.travel.media.fetch_destination_images`."""
        return media.fetch_destination_images(
            self,
            dest,
            subjects,
            provider_name,
            vault_root,
            delay_s,
            keep_original,
            errors,
        )

    def shrink_images(
        self, rel_dir: str, *, dry_run: bool = False, skip_png: bool = False,
        vault_root: Path | None = None,
    ) -> list[ShrinkReport]:
        """Apply the vault image-size discipline to assets already on disk (in place, extensions
        kept). `rel_dir` is vault-relative (e.g. `travel/destinations/x/food/2026-09-assets`)."""
        root = (vault_root or get_settings().tracker_path) / rel_dir.strip("/")
        if not root.is_dir():
            raise ProviderError(f"not a directory under the vault: {rel_dir}")
        return shrink_tree(root, dry_run=dry_run, skip_png=skip_png)

    def archive_food_photos(
        self, galleries: dict[str, list[str]], rel_dir: str, *, per_place: int = 2,
        vault_root: Path | None = None,
    ) -> dict[str, list[str]]:
        """See `harness.travel.food.archive_food_photos`."""
        return food.archive_food_photos(
            self,
            galleries,
            rel_dir,
            per_place=per_place,
            vault_root=vault_root,
        )

    def make_contact_sheet(
        self,
        dest: str | None = None,
        paths: list[str] | None = None,
        *,
        name: str | None = None,
        cols: int = 4,
        vault_root: Path | None = None,
    ) -> Path:
        """See `harness.travel.media.make_contact_sheet`."""
        return media.make_contact_sheet(self, dest, paths, name=name, cols=cols, vault_root=vault_root)


# ---- traffic filter predicates (module-level: stateless, shared by get_traffic) ----









