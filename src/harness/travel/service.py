"""Single orchestration surface. MCP + CLI adapters both call here (parity is structural)."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

from harness.travel.conditions import ConditionsReport, compute_flags
from harness.travel.config.settings import get_settings
from harness.travel.corpus.reader import CorpusReader, PreferencesDigest, TripPlan
from harness.travel.corpus.reference import read_reference
from harness.travel.media import build_contact_sheet, dest_dir_parts, store_image
from harness.travel.models import (
    AirQualityReport,
    CityEvents,
    CountryFacts,
    Eatery,
    EventResult,
    FerryReport,
    FlightOffer,
    FlightQuery,
    FlightSearch,
    FoodReport,
    FxRates,
    GeoLocation,
    HighwayAlert,
    Holidays,
    HotelQuery,
    HotelSearch,
    ImageResult,
    RoadwayLocation,
    SeismicReport,
    Shortlist,
    SunTimes,
    TrafficReport,
    TravelTime,
    Trip,
    WeatherForecast,
)
from harness.travel.providers import (
    get_air_quality_provider,
    get_earthquake_provider,
    get_event_provider,
    get_ferry_provider,
    get_flight_provider,
    get_hotel_provider,
    get_image_provider,
    get_traffic_provider,
    get_trip_prep_provider,
    get_weather_provider,
)
from harness.travel.providers.base import FlightProvider, ProviderError
from harness.travel.providers.geocoding import geocode, geocode_label
from harness.travel.providers.wsdot_provider import normalize_road
from harness.travel.ranking.ranker import rank_candidates
from harness.travel.ranking.weights import WeightConfig, load_weights


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
        """Rich, cabin-aware flight research for a route+window across one or more origins — the flights
        twin of `search_hotels`. Default `origins` (config-driven, `FlightWeights`): the **local/home
        airport + a hub** for a destination the local airport serves (the local airport leads — the
        convenience win, with the hub as the price/frequency comparison), else the hub only. A small
        local airport is fully queryable but can have a sparse schedule, so it returns no options when it
        doesn't fly the window. Spends one search per origin × cabin (default 2 origins × 2 cabins = 4) —
        opt-in deepen-after-pick."""
        provider = get_flight_provider(
            provider_name, avoid_iata=self.weights.flight.airline_avoid_iata
        )
        fw = self.weights.flight
        dest = destination.upper()
        home_airport_served = dest in fw.home_airport_served_iata
        if origins is None:
            origins = fw.query_origins(home_airport_served)
        origins = [o.upper() for o in origins]
        hub = fw.comparison_airport.upper()
        options = []
        insight = None
        for org in origins:
            for cabin in cabins:
                query = FlightQuery(
                    origins=[org], destination=dest, depart=depart, return_=return_
                )
                options.extend(provider.search_itineraries(query, cabin=cabin, limit=limit))
                # fare-context comes from the hub's economy (the deepest, most-comparable market)
                if org == hub and cabin == "economy" and provider.last_insight is not None:
                    insight = provider.last_insight
        return FlightSearch(
            origins=origins,
            dest_iata=dest,
            depart=depart,
            return_=return_,
            round_trip=return_ is not None,
            home_airport_served=home_airport_served,
            home_airport=fw.home_airport.upper(),
            comparison_airport=hub,
            home_airport_note=fw.home_airport_note,
            cabins=list(cabins),
            options=options,
            price_insight=insight,
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
        """Live events in `city` within [start, end]. Raw + classified; tier with event_tier()."""
        provider = get_event_provider(provider_name)
        return provider.search_events(city, start, end, classification=classification)

    def event_tier(self, event: EventResult) -> str:
        """'centerpiece' (NBA/NFL — trip-around-able) or 'perk', per the corpus EventWeights."""
        return self.weights.events.tier_for(event.subgenre)

    @staticmethod
    def dedupe_events(events: list[EventResult]) -> list[EventResult]:
        """Drop repeat *listings* of the same event. Tier-aware: residencies/tours (music/arts) fold
        to a single listing by name (TM lists each night separately — one 'it's in town' signal is
        enough), but SPORTS keep one-per-date — a series shares a name yet each game is a distinct
        attend-decision, and folding them hid a team's home dates. Keys on the name before any ' - '
        qualifier; sports additionally key on local_date. Order-preserving."""
        seen: set[tuple[str, str]] = set()
        out: list[EventResult] = []
        for e in events:
            base = e.name.split(" - ")[0].strip().lower()
            if not base:
                out.append(e)  # unnamed — never fold
                continue
            per_date = (e.segment or "").lower() == "sports"
            key = (base, e.local_date if per_date else "")
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    def scan_events(
        self,
        cities: list[str],
        start: date,
        end: date,
        *,
        classification: str | None = None,
        provider_name: str = "ticketmaster",
    ) -> list[CityEvents]:
        """Scan multiple cities for events in a window — the carved multi-city path (replaces
        ad-hoc scanning). Fails fast on a missing key (a config error, not per-city), but a per-city
        transient error yields that city empty rather than killing the scan. Deduped per city."""
        if not get_settings().ticketmaster_key:
            raise ProviderError(
                "TICKETMASTER_KEY is not set — cannot scan events. Add it to .env "
                "(Consumer Key from developer.ticketmaster.com)."
            )
        provider = get_event_provider(provider_name)
        out: list[CityEvents] = []
        for city in cities:
            try:
                found = provider.search_events(city, start, end, classification=classification)
            except (ProviderError, RuntimeError):
                found = []  # per-city transient — keep the rest of the scan
            out.append(CityEvents(city=city, events=self.dedupe_events(found)))
        return out

    def scan_trip_events(
        self,
        trip_slug: str,
        *,
        classification: str | None = None,
        provider_name: str = "ticketmaster",
    ) -> list[CityEvents]:
        """Trip-scoped events — read the trip's candidate destinations from the corpus, map each to
        its event-city (weights.destination_cities), and scan that set for the trip window. The
        events twin of `rank_trip`. Candidates lacking a city mapping are skipped."""
        plan = self.reader.build_trip_plan(trip_slug)
        end = plan.return_ or plan.depart
        cities = [
            self.weights.destination_cities[c.slug]
            for c in plan.candidates
            if c.slug in self.weights.destination_cities
        ]
        return self.scan_events(
            cities, plan.depart, end, classification=classification, provider_name=provider_name
        )

    def scan_reference(
        self, start: date, end: date, *, city: str | None = None
    ) -> list[EventResult]:
        """Static-almanac entries (source='reference') in [start, end] — the PROACTIVE layer that
        merges with the reactive live `scan_events`. Reads corpus/travel/reference/*.md
        (centerpiece games + holidays + mega-events). `city` keeps located entries matching that city
        plus all location-agnostic ones (holidays/personal dates); None returns everything in-window.
        Sorted by date. Tier with `event_tier()` like any EventResult (NFL games → centerpiece)."""
        out: list[EventResult] = []
        for e in read_reference(self.reader._root, self.weights.events.followed_teams):
            try:
                d = date.fromisoformat(e.local_date)
            except ValueError:
                continue
            if not (start <= d <= end):
                continue
            if city and e.city and city.lower() not in e.city.lower() and e.city.lower() not in city.lower():
                continue
            out.append(e)
        return sorted(out, key=lambda e: e.local_date)

    # ---- trip pipeline (the command-center spine) ----
    def trip_pipeline(self, today: date, *, include_past: bool = False) -> list[dict[str, str]]:
        """The whole travel horizon as dashboard-ready rows: every live trip/visit (and past ones
        with `include_past`), countdown-enriched and ordered live-soonest-first then past-most-recent.
        Reads the `trip:` frontmatter via the corpus — deterministic, no tag-guessing."""
        done = {"completed", "cancelled"}
        trips = self.reader.scan_trips()
        live = sorted((t for t in trips if t.status not in done), key=lambda t: t.sort_date)
        past = sorted((t for t in trips if t.status in done), key=lambda t: t.sort_date, reverse=True)
        ordered = live + (past if include_past else [])
        return [self._trip_row(t, today) for t in ordered]

    @staticmethod
    def _trip_row(t: Trip, today: date) -> dict[str, str]:
        if t.status == "completed":
            when = "completed"
        elif t.status == "cancelled":
            when = "cancelled"
        elif t.start is None:
            when = t.window or "planning"
        else:
            days = (t.start - today).days
            if days < 0:
                when = "now" if (t.end and t.end >= today) else "past"
            elif days == 0:
                when = "TODAY"
            elif days == 1:
                when = "tomorrow"
            elif days < 14:
                when = f"in {days}d"
            elif days < 70:
                when = f"in {days // 7}w"
            else:
                when = f"in {days // 30}mo"
        return {
            "when": when,
            "date": t.start.isoformat() if t.start else (t.window or "—"),
            "status": t.status,
            "kind": t.kind,
            "destination": t.destination or "—",
            "anchor": t.anchor or "—",
            "who": ", ".join(t.travelers) or "—",
            "doc": t.doc,  # tracker-relative vault path → the dashboard renders it as an "open ↗" link
        }

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
        """Build `map` viz data for a trip: geocode each candidate's event-city + arc from the
        origin. All origin details are config-driven (no hardcoded home location): the label is the
        configured hub / local airport pair, and the lat/lon geocode the configured home locale
        (`conditions.home`) when not passed explicitly. Reach label is curated — '{local} direct' if
        the gateway IATA is on the local airport's route map, else 'via {hub}' (NOT a routed/computed
        time). Candidates lacking a city mapping or geocode are skipped (honest partial)."""
        fw = self.weights.flight
        home_iata, hub_iata = fw.home_airport.upper(), fw.comparison_airport.upper()
        if origin_label is None:
            origin_label = " / ".join(x for x in (hub_iata, home_iata) if x) or "home"
        if origin_lat is None or origin_lon is None:
            home_locale = self.weights.conditions.home
            loc = self.geocode_place(home_locale) if home_locale else None
            origin_lat = loc.latitude if loc else 0.0
            origin_lon = loc.longitude if loc else 0.0
        plan = self.reader.build_trip_plan(trip_slug)
        points: list[dict[str, Any]] = []
        for c in plan.candidates:
            city = self.weights.destination_cities.get(c.slug)
            if not city:
                continue
            loc = self.geocode_place(city)
            if loc is None:
                continue
            iata = self.weights.destination_airports.get(c.slug)
            served = home_iata and iata in fw.home_airport_served_iata
            reach = f"{home_iata} direct" if served else (f"via {hub_iata}" if hub_iata else "")
            points.append(
                {"label": c.display_name, "lat": loc.latitude, "lon": loc.longitude, "reach": reach}
            )
        return {
            "title": f"{trip_slug} — candidate map",
            "subtitle": f"great-circle from {origin_label} · reach is curated, not routed",
            "origin": {"label": origin_label, "lat": origin_lat, "lon": origin_lon},
            "points": points,
        }

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
            days=days,
        )

    def get_air_quality(
        self,
        city: str,
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
        from harness.travel.providers.air_quality_provider import aqi_category

        return AirQualityReport(
            location=geocode_label(loc),
            latitude=loc.latitude,
            longitude=loc.longitude,
            timezone=tz or (loc.timezone or ""),
            current_us_aqi=current,
            current_category=aqi_category(current),
            days=days,
        )

    def conditions_pulse(self, *, today: date | None = None) -> ConditionsReport:
        """Travel Watchman conditions watch: flag threshold crossings (heat/smoke/wet_day/
        snow) for the configured **home** locale over the near horizon AND for any **finalized trip**
        within ``arm_days`` of its start — thresholds from weights.yaml ``conditions:``. quiet=True ->
        a standing run ends silently. Detection only; the agent narrates. `today` is injectable for tests."""
        from datetime import timedelta

        from harness.travel.conditions import should_arm

        cfg = self.weights.conditions
        day0 = today or date.today()
        end = day0 + timedelta(days=max(0, cfg.horizon_days))

        weather = self.get_weather(cfg.home, day0, end)  # ProviderError propagates (home must resolve)
        try:
            air = self.get_air_quality(cfg.home, day0, end)
        except ProviderError:
            air = None  # air sense down -> weather flags still fire (graceful degradation)
        flags = compute_flags(scope="home", place=cfg.home, weather=weather, air=air, th=cfg.thresholds)

        # finalized trips within arm_days -> watch the destination over its (horizon-capped) window
        armed: list[str] = []
        for trip in self.reader.scan_trips():
            if not should_arm(trip, day0, cfg.arm_statuses, cfg.arm_days):
                continue
            assert trip.start is not None  # guaranteed by should_arm
            t_end = min(trip.end or trip.start, day0 + timedelta(days=15))  # ~16d weather horizon
            try:
                t_wx = self.get_weather(trip.destination, trip.start, t_end)
            except ProviderError:
                continue  # can't resolve/forecast the destination -> skip it, never kill the home pulse
            try:
                t_air = self.get_air_quality(
                    trip.destination, trip.start, min(t_end, day0 + timedelta(days=5))
                )
            except ProviderError:
                t_air = None
            flags += compute_flags(
                scope="trip", place=trip.destination, weather=t_wx, air=t_air, th=cfg.thresholds
            )
            armed.append(f"{trip.destination} ({trip.start.isoformat()})")

        return ConditionsReport(
            as_of=day0.isoformat(), home=cfg.home, quiet=not flags,
            flags=flags, weather=weather, air=air, armed_trips=armed,
        )

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
        """Recent earthquakes within radius_km of `city` over the last `days_back` — the geological-
        screen sense (USGS, keyless). Calibrates the screen with real data, not categorical priors."""
        from datetime import timedelta

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

    def find_food(
        self,
        place: str,
        *,
        radius_m: int = 1500,
        live_ratings: bool = False,
        refresh: bool = False,
    ) -> FoodReport:
        """Eateries near `place` — two-tier. Tier 1 (default, KEYLESS): OSM Overpass
        enumeration — what exists, from data instead of memory. Tier 2 (`live_ratings=True`, QUOTA
        — confirm first): SerpAPI google_maps ratings merged on, ~1 search, cached."""
        from harness.travel.providers.food_provider import (
            OverpassFoodProvider,
            SerpApiLocalFoodProvider,
            merge_eateries,
        )

        loc = geocode(place)
        if loc is None:
            raise ProviderError(f"no location match for {place!r}")
        eateries: list[Eatery] = OverpassFoodProvider().eateries_near(
            loc.latitude, loc.longitude, radius_m=radius_m
        )
        notes = [f"OSM Overpass enumeration (keyless): {len(eateries)} mapped eateries"]
        if live_ratings:
            provider = SerpApiLocalFoodProvider(get_settings().serpapi_key)
            rated = provider.rated_eateries(
                geocode_label(loc), loc.latitude, loc.longitude, refresh=refresh
            )
            eateries = merge_eateries(eateries, rated)
            spent = "cache (0 quota)" if provider.cache_hits else f"{provider.search_count} search"
            notes.append(
                f"Google local ratings merged: {len(rated)} rated rows · {spent}. "
                "Unrated OSM rows are below the ~20-row local-pack fold, not bad signs."
            )
        else:
            notes.append("ratings tier NOT spent (opt-in: --live-ratings) — enumeration only")
        return FoodReport(
            location=geocode_label(loc),
            latitude=loc.latitude,
            longitude=loc.longitude,
            radius_m=radius_m,
            count=len(eateries),
            live_ratings=live_ratings,
            eateries=eateries,
            notes=notes,
        )

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
        """Live WA traffic from WSDOT (keyed-free): travel-time congestion deltas + highway alerts,
        filtered for the configured corridors. `near` = case-insensitive substring over route
        name/description (times) + headline/location (alerts); `road` = a friendly highway token
        ('I-5' / '405' / 'US-2', normalized to WSDOT's code); `category` filters alert EventCategory
        (e.g. 'Construction'). `congested_only` keeps only routes delayed >= the threshold."""
        provider = get_traffic_provider(provider_name)
        road_code = normalize_road(road) if road else None
        times: list[TravelTime] = []
        alerts: list[HighwayAlert] = []
        if include_times:
            times = [
                t
                for t in provider.travel_times(congestion_threshold=congestion_threshold)
                if _tt_matches(t, near, road_code, congested_only)
            ]
        if include_alerts:
            alerts = [
                a
                for a in provider.highway_alerts()
                if _alert_matches(a, near, road_code, category)
            ]
        applied = {
            "near": near,
            "road": road,
            "category": category,
            "congested_only": "true" if congested_only else None,
        }
        return TrafficReport(
            travel_times=times,
            alerts=alerts,
            filters={k: v for k, v in applied.items() if v},
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
        """WSF ferry snapshot. With `route` ('Seattle-Bainbridge Island'): today's sailing times,
        plus (if `space`) the departing terminal's live drive-up space and (if `vessels`) the vessels
        serving it. Without `route`: `space` returns all terminals' live space, `vessels` returns all
        vessels. `route` alone implies schedule; pass `space`/`vessels` to enrich it."""
        provider = get_ferry_provider(provider_name)
        report = FerryReport(route=route)
        if route:
            dep_id, arr_id, dep_name, arr_name = provider.resolve_route(route)
            report.sailings = provider.schedule_today(dep_id, arr_id, only_remaining=only_remaining)
            if space:
                report.space = provider.sailing_space(terminal=dep_name)
            if vessels:
                report.vessels = provider.vessel_locations(route=dep_name)
        else:
            if space:
                report.space = provider.sailing_space()
            if vessels:
                report.vessels = provider.vessel_locations()
        return report

    # ---- images: fetch + locally store for embedding in a destination report ----
    def fetch_destination_images(
        self,
        dest: str,
        subjects: list[str],
        provider_name: str = "wikimedia",
        vault_root: Path | None = None,
        delay_s: float = 1.0,
    ) -> list[ImageResult]:
        """For each subject (hotel / attraction / view), find an image and store it locally under
        the doc's assets/ dir. `dest` is a bare destination slug (→ travel/destinations/{slug}/assets,
        back-compat) OR a vault path under travel/ (→ travel/{dest}/assets, like `viz --dest` — lets
        visits/ + trips/ folder-notes be illustrated). Returns embeddable ImageResults.

        Resilient + polite: a brief `delay_s` between subjects smooths bursts (Wikimedia throttles
        rapid runs), and a subject that finds nothing OR errors (throttle/transient) is skipped, not
        fatal — the rest still come back. The CLI reports skipped subjects so they can be re-run."""
        root = vault_root or get_settings().tracker_path
        provider = get_image_provider(provider_name)
        results: list[ImageResult] = []
        for i, subject in enumerate(subjects):
            if i:
                time.sleep(delay_s)  # politeness throttle — not a limiter; just spaces the burst
            try:
                candidates = provider.search_images(subject, limit=1)
                if not candidates:
                    continue
                results.append(store_image(candidates[0], root, dest, subject))
            except (ProviderError, RuntimeError):
                continue  # throttled/transient — skip this subject, keep the rest
        return results

    def make_contact_sheet(
        self,
        dest: str | None = None,
        paths: list[str] | None = None,
        *,
        name: str | None = None,
        cols: int = 4,
        vault_root: Path | None = None,
    ) -> Path:
        """Tile a destination's `assets/` (by slug/path) OR an explicit list of image paths into one
        labeled grid PNG — the eyeball aid for reviewing a fetched batch in a single look before
        embedding. Written to `~/.cache/harness/contact-sheets/` (NOT the vault). Returns its path."""
        if paths:
            imgs = [Path(p) for p in paths]
        elif dest:
            root = vault_root or get_settings().tracker_path
            assets = root.joinpath(*dest_dir_parts(dest, root), "assets")
            imgs = sorted(p for p in assets.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        else:
            raise ValueError("make_contact_sheet: pass dest or paths")
        if not imgs:
            raise ValueError("make_contact_sheet: no images found")
        out = Path.home() / ".cache" / "harness" / "contact-sheets" / f"{name or dest or 'sheet'}.png"
        return build_contact_sheet(imgs, out, cols=cols)


# ---- traffic filter predicates (module-level: stateless, shared by get_traffic) ----


def _loc_on_road(loc: RoadwayLocation | None, road_code: str) -> bool:
    return loc is not None and loc.road_name == road_code


def _tt_matches(
    t: TravelTime, near: str | None, road_code: str | None, congested_only: bool
) -> bool:
    if congested_only and not t.congested:
        return False
    if road_code and not (_loc_on_road(t.start_point, road_code) or _loc_on_road(t.end_point, road_code)):
        return False
    if near and near.lower() not in f"{t.name} {t.description}".lower():
        return False
    return True


def _alert_matches(
    a: HighwayAlert, near: str | None, road_code: str | None, category: str | None
) -> bool:
    if category and category.lower() not in a.category.lower():
        return False
    if road_code and not (
        _loc_on_road(a.start_location, road_code) or _loc_on_road(a.end_location, road_code)
    ):
        return False
    if near:
        parts = [a.headline, a.extended_description]
        for loc in (a.start_location, a.end_location):
            if loc:
                parts.append(loc.description)
        if near.lower() not in " ".join(parts).lower():
            return False
    return True
