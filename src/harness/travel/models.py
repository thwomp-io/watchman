"""Shared data shapes (pydantic) used across providers, ranking, corpus, and adapters."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

ScreenVerdict = Literal["clean", "in_screen", "hard_no"]


class FlightQuery(BaseModel):
    """A flight search. `origins` is a list so a local airport + a hub can be compared in one rank."""

    origins: list[str]
    destination: str
    depart: date
    return_: date | None = None
    max_stops: int = 1


class PriceInsight(BaseModel):
    lowest_price: float | None = None
    price_level: str | None = None  # "low" | "typical" | "high"
    typical_low: float | None = None
    typical_high: float | None = None


class FlightOffer(BaseModel):
    carrier: str
    carrier_iata: str | None = None
    origin_iata: str
    dest_iata: str
    stops: int
    duration_minutes: int
    price_usd: float
    deep_link: str | None = None
    price_insight: PriceInsight | None = None

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0

    @property
    def duration_hours(self) -> float:
        return self.duration_minutes / 60.0


class ScreenStatus(BaseModel):
    geological: ScreenVerdict = "clean"
    social_crime: ScreenVerdict = "clean"
    calibration_notes: list[str] = Field(default_factory=list)


class Trip(BaseModel):
    """A trip or visit from the corpus pipeline — parsed from the `trip:` frontmatter block in
    corpus/travel/{trips,visits}/*.md (the machine-readable twin). The spine of
    the travel command-center: the whole horizon at a glance, not one trip in focus."""

    slug: str
    kind: str = "trip"  # "trip" (outbound/overnight) | "visit" (home-anchored: hosting/outing/event)
    status: str = "planning"  # idea|planning|shortlist|booked|active|completed|cancelled
    title: str = ""
    destination: str = ""
    travelers: list[str] = Field(default_factory=list)
    anchor: str = ""
    start: date | None = None
    end: date | None = None
    window: str = ""  # human hint when start/end aren't set yet (e.g. "fall/winter 2026")
    sort_date: date  # derived: start, else the filename date-prefix, else far-future
    doc: str = ""  # tracker-relative vault path (set by the reader) — lets a dashboard row deep-link
    # to its corpus doc (the bus-app table renders a `.md` cell as an "open ↗" VAULT link)


class Candidate(BaseModel):
    """A destination candidate, seeded from the trip + destination corpus docs."""

    slug: str
    display_name: str
    dest_iata: str
    origins: list[str]  # which origins to query (local airport + hub if served, else hub only)
    shape: str = ""
    lodging_anchors: list[str] = Field(default_factory=list)
    screen_status: ScreenStatus = Field(default_factory=ScreenStatus)


class ScoreBreakdown(BaseModel):
    total: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    rationale: list[str] = Field(default_factory=list)


class ScoredCandidate(BaseModel):
    candidate: Candidate
    best_flight: FlightOffer | None = None
    score: ScoreBreakdown = Field(default_factory=ScoreBreakdown)


class Shortlist(BaseModel):
    """Always a ranked shortlist (never a single pick) — the human keeps the final call."""

    window: str
    candidates: list[ScoredCandidate]
    generated_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class EventResult(BaseModel):
    """A live event at a destination during a window — the dynamic-characteristics layer.

    Raw + classified; the configured centerpiece/perk tiering is applied downstream by
    EventWeights.tier_for (NBA/NFL = centerpiece, everything else = perk), NOT stored here.
    """

    name: str
    segment: str = ""  # TM top-level: "Sports" | "Music" | "Arts & Theatre" | ...
    genre: str = ""  # e.g. "Basketball" | "Football" | "Rock"
    subgenre: str = ""  # the precise league/tag, e.g. "NBA" | "NFL" | "MLB" — drives tiering
    local_date: str = ""  # event localDate, YYYY-MM-DD
    local_time: str | None = None
    venue: str = ""
    city: str = ""
    url: str = ""  # Ticketmaster event page
    source: str = "ticketmaster"  # "ticketmaster" (live) | "reference" (static almanac merge)


class CityEvents(BaseModel):
    """Events found in one city for a window — the per-city grouping a multi-city scan returns."""

    city: str
    events: list[EventResult] = Field(default_factory=list)


class ImageCandidate(BaseModel):
    """Pre-download image metadata from a provider search (bytes fetched separately)."""

    subject: str  # what we searched for, e.g. "Hotel del Coronado"
    title: str  # the provider's title for the matched image/page
    image_url: str  # direct URL to the image bytes
    source: str  # provider name, e.g. "wikimedia"
    source_url: str = ""  # human-facing provenance page (attribution)
    attribution: str = ""
    width: int | None = None
    height: int | None = None


class ImageResult(BaseModel):
    """A downloaded + locally-stored image, ready to embed in a report.

    `rel_path` is relative to the tracker vault root, for Obsidian wikilink embeds.
    """

    subject: str
    source: str
    source_url: str = ""
    attribution: str = ""
    rel_path: str
    abs_path: str
    width: int
    height: int
    size_bytes: int

    def markdown(self, embed_width: int = 480) -> str:
        """Obsidian wikilink embed (vault-relative + width). The curator adds a caption line."""
        return f"![[{self.rel_path}|{embed_width}]]"


class HotelQuery(BaseModel):
    """A Google Hotels search for a destination over a date window — the lodging-research layer.

    ONE search returns a whole ranked *list* of properties (not one search per hotel), so a luxe-stay
    pitch of N hotels costs 1 of the free 250/mo quota. Defaults lean to the luxe-stay lens (4-5 star).
    """

    location: str
    check_in: date
    check_out: date
    adults: int = 2
    min_hotel_class: int = 4  # 4-5★ for the luxe-stay lens; 0 = any class
    min_rating: float | None = None  # overall_rating floor (e.g. 4.0)
    max_price: int | None = None  # per-night USD ceiling
    limit: int = 5  # how many properties to surface
    vacation_rentals: bool = False  # homes/cabins inventory (AirBnb-tier) instead of hotels


class NearbyPlace(BaseModel):
    """A point of interest near a property, with its primary travel time — serves the brief's
    walkable / calm-enclave / near-the-good-stuff dimensions."""

    name: str
    transport: str = ""  # "Walking" / "Taxi" / "Public transport"
    duration: str = ""  # "8 min"


class HotelOffer(BaseModel):
    """One bookable property from Google Hotels — name, nightly + total price, class, rating, photos,
    a prose blurb, nearby places with travel times, and the link where the next step is just booking it.
    (description / nearby_places / excluded_amenities / deal / image_urls all ride the same search free.)
    """

    name: str
    type: str = ""  # "hotel" / "vacation rental"
    hotel_class: int | None = None  # stars
    overall_rating: float | None = None
    reviews: int | None = None
    price_per_night_usd: float | None = None
    total_usd: float | None = None
    description: str = ""  # prose blurb — real pitch texture
    deal: str = ""  # "23% below usual" value signal, when present
    amenities: list[str] = Field(default_factory=list)
    excluded_amenities: list[str] = Field(default_factory=list)  # e.g. "No A/C" (matters in the desert)
    nearby_places: list[NearbyPlace] = Field(default_factory=list)
    image_url: str = ""  # hero photo (= image_urls[0])
    image_urls: list[str] = Field(default_factory=list)  # a few photos (free in the pricing response)
    booking_link: str = ""  # the next-step-is-booking link
    latitude: float | None = None
    longitude: float | None = None


class HotelSearch(BaseModel):
    """A hotel search result: the query echoed + the ranked offers + whether it came from the local
    date-keyed cache (so re-views cost zero quota — the 'don't burn the limit' guarantee)."""

    location: str
    check_in: date
    check_out: date
    nights: int
    from_cache: bool = False
    offers: list[HotelOffer] = Field(default_factory=list)


class GeoLocation(BaseModel):
    """A geocoded place — the lat/lon a weather lookup needs, plus context for display."""

    name: str
    latitude: float
    longitude: float
    admin1: str | None = None  # state/region, e.g. "Washington"
    country_code: str | None = None
    timezone: str | None = None


class DailyWeather(BaseModel):
    """One day's forecast. Units are carried on the parent WeatherForecast, not repeated here."""

    date: str  # YYYY-MM-DD (local to the forecast timezone)
    weather_code: int  # WMO code (-1 if absent)
    condition: str  # human-readable label for the code
    temp_max: float | None = None
    temp_min: float | None = None
    precip_prob: int | None = None  # max precip probability %, may be null
    precip_sum: float | None = None  # total precip in the parent's precip unit
    precip_hours: float | None = None  # hours WITH precipitation — the wet-day DURATION signal
    snowfall_sum: float | None = None  # total snowfall in the parent's precip unit (the snow flag)


class WeatherForecast(BaseModel):
    """A live daily forecast for a place over a window — the harness's weather *sense*.

    The dynamic counterpart to the corpus's climate-averages: real conditions on the trip dates.
    """

    location: str  # resolved display name, e.g. "Denver, Colorado, US"
    latitude: float
    longitude: float
    timezone: str = ""
    temperature_unit: str = "°F"
    precipitation_unit: str = "inch"
    days: list[DailyWeather] = Field(default_factory=list)


class DailyAirQuality(BaseModel):
    """One day's air-quality summary (aggregated from hourly). The 'is there wildfire smoke?' sense."""

    date: str  # YYYY-MM-DD
    us_aqi_max: int | None = None  # worst US AQI that day
    pm2_5_max: float | None = None  # worst PM2.5 (μg/m³)
    category: str = ""  # US AQI band: Good / Moderate / USG / Unhealthy / Very Unhealthy / Hazardous


class AirQualityReport(BaseModel):
    """Daily air-quality over a window — the dynamic smoke/AQI sense, sibling of WeatherForecast."""

    location: str
    latitude: float
    longitude: float
    timezone: str = ""
    current_us_aqi: int | None = None
    current_category: str = ""
    days: list[DailyAirQuality] = Field(default_factory=list)


class Earthquake(BaseModel):
    """A single seismic event near a place (USGS). Real data to calibrate the geological screen."""

    magnitude: float | None = None
    place: str = ""
    date: str = ""  # YYYY-MM-DD (UTC)
    depth_km: float | None = None
    url: str = ""


class SeismicReport(BaseModel):
    """Recent earthquakes within a radius of a place over a lookback — the geological-screen sense."""

    location: str
    latitude: float
    longitude: float
    radius_km: int
    since: str  # YYYY-MM-DD
    min_magnitude: float
    count: int = 0
    quakes: list[Earthquake] = Field(default_factory=list)


# ---- food / eateries: OSM Overpass enumeration + opt-in SerpAPI ratings ----


class Eatery(BaseModel):
    """One eatery near a place. Two-tier provenance: `osm` (keyless enumeration — what EXISTS) and
    `google` (quota ratings layer — what's GOOD); a merged row carries both via `sources`."""

    name: str
    category: str = ""  # restaurant | cafe | bar | pub | fast_food | ice_cream | bakery
    cuisine: str = ""  # OSM cuisine tag or Google type, human-ish ("ramen", "Japanese restaurant")
    address: str = ""
    website: str = ""
    opening_hours: str = ""  # raw OSM opening_hours when mapped
    rating: float | None = None  # Google rating (live-ratings tier only)
    reviews: int | None = None
    price: str = ""  # $..$$$$ (live-ratings tier only)
    sources: list[str] = Field(default_factory=list)


class FoodReport(BaseModel):
    """Eateries near a place — enumeration-first (OSM), ratings layered when spent deliberately."""

    location: str
    latitude: float
    longitude: float
    radius_m: int
    count: int = 0
    live_ratings: bool = False
    eateries: list[Eatery] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---- trip-prep enrichers (keyless): FX · holidays · sun · country facts ----


class FxRates(BaseModel):
    """Foreign-exchange rates from a base currency (open.er-api.com; keyless, daily). USD-base by
    default — USD-based, so `rates[X]` = units of X per 1 USD."""

    base: str
    date: str = ""  # provider's last-update date
    rates: dict[str, float] = Field(default_factory=dict)

    def per_usd(self, code: str) -> float | None:
        """Units of `code` per 1 base. (For 'how many USD is 1 EUR' invert: 1 / rates['EUR'].)"""
        return self.rates.get(code.upper())


class Holiday(BaseModel):
    date: str  # YYYY-MM-DD
    local_name: str = ""
    name: str = ""
    nationwide: bool = True  # the API's `global` flag (region-specific holidays = False)
    types: list[str] = Field(default_factory=list)


class Holidays(BaseModel):
    """Public holidays for a country-year (date.nager.at) — crowds/closures timing for trip-prep."""

    country: str
    year: int
    holidays: list[Holiday] = Field(default_factory=list)


class SunTimes(BaseModel):
    """Sunrise/sunset/twilight for a place-day (sunrise-sunset.org; keyless). Times are ISO-8601 UTC.

    Golden-hour fields are *approximations* (sunrise→+1h, sunset−1h→sunset) for photo/beach planning —
    the API gives the precise civil-twilight bounds, surfaced alongside."""

    date: str
    latitude: float
    longitude: float
    sunrise: str | None = None
    sunset: str | None = None
    solar_noon: str | None = None
    day_length_seconds: int | None = None
    civil_twilight_begin: str | None = None
    civil_twilight_end: str | None = None
    golden_hour_morning_end: str | None = None  # ~sunrise + 1h (approx)
    golden_hour_evening_begin: str | None = None  # ~sunset - 1h (approx)


class CountryFacts(BaseModel):
    """Trip-prep country facts (restcountries.com; keyless): currency, language, region, driving side."""

    name: str
    official_name: str = ""
    currencies: dict[str, str] = Field(default_factory=dict)  # code -> "Name (symbol)"
    languages: list[str] = Field(default_factory=list)
    region: str = ""
    subregion: str = ""
    capital: list[str] = Field(default_factory=list)
    driving_side: str = ""  # "left" | "right"
    timezones: list[str] = Field(default_factory=list)


# ---- WSDOT traffic (keyed-free, WA-regional): congestion deltas + highway alerts ----


class RoadwayLocation(BaseModel):
    """A point on a WA highway (WSDOT shape, shared by travel times + alerts)."""

    description: str = ""
    direction: str = ""  # "N" | "S" | "B" (both) | "EB" | ...
    latitude: float | None = None
    longitude: float | None = None
    milepost: float | None = None
    road_name: str = ""  # WSDOT code: "005"=I-5, "405", "522", "002"=US-2


class TravelTime(BaseModel):
    """A WSDOT-instrumented route's live vs typical drive time — the congestion-delta signal.

    `delay_minutes` = current − average (None when either reading is unavailable); `congested` is
    True when the delay meets the threshold passed at fetch time.
    """

    route_id: int
    name: str
    description: str = ""
    distance_miles: float | None = None
    average_minutes: int | None = None
    current_minutes: int | None = None
    delay_minutes: int | None = None
    congested: bool = False
    start_point: RoadwayLocation | None = None
    end_point: RoadwayLocation | None = None
    updated: str | None = None  # ISO-8601 UTC


class HighwayAlert(BaseModel):
    """A WSDOT highway alert — construction / closure / incident / maintenance / special event."""

    alert_id: int
    category: str = ""  # "Construction" | "Lane Closure" | "Incident" | "Maintenance" | ...
    status: str = ""  # "Open" | ...
    priority: str = ""  # "Highest" | "High" | "Medium" | "Low" | "Lowest"
    region: str = ""
    county: str | None = None
    headline: str = ""
    extended_description: str = ""
    start_location: RoadwayLocation | None = None
    end_location: RoadwayLocation | None = None
    start_time: str | None = None  # ISO-8601 UTC
    end_time: str | None = None  # ISO-8601 UTC (open-ended alerts have None)
    last_updated: str | None = None


class TrafficReport(BaseModel):
    """A filtered WA-traffic snapshot (WSDOT): live travel-time deltas + highway alerts, plus an
    echo of the filters applied so the consumer knows the scope of what it's looking at."""

    travel_times: list[TravelTime] = Field(default_factory=list)
    alerts: list[HighwayAlert] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)


# ---- WSF ferries (keyed-free, the WSDOT ferry slice): schedule + live space + vessels ----


class FerrySailing(BaseModel):
    """A scheduled WSF sailing for a route (times are ISO-8601 UTC; arriving_time often None)."""

    departing_time: str | None = None
    arriving_time: str | None = None
    vessel_name: str = ""
    vessel_id: int | None = None


class FerrySpaceDeparture(BaseModel):
    """Live drive-up space for one upcoming departure (flattened to the first arrival terminal)."""

    departure: str | None = None  # ISO-8601 UTC
    vessel_name: str = ""
    is_cancelled: bool = False
    max_space: int | None = None
    drive_up_available: int | None = None
    reservable_available: int | None = None
    arrival_terminal: str = ""


class FerryTerminalSpace(BaseModel):
    """A WSF terminal's upcoming departures with live drive-up space — the 'will it be full' layer."""

    terminal_name: str
    terminal_abbrev: str = ""
    departures: list[FerrySpaceDeparture] = Field(default_factory=list)


class FerryVessel(BaseModel):
    """A WSF vessel's live position + status — the 'where's the boat / is it running' layer."""

    name: str
    departing_terminal: str = ""
    arriving_terminal: str = ""
    latitude: float | None = None
    longitude: float | None = None
    speed: float | None = None
    heading: float | None = None
    in_service: bool = True
    at_dock: bool = False
    eta: str | None = None  # ISO-8601 UTC
    left_dock: str | None = None  # ISO-8601 UTC
    route: list[str] = Field(default_factory=list)  # OpRouteAbbrev (e.g. ["edm-king"])


class FerryReport(BaseModel):
    """A WSF snapshot for a query: today's sailings for a route (when asked) + live drive-up space +
    live vessel positions. Any section may be empty depending on the flags/route requested."""

    route: str | None = None
    sailings: list[FerrySailing] = Field(default_factory=list)
    space: list[FerryTerminalSpace] = Field(default_factory=list)
    vessels: list[FerryVessel] = Field(default_factory=list)


# ---- flight research (the rich, cabin-aware deepen-after-pick artifact; sibling of HotelSearch) ----


class FlightLeg(BaseModel):
    """One flight segment within an itinerary — the per-leg detail the thin FlightOffer discards.

    Times are LOCAL airport clock times ('YYYY-MM-DD HH:MM', no tz) exactly as Google returns them.
    """

    airline: str = ""
    flight_number: str = ""
    airplane: str = ""
    depart_airport: str = ""  # IATA id
    depart_name: str = ""
    depart_time: str = ""  # "YYYY-MM-DD HH:MM" local
    arrive_airport: str = ""
    arrive_name: str = ""
    arrive_time: str = ""
    duration_minutes: int | None = None
    travel_class: str = ""  # "Economy" | "Premium economy" | "Business" | "First Class"
    legroom: str | None = None  # e.g. "31 in" (present-when-reported)


class FlightLayover(BaseModel):
    airport: str = ""  # IATA id
    name: str = ""
    duration_minutes: int | None = None


class FlightItinerary(BaseModel):
    """A full flight option (legs + layovers) at a price + cabin — richer than the ranking FlightOffer."""

    origin_iata: str
    dest_iata: str
    price_usd: float
    cabin: str = ""  # requested/derived cabin ("Economy" | "First Class" | ...)
    total_duration_minutes: int | None = None
    stops: int = 0
    legs: list[FlightLeg] = Field(default_factory=list)
    layovers: list[FlightLayover] = Field(default_factory=list)
    booking_token: str | None = None  # SerpAPI departure_token (not a URL — for a follow-up call)
    carbon_kg: int | None = None

    @property
    def is_nonstop(self) -> bool:
        return self.stops == 0

    @property
    def depart_time(self) -> str:
        return self.legs[0].depart_time if self.legs else ""

    @property
    def arrive_time(self) -> str:
        return self.legs[-1].arrive_time if self.legs else ""

    @property
    def primary_airline(self) -> str:
        return self.legs[0].airline if self.legs else ""

    @property
    def min_legroom(self) -> str | None:
        rooms = [leg.legroom for leg in self.legs if leg.legroom]
        return min(rooms, key=_legroom_inches) if rooms else None


class FlightSearch(BaseModel):
    """Cabin-aware flight research for a dest+window across one or more origins — the persisted
    deepen-after-pick artifact.

    `origins` are the QUERIED origins (e.g. the local/home airport + a hub for a destination the local
    airport serves). A small local airport is fully queryable but may have a sparse schedule, so it can
    return no service for a given window (→ no home-origin options) even when the destination is on its
    route map. `options` spans origins × cabins, each itinerary tagged with its `origin_iata` + `cabin`.
    The airport identities are config-driven (`FlightWeights.home_airport` / `comparison_airport`) and
    carried here so the render layer stays free of hardcoded airport codes.
    """

    origins: list[str]
    dest_iata: str
    depart: date
    return_: date | None = None
    round_trip: bool = False
    home_airport_served: bool = False  # dest on the local airport's route map (service still date-dependent)
    home_airport: str = ""  # the local/convenience origin IATA (config-driven; "" → hub-only)
    comparison_airport: str = ""  # the hub origin IATA used for the price/frequency comparison
    home_airport_note: str = ""  # the convenience tagline for the local airport (e.g. "10 min from home")
    cabins: list[str] = Field(default_factory=list)
    options: list[FlightItinerary] = Field(default_factory=list)
    price_insight: PriceInsight | None = None  # from the hub economy search — "is this fare high/low"

    def cheapest(self, cabin: str, *, origin: str | None = None) -> FlightItinerary | None:
        pool = [
            o
            for o in self.options
            if o.cabin.lower().startswith(cabin.lower()) and (origin is None or o.origin_iata == origin)
        ]
        return min(pool, key=lambda o: o.price_usd) if pool else None

    def origins_with_service(self) -> list[str]:
        """The queried origins that actually returned options (the local airport drops out when it
        doesn't fly the window) — preserves `origins` order so the local airport leads when it serves."""
        return [o for o in self.origins if any(it.origin_iata == o for it in self.options)]

    def cheapest_overall(self, origin: str) -> FlightItinerary | None:
        pool = [o for o in self.options if o.origin_iata == origin]
        return min(pool, key=lambda o: o.price_usd) if pool else None


def _legroom_inches(legroom: str) -> float:
    """'31 in' → 31.0 for comparison; unparseable sorts last."""
    try:
        return float(legroom.split()[0])
    except (ValueError, IndexError):
        return 999.0
