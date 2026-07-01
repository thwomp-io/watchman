"""MCP server adapter (FastMCP) — the Claude-native surface. Thin wrapper over TravelService.

Each tool is a few lines calling service.py and returning JSON-serializable model dumps.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP

from harness.travel.service import TravelService

mcp = FastMCP("harness-travel")


def _svc() -> TravelService:
    return TravelService()


@mcp.tool()
def rank_destinations(trip_slug: str) -> dict[str, Any]:
    """Rank a trip's candidate destinations (from the corpus) against live flight data,
    applying the home-airport-convenience weight + composite screens. Returns a ranked shortlist
    (never a single pick) with an explainable rationale per candidate."""
    return _svc().rank_trip(trip_slug).model_dump()


@mcp.tool()
def search_flights(
    destination: str, depart: str, return_date: str | None = None, origins: str | None = None
) -> list[dict[str, Any]]:
    """Search flights to an IATA destination across one or more origin airports. `origins` defaults to
    the configured home airport + hub. Dates are YYYY-MM-DD. Budget carriers are excluded per prefs."""
    svc = _svc()
    origin_list = (
        [o.strip().upper() for o in origins.split(",")]
        if origins
        else svc.weights.flight.query_origins(home_served=True)
    )
    offers = svc.search_flights(
        origins=origin_list,
        destination=destination.upper(),
        depart=date.fromisoformat(depart),
        return_=date.fromisoformat(return_date) if return_date else None,
    )
    return [o.model_dump() for o in offers]


@mcp.tool()
def research_flights(
    destination: str,
    depart: str,
    return_date: str | None = None,
    origins: str | None = None,
    cabins: str = "economy,first",
    limit: int = 3,
    report_dest: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Cabin-aware flight research across origins — the deepen-after-pick flights artifact (twin of
    find_hotels). Default `origins` (config-driven): the **home airport + hub** for a destination the
    home airport serves (the home airport = the convenience win, leads; the hub = price/frequency
    comparison), else the hub only. A small home airport is fully queryable — it just has a sparse
    schedule, so it returns no options when it doesn't fly the window (a real signal, not a gap).
    Queries each origin × cabin (default 'economy,first'), returning rich options with real times,
    per-leg carrier/aircraft/legroom, layovers, carbon + a first-class-upgrade verdict and a
    home-vs-hub convenience-vs-price read.
    **Opt-in / quota-spending: ~1 SerpAPI search PER ORIGIN × CABIN (default 4) — use deliberately.**
    Dates YYYY-MM-DD. `report_dest` set → also writes window-stamped {report_dest}/flights/{depart}.md
    (`force` to overwrite). Returns {origins, dest_iata, depart, return_, round_trip, home_airport_served,
    cabins, price_insight, options:[{origin_iata, cabin, price_usd, stops, legs:[...], layovers}]}."""
    svc = _svc()
    search = svc.research_flights(
        destination,
        date.fromisoformat(depart),
        date.fromisoformat(return_date) if return_date else None,
        origins=[o.strip().upper() for o in origins.split(",")] if origins else None,
        cabins=tuple(c.strip().lower() for c in cabins.split(",") if c.strip()),
        limit=limit,
    )
    out = search.model_dump(mode="json")
    if report_dest:
        out["report_path"] = str(svc.write_flights_report(search, report_dest, force=force))
    return out


@mcp.tool()
def find_hotels(
    location: str,
    check_in: str,
    check_out: str,
    adults: int = 2,
    min_hotel_class: int = 4,
    max_price: int | None = None,
    limit: int = 5,
    refresh: bool = False,
) -> dict[str, Any]:
    """Tangible bookable properties for a destination+dates — the lodging-research layer (Google
    Hotels via SerpAPI). Turns a luxe-stay pitch into named, priced, photographed, next-step-is-booking
    options. **ONE search returns the whole ranked list** (not one per hotel); a date-keyed cache makes
    re-views cost ZERO quota (`refresh=True` forces fresh). Defaults to the 4-5★ luxe lens. Dates
    YYYY-MM-DD. **Opt-in / quota-spending (shares SERPAPI's 250/mo with flights) — use deliberately.**
    Returns {location, check_in, check_out, nights, from_cache, offers:[{name, hotel_class,
    overall_rating, price_per_night_usd, total_usd, amenities, image_url, booking_link, lat, lon}]}."""
    return (
        _svc()
        .search_hotels(
            location,
            date.fromisoformat(check_in),
            date.fromisoformat(check_out),
            adults=adults,
            min_hotel_class=min_hotel_class,
            max_price=max_price,
            limit=limit,
            refresh=refresh,
        )
        .model_dump(mode="json")
    )


