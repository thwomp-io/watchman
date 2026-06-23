"""Typer CLI adapter (Bash-callable / cron-able). Thin wrapper over TravelService."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from harness.packs import PackGroup
from harness.travel.conditions import ConditionsReport
from harness.travel.config.settings import get_settings
from harness.travel.models import CityEvents, EventResult, Shortlist, TravelTime
from harness.travel.providers.base import ProviderError
from harness.travel.service import TravelService
from harness.travel.viz import (
    VizError,
    embed_markdown,
    events_to_timeline,
    reference_to_calendar,
    render_diagram,
    render_map_annotate,
    weather_to_strip,
)

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn travel plan --pack …)
    add_completion=False,
    help="Live-travel hands for the harness: SerpAPI flight search + corpus-aware ranking.",
)
console = Console()


def _svc(provider: str) -> TravelService:
    return TravelService(flight_provider_name=provider)


@app.command()
def prefs() -> None:
    """Print the parsed preferences digest (sanity-check the corpus read)."""
    d = _svc("serpapi").read_preferences()
    console.print("[bold]Travel preferences digest[/bold]")
    console.print(f"  Avoided airlines : {', '.join(d.avoid_airline_names) or '(none found)'}")
    console.print(f"  Lodging bar      : {d.lodging_bar}")
    console.print(f"  Home airport pref: {d.home_airport_preferred}")


@app.command()
def search(
    to: str = typer.Option(..., "--to", help="Destination IATA (e.g. DEN)"),
    depart: str = typer.Option(..., "--depart", help="YYYY-MM-DD"),
    ret: str | None = typer.Option(None, "--return", help="YYYY-MM-DD (round trip)"),
    origins: str | None = typer.Option(
        None, "--from", help="Comma-separated origin IATAs (default: configured home airport + hub)"
    ),
    provider: str = typer.Option("serpapi", "--provider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Raw flight search across one or more origins."""
    svc = _svc(provider)
    origin_list = (
        [o.strip().upper() for o in origins.split(",")]
        if origins
        else svc.weights.flight.query_origins(home_served=True)
    )
    try:
        offers = svc.search_flights(
            origins=origin_list,
            destination=to.upper(),
            depart=date.fromisoformat(depart),
            return_=date.fromisoformat(ret) if ret else None,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps([o.model_dump() for o in offers]))
        return
    table = Table(title=f"Flights → {to.upper()}")
    for col in ("Origin", "Carrier", "Stops", "Duration", "Price"):
        table.add_column(col)
    for o in offers:
        table.add_row(
            o.origin_iata, o.carrier, str(o.stops),
            f"{o.duration_hours:.1f}h", f"${o.price_usd:.0f}",
        )
    console.print(table)


@app.command()
def flights(
    to: str = typer.Option(..., "--to", help="Destination IATA (e.g. SAN)"),
    depart: str = typer.Option(..., "--depart", help="YYYY-MM-DD"),
    ret: str | None = typer.Option(None, "--return", help="YYYY-MM-DD (round trip)"),
    origins: str | None = typer.Option(
        None, "--origins", help="Comma-separated origins (default: home airport + hub when served, else hub)"
    ),
    cabins: str = typer.Option(
        "economy,first", "--cabins", help="Comma-separated: economy,premium,business,first"
    ),
    limit: int = typer.Option(3, "--limit", help="Options per cabin"),
    report: str = typer.Option(
        "", "--report", help="Write a {dest}/flights/ report (slug or vault path under travel/)"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing flights report"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Cabin-aware flight research across origins — the configured home airport + a hub side-by-side
    for served dests, economy vs first, real times/layovers/legroom + a first-class-upgrade verdict.
    The flights twin of `hn travel hotels`; `--report` persists a {dest}/flights/ doc. ~1 search per
    origin × cabin (default 4)."""
    from harness.travel.flights import _home_vs_hub, fmt_dur, fmt_time, upgrade_verdict

    cabin_list = [c.strip().lower() for c in cabins.split(",") if c.strip()]
    origin_list = [o.strip().upper() for o in origins.split(",")] if origins else None
    try:
        search = TravelService().research_flights(
            to,
            date.fromisoformat(depart),
            date.fromisoformat(ret) if ret else None,
            origins=origin_list,
            cabins=tuple(cabin_list),
            limit=limit,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(search.model_dump(mode="json")))
    else:
        trip = "round trip" if search.round_trip else "one way"
        win = f"{search.depart}" + (f" → {search.return_}" if search.return_ else "")
        orgs = "/".join(search.origins)
        console.print(
            f"\n[bold]Flights {orgs} → {search.dest_iata}[/bold] [dim]{win} · {trip}[/dim]"
        )
        hvh = _home_vs_hub(search)
        if hvh:
            console.print(f"  {hvh}")
        serving = search.origins_with_service()
        if not serving:
            console.print("[dim]  (no flights returned for any queried origin)[/dim]")
        for origin in serving:
            v = upgrade_verdict(search, origin=origin)
            label = (
                f"{origin} ({search.home_airport_note})"
                if origin == search.home_airport and search.home_airport_note
                else origin
            )
            console.print(f"\n[bold]From {label}[/bold]" + (f"\n  {v}" if v else ""))
            for cabin in search.cabins:
                pool = sorted(
                    (
                        o
                        for o in search.options
                        if o.origin_iata == origin and o.cabin.lower().startswith(cabin.lower())
                    ),
                    key=lambda o: o.price_usd,
                )
                console.print(f"[bold]{cabin.capitalize()}[/bold] [dim]({len(pool)})[/dim]")
                if not pool:
                    console.print("[dim]  (none)[/dim]")
                    continue
                table = Table()
                for col in ("Price", "Depart", "Arrive", "Carrier", "Stops", "Dur", "Legroom"):
                    table.add_column(col)
                for o in pool:
                    table.add_row(
                        f"${o.price_usd:,.0f}", fmt_time(o.depart_time), fmt_time(o.arrive_time),
                        o.primary_airline, "nonstop" if o.is_nonstop else f"{o.stops}",
                        fmt_dur(o.total_duration_minutes), o.min_legroom or "—",
                    )
                console.print(table)

    if report:
        try:
            path = TravelService().write_flights_report(search, report, force=force)
            console.print(f"\n[bold green]Flights report written:[/bold green] {path}")
        except FileExistsError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e


@app.command()
def rank(
    trip: str = typer.Option(..., "--trip", help="Trip slug, e.g. 2026-09-birthday-window"),
    provider: str = typer.Option("serpapi", "--provider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Rank a trip's candidate destinations against live flight data + the screens/home-airport weight."""
    try:
        shortlist = _svc(provider).rank_trip(trip)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(shortlist.model_dump_json())
        return
    _render_shortlist(shortlist)


@app.command()
def images(
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Destination slug (e.g. san-diego → destinations/{slug}/assets) OR a vault path "
        "under travel/ (e.g. visits/2026-05-30-family-visit → travel/{dest}/assets), like `viz --dest`",
    ),
    subjects: str = typer.Option(
        ..., "--subjects", help="Comma-separated subjects (hotels / attractions / views)"
    ),
    provider: str = typer.Option("wikimedia", "--provider", help="wikimedia (keyless / free)"),
    width: int = typer.Option(480, "--width", help="Embed width for the suggested markdown"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch + locally store images for a destination report; print embed-ready markdown."""
    subj_list = [s.strip() for s in subjects.split(",") if s.strip()]
    try:
        results = TravelService().fetch_destination_images(
            dest, subj_list, provider_name=provider
        )
    except (ProviderError, RuntimeError) as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps([r.model_dump() for r in results]))
        return

    table = Table(title=f"Images → {dest}")
    for col in ("Subject", "Source", "Size", "Dims", "Path"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.subject, r.source, f"{r.size_bytes // 1024}KB",
            f"{r.width}x{r.height}", r.rel_path,
        )
    console.print(table)

    console.print("\n[bold]Embed-ready markdown[/bold] (paste into the report, add a caption):")
    for r in results:
        console.print(r.markdown(width), markup=False)  # markup=False: keep the [[...]] literal
        console.print(f"[dim]*{r.subject}* — {r.attribution}[/dim]")

    found = {r.subject for r in results}
    missing = [s for s in subj_list if s not in found]
    if missing:
        console.print(f"\n[yellow]No image found for:[/yellow] {', '.join(missing)}")


@app.command("contact-sheet")
def contact_sheet(
    dest: str = typer.Option(
        None, "--dest", help="Destination slug or vault path — tiles its assets/ images"
    ),
    paths: str = typer.Option(None, "--paths", help="Comma-separated image paths (instead of --dest)"),
    cols: int = typer.Option(4, "--cols", help="Grid columns"),
    name: str = typer.Option(None, "--name", help="Output filename stem (default: the dest slug)"),
) -> None:
    """Tile fetched images into ONE labeled grid PNG for fast eyeballing before embedding.

    Written to ~/.cache/harness/contact-sheets/ (NOT the vault — a throwaway eyeball aid).
    """
    try:
        out = TravelService().make_contact_sheet(
            dest=dest, paths=[p.strip() for p in paths.split(",")] if paths else None,
            cols=cols, name=name,
        )
    except (ValueError, OSError) as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    console.print(f"Contact sheet: {out}")


@app.command()
def events(
    trip: str | None = typer.Option(
        None, "--trip", help="Trip slug — scan its candidate cities for the trip window"
    ),
    city: list[str] | None = typer.Option(
        None, "--city", help="City; repeatable. Use with --from/--to (omit if using --trip)"
    ),
    frm: str | None = typer.Option(None, "--from", help="YYYY-MM-DD (required with --city)"),
    to: str | None = typer.Option(None, "--to", help="YYYY-MM-DD (required with --city)"),
    category: str | None = typer.Option(
        None, "--category", help="Filter TM classification: Sports / Music / 'Arts & Theatre'"
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        help="Max perk-tier events shown per city (centerpiece always shown in full); "
        "ignored when --all is set",
    ),
    all_events: bool = typer.Option(
        False,
        "--all",
        "--verbose",
        "-v",
        help="Show ALL events (no perk-tier truncation) — reads the single API fetch, no extra calls",
    ),
    reference: bool = typer.Option(
        True,
        "--reference/--no-reference",
        help="Also surface static-almanac entries (known games/holidays) for the window — the "
        "proactive merge with the live scan",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Find live events in a date window — the dynamic-destination layer. Either scan a TRIP's
    candidate cities (--trip, corpus-driven) or arbitrary cities (--city + --from/--to).

    NBA/NFL surface as 'centerpiece' (trip-around-able); everything else as 'perk'. Live Ticketmaster
    results are merged with the static reference almanac (centerpiece games + holidays) unless
    --no-reference."""
    svc = TravelService()
    try:
        if trip:
            scans = svc.scan_trip_events(trip, classification=category)
            plan = svc.read_trip_plan(trip)
            window = plan.window
            ref_start, ref_end = plan.depart, plan.return_ or plan.depart
        else:
            if not (city and frm and to):
                _fail("provide --trip, OR --city with --from and --to")
                raise typer.Exit(code=1)
            ref_start, ref_end = date.fromisoformat(frm), date.fromisoformat(to)
            scans = svc.scan_events(city, ref_start, ref_end, classification=category)
            window = f"{frm} → {to}"
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    except (FileNotFoundError, KeyError, ValueError) as e:
        _fail(f"could not resolve trip {trip!r}: {e}")
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(
            json.dumps(
                [
                    {
                        "city": s.city,
                        "events": [{**e.model_dump(), "tier": svc.event_tier(e)} for e in s.events],
                    }
                    for s in scans
                ]
            )
        )
        return

    for s in scans:
        centerpiece = [e for e in s.events if svc.event_tier(e) == "centerpiece"]
        perks = [e for e in s.events if svc.event_tier(e) == "perk"]
        console.print(f"\n[bold]{s.city}[/bold] — {len(s.events)} events  ({window})")
        if centerpiece:
            console.print("[bold green]  Centerpiece — NBA/NFL (trip-around-able)[/bold green]")
            console.print(_event_table(centerpiece))
        shown, label = _perk_view(perks, limit, all_events)
        if shown:
            console.print(f"[bold]  Perks[/bold] [dim]({label})[/dim]")
            console.print(_event_table(shown))
        if not s.events:
            console.print("[dim]  (no ticketed events in window)[/dim]")

    if reference:
        ref = svc.scan_reference(ref_start, ref_end)
        if ref:
            console.print(
                "\n[bold]📌 Reference almanac[/bold] "
                "[dim](proactive — known games / holidays / mega-events in window)[/dim]"
            )
            console.print(_event_table(ref))


@app.command()
def reference(
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    city: str | None = typer.Option(
        None, "--city", help="Keep located entries matching this city + all window-wide ones"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Static-almanac entries (known centerpiece games + holidays + mega-events) in a window — the
    PROACTIVE layer read from corpus/travel/reference/*.md, the complement to the reactive live
    `events` scan. Confirm a specific game/price live before acting."""
    svc = TravelService()
    ref = svc.scan_reference(date.fromisoformat(frm), date.fromisoformat(to), city=city)
    if as_json:
        console.print_json(
            json.dumps([{**e.model_dump(), "tier": svc.event_tier(e)} for e in ref])
        )
        return
    scope = f" · {city}" if city else ""
    console.print(f"[bold]Reference almanac[/bold] — {len(ref)} entries  ({frm} → {to}{scope})")
    if ref:
        console.print(_event_table(ref))
    else:
        console.print("[dim](nothing in the almanac for this window)[/dim]")


@app.command()
def trips(
    as_json: bool = typer.Option(False, "--json"),
    all_trips: bool = typer.Option(
        False, "--all", help="Include completed + cancelled (the history)"
    ),
) -> None:
    """The travel horizon — every upcoming trip + visit from the corpus `trip:` frontmatter,
    countdown-ordered (the command-center spine read from corpus/travel/{trips,visits}/). `--all`
    appends completed/cancelled history (most-recent first)."""
    svc = TravelService()
    rows = svc.trip_pipeline(date.today(), include_past=all_trips)
    if as_json:
        console.print_json(
            json.dumps({"as_of": date.today().isoformat(), "count": len(rows), "trips": rows})
        )
        return
    if not rows:
        console.print("[dim](no trips in the pipeline — add a `trip:` frontmatter block)[/dim]")
        return
    table = Table(title=f"Travel horizon — {len(rows)} trip(s)")
    for col in ("When", "Date", "Status", "Kind", "Destination", "Anchor", "Who"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["when"], r["date"], r["status"], r["kind"], r["destination"], r["anchor"], r["who"]
        )
    console.print(table)


@app.command()
def hotels(
    location: str = typer.Option(..., "--location", "--where", help="Where, e.g. 'La Jolla'"),
    check_in: str = typer.Option(..., "--check-in", help="Check-in YYYY-MM-DD"),
    check_out: str = typer.Option(..., "--check-out", help="Check-out YYYY-MM-DD"),
    adults: int = typer.Option(2, "--adults"),
    min_class: int = typer.Option(4, "--min-class", help="Min hotel stars (4-5 = the luxe lens; 0 = any)"),
    max_price: int = typer.Option(0, "--max-price", help="Per-night USD ceiling (0 = none)"),
    limit: int = typer.Option(5, "--limit", help="How many properties to surface"),
    vacation_rentals: bool = typer.Option(False, "--vacation-rentals", help="Homes/cabins, not hotels"),
    report: str = typer.Option("", "--report", help="Write a {dest}/lodging/ report (slug or vault path)"),
    photos: int = typer.Option(5, "--photos", help="Photos per property in the report (API returns ~9+)"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing lodging report"),
    refresh: bool = typer.Option(False, "--refresh", help="Force a fresh search (skip the cache)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Tangible bookable properties for a destination+dates — the lodging-research layer (Google
    Hotels via SerpAPI). ONE search returns the whole ranked list (not one per hotel); a date-keyed
    cache makes re-views cost ZERO quota. **Opt-in / quota-spending** — confirm before running. The
    luxe-stay texture: named, priced, photographed, next-step-is-booking."""
    svc = TravelService()
    try:
        res = svc.search_hotels(
            location,
            date.fromisoformat(check_in),
            date.fromisoformat(check_out),
            adults=adults,
            min_hotel_class=min_class,
            max_price=max_price or None,
            limit=limit,
            vacation_rentals=vacation_rentals,
            refresh=refresh,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(res.model_dump(mode="json")))
        return

    src = "[dim](cached — 0 quota)[/dim]" if res.from_cache else "[dim](live search — 1 quota)[/dim]"
    console.print(
        f"\n[bold]{res.location}[/bold]  "
        f"[dim]{check_in} → {check_out}, {res.nights}n, {adults} adults[/dim]  {src}"
    )
    if not res.offers:
        console.print("[yellow]No properties returned (try --min-class 0 or widen dates).[/yellow]")
        return
    table = Table()
    for col in ("Property", "★", "Rating", "$/night", "Total", "Amenities"):
        table.add_column(col)
    for o in res.offers:
        table.add_row(
            o.name,
            "—" if o.hotel_class is None else str(o.hotel_class),
            "—" if o.overall_rating is None else f"{o.overall_rating}",
            "—" if o.price_per_night_usd is None else f"${o.price_per_night_usd:,.0f}",
            "—" if o.total_usd is None else f"${o.total_usd:,.0f}",
            ", ".join(o.amenities[:3]),
        )
    console.print(table)
    console.print("\n[dim]Details:[/dim]")
    for o in res.offers:
        deal = f"  [green]⭐ {o.deal}[/green]" if o.deal else ""
        console.print(f"\n[bold]{o.name}[/bold]{deal}")
        if o.description:
            console.print(f"  {o.description}")
        if o.nearby_places:
            near = " · ".join(
                f"{np.name} ({np.duration} {np.transport})".strip().replace("()", "")
                for np in o.nearby_places[:4]
            )
            console.print(f"  [dim]Nearby:[/dim] {near}")
        if o.excluded_amenities:
            console.print(f"  [yellow]Lacks:[/yellow] {', '.join(o.excluded_amenities)}")
        if o.booking_link:
            console.print(f"  [dim]Book:[/dim] {o.booking_link}")

    if report:
        try:
            path = svc.write_lodging_report(res, report, photos_per=photos, force=force)
            console.print(f"\n[bold green]Lodging report written:[/bold green] {path}")
        except FileExistsError as e:
            _fail(f"{e} (use --force to overwrite)")
            raise typer.Exit(code=1) from e


@app.command()
def weather(
    city: str | None = typer.Option(
        None, "--city",
        help="Place name (e.g. 'Denver'). Omit to use your home base — weights `conditions.home`, "
        "which is pack-aware, so a loaded persona's home is used.",
    ),
    frm: str = typer.Option(..., "--from", help="Window start YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="Window end YYYY-MM-DD"),
    celsius: bool = typer.Option(False, "--celsius", help="Metric units (default Fahrenheit/inch)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Live daily forecast for a place over a window — the dynamic weather *sense* (Open-Meteo,
    keyless). Real conditions on the trip dates, vs. the corpus's climate-averages. With no `--city`,
    forecasts the configured home base (`conditions.home`)."""
    svc = TravelService()
    where = city or svc.weights.conditions.home
    try:
        fc = svc.get_weather(
            where, date.fromisoformat(frm), date.fromisoformat(to), fahrenheit=not celsius
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(fc.model_dump()))
        return

    console.print(f"\n[bold]{fc.location}[/bold]  [dim]({frm} → {to}, {fc.timezone})[/dim]")
    if not fc.days:
        console.print("[yellow]No forecast days returned for that window.[/yellow]")
        return
    table = Table()
    unit = fc.temperature_unit
    punit = fc.precipitation_unit
    for col in ("Date", "Conditions", f"High ({unit})", f"Low ({unit})", "Precip %", "Rain (hrs)", "Snow"):
        table.add_column(col)
    for d in fc.days:
        rain = "—"
        if d.precip_hours:  # the wet-day DURATION signal — hours of precip + the accumulation
            amt = f" · {d.precip_sum:.2f}{punit[0]}" if d.precip_sum else ""
            rain = f"{d.precip_hours:.0f}h{amt}"
        snow = f"{d.snowfall_sum:.1f}{punit[0]}" if d.snowfall_sum else "—"
        table.add_row(
            d.date, d.condition,
            "—" if d.temp_max is None else f"{d.temp_max:.0f}",
            "—" if d.temp_min is None else f"{d.temp_min:.0f}",
            "—" if d.precip_prob is None else f"{d.precip_prob}%",
            rain, snow,
        )
    console.print(table)


@app.command()
def air(
    city: str = typer.Option(..., "--city", help="Place name, e.g. 'Denver'"),
    frm: str = typer.Option(..., "--from", help="Window start YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="Window end YYYY-MM-DD"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Daily air-quality (US AQI / PM2.5 max) over a window — the wildfire-smoke sense (Open-Meteo,
    keyless). AQI horizon is shorter than weather (~5 days)."""
    try:
        rep = TravelService().get_air_quality(city, date.fromisoformat(frm), date.fromisoformat(to))
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    cur = f"now {rep.current_us_aqi} ({rep.current_category})" if rep.current_us_aqi is not None else ""
    console.print(f"\n[bold]{rep.location}[/bold]  [dim]({frm} → {to}, {rep.timezone}) {cur}[/dim]")
    if not rep.days:
        console.print("[yellow]No air-quality data for that window (beyond ~5-day horizon?).[/yellow]")
        return
    table = Table()
    for col in ("Date", "US AQI (max)", "Category", "PM2.5 (max)"):
        table.add_column(col)
    for d in rep.days:
        table.add_row(
            d.date,
            "—" if d.us_aqi_max is None else str(d.us_aqi_max),
            d.category,
            "—" if d.pm2_5_max is None else f"{d.pm2_5_max:.0f}",
        )
    console.print(table)


def _publish_conditions_to_bus(rep: ConditionsReport) -> str:
    """Publish conditions flags to the harness bus (the durable human-event layer the tray app
    delivers from). Returns an 'N published, M dup' note for the run-log. Never kills the standing
    run — a bus failure degrades to an ERROR note (graceful-degradation rule)."""
    if rep.quiet:
        return ""
    try:
        from harness.bus.service import BusService
        from harness.travel.conditions import events_from_conditions

        results = BusService().publish_many(events_from_conditions(rep))
        published = sum(1 for r in results if r.status == "published")
        return f"bus: {published} published, {len(results) - published} dup"
    except Exception as e:  # noqa: BLE001 — standing loop must survive any bus failure
        return f"bus ERROR: {e}"


def _publish_morning_to_bus(rep: ConditionsReport, report_rel: str) -> str:
    """Publish the ONE escalating 6am morning-report event (the daily ritual, louder on a flag —
    not separate pings). `report_rel` deep-links the bus Inbox to today's report doc. Graceful."""
    try:
        from harness.bus.service import BusService
        from harness.travel.conditions import morning_event

        results = BusService().publish_many([morning_event(rep, report_rel)])
        published = sum(1 for r in results if r.status == "published")
        return f"bus: {published} published, {len(results) - published} dup"
    except Exception as e:  # noqa: BLE001 — standing loop must survive any bus failure
        return f"bus ERROR: {e}"


def _conditions_log(rep: ConditionsReport, bus_note: str = "") -> None:
    """Append one line to ~/.local/state/harness/travel-pulse.log (the did-it-run audit) — always,
    quiet or not. Mirrors the finance pulse-log; the bus is the human transport (no osascript)."""
    state_dir = Path.home() / ".local" / "state" / "harness"
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if rep.quiet:
        line = f"{stamp} quiet (0 flags) [{rep.home}]"
    else:
        kinds = ", ".join(f"{f.kind}:{f.date}" for f in rep.flags)
        suffix = f" [{bus_note}]" if bus_note else ""
        line = f"{stamp} {len(rep.flags)} flag(s): {kinds}{suffix}"
    with (state_dir / "travel-pulse.log").open("a") as fh:
        fh.write(line + "\n")


@app.command()
def pulse(
    as_json: bool = typer.Option(False, "--json", help="Machine output for the scheduled agent"),
    report: bool = typer.Option(
        False, "--report",
        help="The 6am morning mode (launchd): write the digest to travel/conditions/reports/{date}.md "
        "+ publish ONE escalating morning event to the bus. The always-fires daily ritual.",
    ),
    notify: bool = typer.Option(
        False, "--notify",
        help="Per-flag alert mode: publish each crossing as its own deep-linked bus event (for a "
        "future intraday/fast-mover re-check). Distinct from --report's single daily push.",
    ),
) -> None:
    """Travel Watchman conditions watch: the configured home locale's weather + air vs the
    thresholds in weights.yaml `conditions:` — heat / smoke / wet_day / snow. Quiet days say quiet.
    `--report` is the 6am path (digest doc + one escalating push); `--notify` is per-flag alerts.
    Deterministic; the agent narrates on demand."""
    try:
        rep = TravelService().conditions_pulse()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    notes: list[str] = []
    if report:
        from harness.travel.conditions import write_morning_report

        root = get_settings().tracker_path
        path = write_morning_report(rep, root)
        notes.append(_publish_morning_to_bus(rep, str(path.relative_to(root))))
        notes.append(f"report:{path.name}")
    if notify:
        notes.append(_publish_conditions_to_bus(rep))
    if report or notify:
        _conditions_log(rep, bus_note=" ".join(n for n in notes if n))

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    console.print(f"\nconditions · {rep.as_of} · [bold]{rep.home}[/bold]")
    if rep.quiet:
        console.print(
            "[green]QUIET — nothing crossed. No heat / smoke / wet day / snow in the window.[/green]"
        )
        return
    table = Table()
    for col in ("Flag", "Date", "Detail"):
        table.add_column(col)
    for f in rep.flags:
        table.add_row(f.kind, f.date, f.message)
    console.print(table)


@app.command()
def food(
    near: str = typer.Option(..., "--near", help="Place name, e.g. 'Anytown, Colorado'"),
    radius: int = typer.Option(1500, "--radius", help="Search radius meters"),
    live_ratings: bool = typer.Option(
        False, "--live-ratings", help="QUOTA: merge Google ratings (1 SerpAPI search, cached; confirm first)"
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the ratings cache (re-spends quota)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Eateries near a place — two-tier food discovery. Default = OSM Overpass
    enumeration (KEYLESS, free): what exists, from data not memory. --live-ratings layers Google
    ratings/price on top (1 quota search, day-cached)."""
    try:
        rep = TravelService().find_food(
            near, radius_m=radius, live_ratings=live_ratings, refresh=refresh
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    console.print(
        f"\n[bold]{rep.location}[/bold]  [dim]{rep.count} eateries within {rep.radius_m}m"
        f"{' · ratings merged' if rep.live_ratings else ''}[/dim]"
    )
    if not rep.eateries:
        console.print("[yellow]No mapped eateries — try a bigger --radius (small towns map sparse).[/yellow]")
        return
    table = Table()
    for col in ("Name", "Kind", "Cuisine", "Rating", "Price", "Hours / address", "Src"):
        table.add_column(col)
    for ea in rep.eateries:
        rating = "—" if ea.rating is None else f"{ea.rating:.1f} ({ea.reviews or 0})"
        table.add_row(
            ea.name,
            ea.category.replace("_", " "),
            ea.cuisine or "—",
            rating,
            ea.price or "—",
            ea.opening_hours or ea.address or "—",
            "+".join(ea.sources),
        )
    console.print(table)
    for n in rep.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def quakes(
    city: str = typer.Option(..., "--city", help="Place name, e.g. 'Denver' or 'Reykjavik'"),
    radius: int = typer.Option(300, "--radius", help="Search radius km"),
    days: int = typer.Option(90, "--days", help="Lookback window (days)"),
    min_mag: float = typer.Option(2.5, "--min-mag", help="Minimum magnitude"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Recent earthquakes near a place — the geological-screen sense (USGS, keyless). Real data to
    calibrate the screen, not categorical priors."""
    try:
        rep = TravelService().get_earthquakes(
            city, radius_km=radius, days_back=days, min_magnitude=min_mag
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    console.print(
        f"\n[bold]{rep.location}[/bold]  [dim]{rep.count} quakes ≥M{rep.min_magnitude} "
        f"within {rep.radius_km}km since {rep.since}[/dim]"
    )
    if not rep.quakes:
        console.print("[green]No recent earthquakes in range — calm by this measure.[/green]")
        return
    table = Table()
    for col in ("Date", "Mag", "Place", "Depth (km)"):
        table.add_column(col)
    for q in rep.quakes:
        table.add_row(
            q.date,
            "—" if q.magnitude is None else f"{q.magnitude:.1f}",
            q.place,
            "—" if q.depth_km is None else f"{q.depth_km:.0f}",
        )
    console.print(table)


@app.command()
def fx(
    symbols: list[str] = typer.Argument(None, help="Target currencies, e.g. EUR JPY MXN (default: all)"),
    base: str = typer.Option("USD", "--base", help="Base currency"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Exchange rates from a base currency (keyless, open.er-api.com). rate = units per 1 base."""
    try:
        fxr = TravelService().fx_rates(symbols or None, base=base)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(fxr.model_dump()))
        return
    console.print(f"\n[bold]1 {fxr.base}[/bold] = …  [dim]{fxr.date}[/dim]")
    table = Table()
    for col in (f"per 1 {fxr.base}", "Rate", f"1 X = {fxr.base}"):
        table.add_column(col)
    for code, rate in sorted(fxr.rates.items()):
        inv = f"{1 / rate:,.4f}" if rate else "—"
        table.add_row(code, f"{rate:,.4f}", inv)
    console.print(table)


@app.command()
def holidays(
    country: str = typer.Argument(..., help="ISO-3166 alpha-2, e.g. US, JP, MX"),
    year: int = typer.Option(..., "--year", help="Year, e.g. 2026"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Public holidays for a country-year (keyless, date.nager.at) — crowds/closures timing."""
    try:
        hol = TravelService().public_holidays(country, year)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(hol.model_dump()))
        return
    console.print(f"\n[bold]{hol.country} {hol.year}[/bold]  [dim]{len(hol.holidays)} public holidays[/dim]")
    table = Table()
    for col in ("Date", "Holiday", "Local name", "Nationwide"):
        table.add_column(col)
    for h in hol.holidays:
        table.add_row(h.date, h.name, h.local_name, "yes" if h.nationwide else "regional")
    console.print(table)


@app.command()
def sun(
    place: str = typer.Argument(..., help="Place name (geocoded), e.g. 'San Diego'"),
    on: str = typer.Option(..., "--date", help="Date YYYY-MM-DD"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Sunrise/sunset/twilight + golden-hour approximations for a place-day (keyless,
    sunrise-sunset.org). Times are ISO-8601 UTC. Golden-hour fields are approximate."""
    try:
        st = TravelService().sun_times(place, date_str=on)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(st.model_dump()))
        return
    dl = st.day_length_seconds
    suffix = f"  [dim](day length {dl // 3600}h{dl % 3600 // 60:02d}m)[/dim]" if dl else ""
    console.print(f"\n[bold]{place}[/bold] {st.date}{suffix}")
    table = Table()
    table.add_column("Event")
    table.add_column("UTC time")
    for label, val in (
        ("civil dawn", st.civil_twilight_begin),
        ("sunrise", st.sunrise),
        ("golden hr ends ~", st.golden_hour_morning_end),
        ("solar noon", st.solar_noon),
        ("golden hr begins ~", st.golden_hour_evening_begin),
        ("sunset", st.sunset),
        ("civil dusk", st.civil_twilight_end),
    ):
        table.add_row(label, val or "—")
    console.print(table)
    console.print("[dim]  • UTC times; golden-hour fields ~approximate (sunrise+1h / sunset-1h).[/dim]")


@app.command()
def country(
    name: str = typer.Argument(..., help="Country name, e.g. Japan, Mexico"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Trip-prep country facts (keyless, restcountries.com): currency, language, region, driving side."""
    try:
        cf = TravelService().country_facts(name)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(cf.model_dump()))
        return
    console.print(f"\n[bold]{cf.name}[/bold]  [dim]{cf.official_name}[/dim]")
    cur = ", ".join(f"{code}: {desc}" for code, desc in cf.currencies.items()) or "—"
    console.print(f"  Region:     {cf.region}{' / ' + cf.subregion if cf.subregion else ''}")
    console.print(f"  Capital:    {', '.join(cf.capital) or '—'}")
    console.print(f"  Currency:   {cur}")
    console.print(f"  Languages:  {', '.join(cf.languages) or '—'}")
    console.print(f"  Drives on:  {cf.driving_side or '—'}")
    console.print(f"  Timezones:  {', '.join(cf.timezones) or '—'}")


@app.command()
def traffic(
    near: str | None = typer.Option(
        None, "--near", help="Substring over route/alert text, e.g. a town or city name"
    ),
    road: str | None = typer.Option(
        None, "--road", help="Highway, e.g. I-5 / 405 / US-2 (normalized to the WSDOT code)"
    ),
    category: str | None = typer.Option(
        None, "--category", help="Alert category, e.g. Construction / Incident / 'Lane Closure'"
    ),
    congested: bool = typer.Option(
        False, "--congested", help="Travel times: only routes delayed past the threshold"
    ),
    threshold: int = typer.Option(
        5, "--threshold", help="Minutes over average to flag a route congested"
    ),
    times_only: bool = typer.Option(False, "--times-only", help="Only travel times (skip alerts)"),
    alerts_only: bool = typer.Option(False, "--alerts-only", help="Only alerts (skip travel times)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Live WA traffic (keyed-free WSDOT): travel-time congestion deltas + construction/closure/incident
    alerts on your corridors. Filter with --near (a town name) / --road (I-5) / --category (Construction)."""
    try:
        report = TravelService().get_traffic(
            near=near,
            road=road,
            category=category,
            congested_only=congested,
            include_times=not alerts_only,
            include_alerts=not times_only,
            congestion_threshold=threshold,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(report.model_dump()))
        return

    if not alerts_only:
        tt = report.travel_times
        console.print(f"\n[bold]Travel times[/bold] [dim]({len(tt)} routes)[/dim]")
        if tt:
            table = Table()
            for col in ("Route", "Now", "Avg", "Δ", "Dist"):
                table.add_column(col)
            for t in tt:
                table.add_row(
                    t.name, _mins(t.current_minutes), _mins(t.average_minutes),
                    _delay(t), f"{t.distance_miles:.0f}mi" if t.distance_miles else "—",
                )
            console.print(table)
        else:
            console.print("[dim]  (no routes match)[/dim]")

    if not times_only:
        al = report.alerts
        console.print(f"\n[bold]Highway alerts[/bold] [dim]({len(al)})[/dim]")
        if al:
            table = Table()
            for col in ("Rd", "Category", "Pri", "Headline"):
                table.add_column(col)
            for a in al:
                rd = a.start_location.road_name.lstrip("0") if a.start_location else ""
                head = (a.headline[:97] + "…") if len(a.headline) > 98 else a.headline
                table.add_row(rd or "—", a.category, a.priority, head)
            console.print(table)
        else:
            console.print("[dim]  (no alerts match)[/dim]")


@app.command()
def ferry(
    route: str | None = typer.Option(
        None, "--route", help="'Departing-Arriving', e.g. 'Seattle-Bainbridge Island' → today's sailings"
    ),
    space: bool = typer.Option(
        False, "--space", help="Live drive-up space per upcoming departure (a route's terminal, or all)"
    ),
    vessels: bool = typer.Option(
        False, "--vessels", help="Live vessel positions + ETA + in-service/cancellation"
    ),
    all_today: bool = typer.Option(
        False, "--all", help="Schedule: include past sailings too (default: only remaining today)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Washington State Ferries (keyed-free WSDOT): with --route, today's sailing times for the route
    (+ --space for live drive-up space, + --vessels for boat positions); without --route, --space /
    --vessels show the whole-system live boards. Routes are 'Departing-Arriving' terminal pairs."""
    if not (route or space or vessels):
        _fail("pass --route (schedule) and/or --space / --vessels")
        raise typer.Exit(code=1)
    try:
        report = TravelService().get_ferry(
            route=route, space=space, vessels=vessels, only_remaining=not all_today
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps(report.model_dump()))
        return

    if route:
        console.print(f"\n[bold]⛴  {route}[/bold] [dim](today's sailings)[/dim]")
        if report.sailings:
            table = Table()
            for col in ("Depart", "Arrive", "Vessel"):
                table.add_column(col)
            for s in report.sailings:
                table.add_row(_ptime(s.departing_time), _ptime(s.arriving_time), s.vessel_name)
            console.print(table)
        else:
            console.print("[dim]  (no remaining sailings today — try --all)[/dim]")

    for ts in report.space:
        console.print(f"\n[bold]Drive-up space — {ts.terminal_name}[/bold] [dim]({ts.terminal_abbrev})[/dim]")
        if ts.departures:
            table = Table()
            for col in ("Depart", "Vessel", "→ Arrive", "Drive-up", ""):
                table.add_column(col)
            for d in ts.departures:
                du = "—" if d.drive_up_available is None else str(d.drive_up_available)
                if d.is_cancelled:
                    flag = "[red]CANCELLED[/red]"
                elif d.drive_up_available == 0:
                    flag = "[red]FULL[/red]"
                else:
                    flag = ""
                table.add_row(_ptime(d.departure), d.vessel_name, d.arrival_terminal, du, flag)
            console.print(table)
        else:
            console.print("[dim]  (no upcoming departures)[/dim]")

    if report.vessels:
        console.print(f"\n[bold]Vessels[/bold] [dim]({len(report.vessels)})[/dim]")
        table = Table()
        for col in ("Vessel", "Route", "ETA", "Status"):
            table.add_column(col)
        for v in report.vessels:
            leg = f"{v.departing_terminal} → {v.arriving_terminal}".strip(" →")
            if not v.in_service:
                status = "[red]out of service[/red]"
            else:
                status = "at dock" if v.at_dock else "under way"
            table.add_row(v.name, leg or "—", _ptime(v.eta), status)
        console.print(table)


@app.command()
def viz(
    diagram: str = typer.Argument(..., help="Diagram type (timeline)"),
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Vault path under travel/ — SVG written to {dest}/visuals/ "
        "(e.g. 'trips/2026-09-birthday-window' or 'viz-demo')",
    ),
    data_file: str | None = typer.Option(
        None, "--data", help="Render pre-built JSON directly (skips the events builder)"
    ),
    city: str | None = typer.Option(None, "--city", help="Events-timeline city (with --from/--to)"),
    trip: str | None = typer.Option(None, "--trip", help="Trip slug (for `map`: geocode its candidates)"),
    frm: str | None = typer.Option(None, "--from", help="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", help="YYYY-MM-DD"),
    category: str | None = typer.Option(
        None, "--category", help="Filter the events timeline: Sports / Music / 'Arts & Theatre'"
    ),
    name: str = typer.Option("timeline", "--name", help="Output file stem"),
    title: str | None = typer.Option(None, "--title"),
    subtitle: str | None = typer.Option(None, "--subtitle"),
    grid: bool = typer.Option(
        False, "--grid", help="map-annotate: overlay a 0.1 coordinate grid (read off pin fractions)"
    ),
    theme: str = typer.Option(
        "light", "--theme", help="Render theme: light (default) | instrument (the bus-app console palette)"
    ),
) -> None:
    """Render a D3 diagram into the corpus ({dest}/visuals/{name}.svg) + print the Obsidian embed.

    Data source: --data <json> (arbitrary, e.g. a built schedule); OR --city + --from/--to (a live
    events timeline, or weather-strip when diagram=weather-strip); OR --trip (for diagram=map:
    geocode the trip's candidates + arc from home).

    map-annotate: pass --data <spec.json> with an `image` (path under the tracker corpus) + fractional
    `pins`/`route`/`notes`. Render once with --grid to read off coordinates, then drop it for the final."""
    if data_file:
        data = json.loads(Path(data_file).read_text())
    elif trip and diagram == "map":
        try:
            data = _svc("serpapi").map_data_for_trip(trip)
        except (FileNotFoundError, KeyError, ValueError) as e:
            _fail(f"could not build map for trip {trip!r}: {e}")
            raise typer.Exit(code=1) from e
    elif diagram == "calendar" and frm and to:
        s, e_ = date.fromisoformat(frm), date.fromisoformat(to)
        ref = _svc("serpapi").scan_reference(s, e_)
        data = reference_to_calendar(
            ref, start=s, end=e_,
            title=title or f"Travel calendar ({frm} → {to})",
            subtitle=subtitle or "long-weekends · centerpiece games · key dates — from the reference almanac",
        )
    elif city and frm and to and diagram == "weather-strip":
        try:
            fc = _svc("serpapi").get_weather(city, date.fromisoformat(frm), date.fromisoformat(to))
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
        data = weather_to_strip(
            fc,
            title=title or f"{city} — weather ({frm} → {to})",
            subtitle=subtitle or "live forecast · °F hi/lo + precip chance",
        )
    elif city and frm and to:
        try:
            scans = _svc("serpapi").scan_events(
                [city], date.fromisoformat(frm), date.fromisoformat(to), classification=category
            )
        except ProviderError as e:
            _fail(str(e))
            raise typer.Exit(code=1) from e
        ce = scans[0] if scans else CityEvents(city=city)
        data = events_to_timeline(
            ce,
            start=frm,
            end=to,
            title=title or f"{city} — what's on ({frm} → {to})",
            subtitle=subtitle,
        )
    else:
        _fail("provide --data <json>, OR --city with --from/--to, OR --trip (for diagram=map)")
        raise typer.Exit(code=1)

    out = get_settings().travel_corpus_path / dest / "visuals" / f"{name}.svg"
    try:
        if diagram == "map-annotate":
            # image paths in the spec resolve against the tracker corpus root (e.g. screenshots/…)
            written = render_map_annotate(
                data, out, image_base=get_settings().tracker_path, grid=grid
            )
        else:
            written = render_diagram(diagram, data, out, theme=theme)
    except VizError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    console.print(f"[green]✓[/green] wrote {written}")
    console.print("\n[bold]Obsidian embed[/bold] (paste into the doc):")
    console.print(embed_markdown(written), markup=False)  # markup=False: keep the [[...]] literal


@app.command()
def mcp() -> None:
    """Launch the MCP server (stdio)."""
    from harness.travel.mcp_server import main as mcp_main

    mcp_main()


def _render_shortlist(s: Shortlist) -> None:
    console.print(f"[bold]Shortlist — {s.window}[/bold]  (ranked; not a single pick)")
    table = Table()
    for col in ("#", "Candidate", "Origin", "Carrier", "Stops", "Duration", "Price", "Score"):
        table.add_column(col)
    for i, sc in enumerate(s.candidates, 1):
        f = sc.best_flight
        table.add_row(
            str(i), sc.candidate.display_name,
            f.origin_iata if f else "—",
            f.carrier if f else "—",
            str(f.stops) if f else "—",
            f"{f.duration_hours:.1f}h" if f else "—",
            f"${f.price_usd:.0f}" if f else "—",
            f"{sc.score.total:.0f}",
        )
    console.print(table)
    for sc in s.candidates:
        console.print(f"\n[bold]{sc.candidate.display_name}[/bold] — {sc.score.total:.0f}")
        for r in sc.score.rationale:
            console.print(f"   • {r}")
    if s.notes:
        console.print("\n[bold]Notes[/bold]")
        for n in s.notes:
            console.print(f"   • {n}")


def _perk_view(
    perks: list[EventResult], limit: int, show_all: bool
) -> tuple[list[EventResult], str]:
    """Perk-tier display selection + label. ``show_all`` (or a list already within ``limit``)
    shows everything; otherwise truncate to ``limit`` and label the remainder. Operates on the
    events already fetched in the single provider call — no extra API calls."""
    if show_all or len(perks) <= limit:
        return perks, f"all {len(perks)}"
    shown = perks[:limit]
    return shown, f"top {len(shown)} (+{len(perks) - len(shown)} more)"


def _event_table(events_list: list[EventResult]) -> Table:
    table = Table()
    for col in ("Date", "Time", "Event", "Type", "Venue"):
        table.add_column(col)
    for e in events_list:
        kind = " / ".join(x for x in (e.segment, e.genre, e.subgenre) if x)
        table.add_row(e.local_date, e.local_time or "—", e.name, kind, e.venue)
    return table


def _fail(msg: str) -> None:
    console.print(f"[red]error:[/red] {msg}")


def _mins(v: int | None) -> str:
    return f"{v}m" if v and v > 0 else "—"


def _delay(t: TravelTime) -> str:
    """Render a travel-time delay: red +N when congested, plain ±N otherwise, — when no reading."""
    d = t.delay_minutes
    if d is None:
        return "—"
    s = f"+{d}" if d > 0 else str(d)
    return f"[red]{s}[/red]" if t.congested else s


_PACIFIC = ZoneInfo("America/Los_Angeles")


def _ptime(iso: str | None) -> str:
    """An ISO-8601 UTC timestamp → local Pacific 'h:MM AM/PM' (ferry times read in local time)."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(_PACIFIC).strftime("%-I:%M %p")
    except ValueError:
        return "—"


if __name__ == "__main__":
    app()