@mcp.tool()
def read_preferences() -> dict[str, Any]:
    """Return the parsed travel-preferences digest (avoided airlines, lodging bar, home-airport pref)."""
    d = _svc().read_preferences()
    return {
        "avoid_airline_names": d.avoid_airline_names,
        "lodging_bar": d.lodging_bar,
        "home_airport_preferred": d.home_airport_preferred,
    }


@mcp.tool()
def fetch_destination_images(
    dest: str, subjects: list[str], provider: str = "wikimedia"
) -> list[dict[str, Any]]:
    """Fetch + locally store images (hotels / attractions / views) for a report, under the doc's
    assets/ dir. `dest` is a bare destination slug (→ travel/destinations/{slug}/assets) OR a vault
    path under travel/ (→ travel/{dest}/assets, e.g. 'visits/2026-05-example-visit' — like make_diagram's
    dest). Returns embeddable results incl. an Obsidian wikilink `markdown` field. provider
    'wikimedia' is keyless + quota-free (best for landmarks); google_hotels / pexels / unsplash slot
    in later. Images are personal-use."""
    results = _svc().fetch_destination_images(dest, subjects, provider_name=provider)
    return [{**r.model_dump(), "markdown": r.markdown()} for r in results]


@mcp.tool()
def make_contact_sheet(
    dest: str | None = None, paths: list[str] | None = None, cols: int = 4, name: str | None = None
) -> dict[str, str]:
    """Tile a batch of fetched images into ONE labeled grid PNG for fast eyeballing before embedding
    (the eyeball-before-embed discipline — review a whole destination's candidates in a single look,
    catching wrong-place mis-resolves). Pass `dest` (slug/vault-path → tiles its assets/) OR `paths`
    (explicit image paths). Writes to ~/.cache/harness/contact-sheets/ (NOT the vault) and
    returns the path to Read."""
    out = _svc().make_contact_sheet(dest=dest, paths=paths, cols=cols, name=name)
    return {"contact_sheet": str(out)}


@mcp.tool()
def find_events(
    cities: list[str], start_date: str, end_date: str, category: str | None = None
) -> list[dict[str, Any]]:
    """Find live events (games / concerts / festivals / theatre) across one or more cities within a
    date window — the dynamic-destination layer. Dates are YYYY-MM-DD. `category` optionally filters
    the TM classification (Sports / Music / 'Arts & Theatre'). Returns one entry per city:
    {city, events:[...]}, each event carrying a `tier`: 'centerpiece' (NBA/NFL — trip-around-able)
    or 'perk' (bonus on an otherwise-good destination, NEVER the reason to pick it). Note: TM
    city-matching is unreliable for some markets — treat a 0 from a big event-city as suspect."""
    svc = _svc()
    scans = svc.scan_events(
        cities,
        date.fromisoformat(start_date),
        date.fromisoformat(end_date),
        classification=category,
    )
    return [
        {
            "city": s.city,
            "events": [{**e.model_dump(), "tier": svc.event_tier(e)} for e in s.events],
        }
        for s in scans
    ]


@mcp.tool()
def find_trip_events(trip_slug: str, category: str | None = None) -> list[dict[str, Any]]:
    """Trip-scoped events: scan every candidate-destination city of a trip (from the corpus) for the
    trip window. Returns one {city, events:[...]} entry per mappable candidate, each event carrying
    a `tier` ('centerpiece' = NBA/NFL, else 'perk'). The events twin of rank_destinations. (For
    ad-hoc cities not tied to a trip, use find_events instead.)"""
    svc = _svc()
    scans = svc.scan_trip_events(trip_slug, classification=category)
    return [
        {
            "city": s.city,
            "events": [{**e.model_dump(), "tier": svc.event_tier(e)} for e in s.events],
        }
        for s in scans
    ]


@mcp.tool()
def find_reference(
    start_date: str, end_date: str, city: str | None = None
) -> list[dict[str, Any]]:
    """Static-almanac entries (known centerpiece games + holidays + mega-events) in a window — the
    PROACTIVE layer read from corpus/travel/reference/*.md, complementing the reactive live
    find_events scan. Dates YYYY-MM-DD. `city` keeps located entries matching it plus
    all window-wide ones (holidays/personal dates). Each entry carries source='reference' + a `tier`
    ('centerpiece' for NFL games). Confirm a specific game/price live before acting on it."""
    svc = _svc()
    ref = svc.scan_reference(
        date.fromisoformat(start_date), date.fromisoformat(end_date), city=city
    )
    return [{**e.model_dump(), "tier": svc.event_tier(e)} for e in ref]


@mcp.tool()
def list_trips(include_past: bool = False) -> list[dict[str, str]]:
    """The travel horizon — every upcoming trip + visit from the corpus `trip:` frontmatter blocks
    (corpus/travel/{trips,visits}/), countdown-ordered soonest-first. Each row: {when, date, status,
    kind (trip|visit), destination, anchor, who}. `include_past` appends completed/cancelled history
    (most-recent first). The command-center spine — read-only, deterministic (no tag-guessing)."""
    return _svc().trip_pipeline(date.today(), include_past=include_past)


@mcp.tool()
def get_weather(
    city: str, start_date: str, end_date: str, fahrenheit: bool = True
) -> dict[str, Any]:
    """Live daily forecast for a place over a window — the dynamic weather sense (Open-Meteo, keyless,
    no quota). Dates YYYY-MM-DD. Returns {location, timezone, units, days:[{date, condition,
    weather_code, temp_max, temp_min, precip_prob, precip_sum, precip_hours, snowfall_sum}]} —
    precip_hours is the wet-day DURATION + snowfall_sum the snow signal. Use REAL conditions on the
    trip dates instead of climate-averages (the corpus weather-weight is a prior, not a forecast).
    Horizon is ~16 days; longer-range raises an error (climate-normals planned)."""
    return (
        _svc()
        .get_weather(
            city,
            date.fromisoformat(start_date),
            date.fromisoformat(end_date),
            fahrenheit=fahrenheit,
        )
        .model_dump()
    )


@mcp.tool()
def get_air_quality(city: str, start_date: str, end_date: str) -> dict[str, Any]:
    """Daily air-quality (US AQI / PM2.5 max) for a place over a window — the wildfire-smoke sense
    (Open-Meteo, keyless). Dates YYYY-MM-DD; AQI horizon ~5 days. Returns {location, current_us_aqi,
    current_category, days:[{date, us_aqi_max, pm2_5_max, category}]}. Use when smoke/air is a real
    trip-quality question (esp. wildfire-smoke seasons)."""
    return (
        _svc()
        .get_air_quality(city, date.fromisoformat(start_date), date.fromisoformat(end_date))
        .model_dump()
    )


@mcp.tool()
def find_food(
    near: str, radius_m: int = 1500, live_ratings: bool = False
) -> dict[str, Any]:
    """Eateries near a place — two-tier food discovery (what exists + optionally what's good).
    Default tier is OSM Overpass (KEYLESS/free): every mapped eatery with name/cuisine/hours —
    enumeration from data, not memory. live_ratings=True merges Google ratings/price via SerpAPI
    (QUOTA: ~1 search of the shared 250/mo, day-cached — CONFIRM with the maintainer before spending).
    Returns {location, count, eateries:[{name, category, cuisine, rating, reviews, price,
    opening_hours, address, website, sources}], notes}."""
    return _svc().find_food(near, radius_m=radius_m, live_ratings=live_ratings).model_dump()


@mcp.tool()
def get_earthquakes(
    city: str, radius_km: int = 300, days_back: int = 90, min_magnitude: float = 2.5
) -> dict[str, Any]:
    """Recent earthquakes near a place — the geological-screen sense (USGS, keyless). Returns
    {location, count, radius_km, since, quakes:[{magnitude, place, date, depth_km, url}]}. Use to
    calibrate the geological risk screen with real seismic data (data-pushback, not categorical
    priors) — e.g. 'how active is it actually near a given destination lately'."""
    return (
        _svc()
        .get_earthquakes(
            city, radius_km=radius_km, days_back=days_back, min_magnitude=min_magnitude
        )
        .model_dump()
    )


@mcp.tool()
def fx_rates(symbols: list[str] | None = None, base: str = "USD") -> dict[str, Any]:
    """Exchange rates from `base` (default USD; keyless, open.er-api.com). rates[X] = units of X per 1
    base; invert (1/rate) for 'X in base'. `symbols` filters the targets (default: all). Trip-prep
    cost framing + finance-useful."""
    return _svc().fx_rates(symbols or None, base=base).model_dump()


@mcp.tool()
def public_holidays(country: str, year: int) -> dict[str, Any]:
    """Public holidays for a country-year (keyless, date.nager.at). `country` is ISO-3166 alpha-2
    (US/JP/MX). Trip-prep crowds/closures timing — flags dates likely to be busy/closed."""
    return _svc().public_holidays(country, year).model_dump()


@mcp.tool()
def sun_times(
    place: str | None = None, date: str = "", latitude: float | None = None, longitude: float | None = None
) -> dict[str, Any]:
    """Sunrise/sunset/twilight + golden-hour approximations for a place-day (keyless,
    sunrise-sunset.org). Pass `place` (geocoded) OR `latitude`+`longitude`, plus `date` (YYYY-MM-DD).
    Times are ISO-8601 UTC; golden-hour fields are ~approximations (sunrise+1h / sunset-1h) for
    beach/photo planning. day_length_seconds included."""
    return _svc().sun_times(place, latitude=latitude, longitude=longitude, date_str=date).model_dump()


@mcp.tool()
def country_facts(name: str) -> dict[str, Any]:
    """Trip-prep facts for a country (keyless, restcountries.com): currencies, languages, region,
    capital, driving side, timezones. Int'l-prep enricher."""
    return _svc().country_facts(name).model_dump()


@mcp.tool()
def get_traffic(
    near: str | None = None,
    road: str | None = None,
    category: str | None = None,
    congested_only: bool = False,
    times_only: bool = False,
    alerts_only: bool = False,
) -> dict[str, Any]:
    """Live WA traffic from WSDOT (keyed-free): travel-time congestion deltas (Current vs Average
    minutes on instrumented corridors in your region) + highway alerts
    (construction / closure / incident / maintenance). Filter with `near` (case-insensitive substring,
    e.g. a town name), `road` ('I-5' / '405' / 'US-2', normalized to WSDOT's code), `category`
    ('Construction' / 'Incident' / 'Lane Closure'). `congested_only` keeps routes delayed >= 5 min.
    `times_only` / `alerts_only` restrict the surface. Returns {travel_times:[...], alerts:[...],
    filters}; each travel_time carries delay_minutes + a `congested` flag. WA-regional — the
    instrumented corridors in your region."""
    return (
        _svc()
        .get_traffic(
            near=near,
            road=road,
            category=category,
            congested_only=congested_only,
            include_times=not alerts_only,
            include_alerts=not times_only,
        )
        .model_dump()
    )


@mcp.tool()
def get_ferry(
    route: str | None = None,
    space: bool = False,
    vessels: bool = False,
    all_today: bool = False,
) -> dict[str, Any]:
    """Washington State Ferries from WSDOT (keyed-free). With `route` (a 'Departing-Arriving'
    terminal pair, e.g. 'Seattle-Bainbridge Island'): today's sailing times for that route, plus — if `space`
    — the departing terminal's live drive-up space ('will it be full'), and — if `vessels` — the boats
    serving it (position/ETA/in-service). Without `route`: `space` returns every terminal's live space,
    `vessels` every vessel. `all_today` includes already-departed sailings. Times are ISO-8601 UTC
    (convert to Pacific for display). Returns {route, sailings:[...], space:[...], vessels:[...]}.
    The ferry-dependent-itinerary layer for the WA-regional ferry network."""
    return (
        _svc()
        .get_ferry(route=route, space=space, vessels=vessels, only_remaining=not all_today)
        .model_dump()
    )


@mcp.tool()
def read_trip(trip_slug: str) -> dict[str, Any]:
    """Return a trip's window + candidate destinations seeded from the corpus (no flight search)."""
    plan = _svc().read_trip_plan(trip_slug)
    return {
        "slug": plan.slug,
        "window": plan.window,
        "depart": plan.depart.isoformat(),
        "return": plan.return_.isoformat() if plan.return_ else None,
        "candidates": [c.model_dump() for c in plan.candidates],
    }


@mcp.tool()
def make_diagram(
    diagram_type: str, data: dict[str, Any], dest: str, name: str = "diagram", grid: bool = False
) -> dict[str, Any]:
    """Render a D3 diagram to static SVG in the corpus + return its Obsidian embed.

    The agent-native path: build the diagram `data` in-context (e.g. a curated schedule, or a
    timeline assembled from find_events results), then render it. `dest` is a vault path under travel/
    (e.g. 'trips/2026-09-fall-trip'); the SVG lands at {dest}/visuals/{name}.svg.

    For diagram_type='map-annotate': `data` carries an `image` (path under the tracker corpus, e.g.
    'screenshots/SCR-….png') + fractional `pins`/`route`/`notes`/`title`/`subtitle`/`legendTitle`
    (coords are 0–1 of image w/h). Pins auto-number in order; `star:true` rings one. `grid=true`
    overlays a 0.1 coordinate grid so you can read off pin fractions, then re-render without it.
    Returns {path, embed}."""
    from pathlib import Path

    from harness.travel.config.settings import get_settings
    from harness.travel.viz import embed_markdown, render_diagram, render_map_annotate

    s = get_settings()
    out = Path(s.travel_corpus_path) / dest / "visuals" / f"{name}.svg"
    if diagram_type == "map-annotate":
        written = render_map_annotate(data, out, image_base=s.tracker_path, grid=grid)
    else:
        written = render_diagram(diagram_type, data, out)
    return {"path": str(written), "embed": embed_markdown(written)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
