"""Typer CLI adapter (Bash-callable / cron-able). Thin wrapper over TravelService."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from harness._cli import fail as _fail
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
    trip: str = typer.Option(..., "--trip", help="Trip slug, e.g. 2027-01-sample-trip"),
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
        "under travel/ (e.g. visits/2026-05-example-visit → travel/{dest}/assets), like `viz --dest`",
    ),
    subjects: str = typer.Option(
        ..., "--subjects", help="Comma-separated subjects (hotels / attractions / views)"
    ),
    provider: str = typer.Option("wikimedia", "--provider", help="wikimedia (keyless / free)"),
    width: int = typer.Option(480, "--width", help="Embed width for the suggested markdown"),
    keep_original: bool = typer.Option(
        False, "--keep-original",
        help="Store the bytes as served (no downscale/re-encode) — the rare print-quality case; "
        "default applies the vault cap (≤1600px long edge, JPEG q85, ~400KB)",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch + locally store images for a destination report; print embed-ready markdown."""
    subj_list = [s.strip() for s in subjects.split(",") if s.strip()]
    try:
        results = TravelService().fetch_destination_images(
            dest, subj_list, provider_name=provider, keep_original=keep_original
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
def decision(as_json: bool = typer.Option(False, "--json")) -> None:
    """The active trip decision's compare-radar data — resolves the soonest live trip/visit whose
    folder carries visuals/compare-data.json (the Planning radar's dynamic source).
    Corpus-driven: follows trips as they open/close; no frozen slug in any config."""
    svc = TravelService()
    data = svc.active_decision()
    if as_json:
        console.print_json(json.dumps(data))
        return
    if not data.get("candidates"):
        console.print("[dim]No live trip carries a compare radar yet.[/dim]")
        return
    head = f"{data.get('trip_name') or data.get('trip')} — {data.get('window') or ''}".rstrip(" —")
    console.print(f"[bold]{head}[/bold]  [dim]{data.get('title', '')}[/dim]")
    for i, r in enumerate(data.get("ranked", []), start=1):
        console.print(
            f"  {i}. {r['label']}  [bold]{r['total']}[/bold]  "
            f"[dim]strongest {r['strongest']} · weakest {r['weakest']}[/dim]"
        )


@app.command()
def bank(
    visit: str = typer.Option(
        None, "--visit", "--trip", help="Visit/trip slug or 'auto' (default: the soonest live one)"
    ),
    scope: str = typer.Option("visit", "--scope", help="Default resolution among: visit | trip"),
    kind: str = typer.Option("all", "--kind", help="outings | restaurants | all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """A visit's idea bank — outings (schedule.json) + restaurants (restaurants.json) merged into
    one tile contract: label, group, area, drive time, indoor, rating, image, links. Resolves the
    soonest live visit whose folder carries a bank, like `decision`. The Visits tab's tile source."""
    svc = TravelService()
    try:
        data = svc.visit_bank(visit, kind, scope=scope)
    except ValueError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(data.model_dump_json())
        return
    if not data.items:
        console.print(f"[dim]{data.title}[/dim]")
        return
    console.print(f"[bold]{data.title}[/bold]  [dim]({data.visit} · {len(data.items)} items)[/dim]")
    for g in data.groups:
        console.print(f"\n[bold]{g.label}[/bold] [dim]({g.kind}, {g.count})[/dim]")
        for it in (i for i in data.items if i.kind == g.kind and i.group == g.key):
            bits = [x for x in (
                it.area,
                f"{it.drive_min}m" if it.drive_min is not None else None,
                "indoor" if it.indoor else ("outdoor" if it.indoor is False else None),
                f"{it.rating}★" if it.rating is not None else None,
                it.price,
            ) if x]
            console.print(f"  • {it.label}  [dim]{' · '.join(bits)}[/dim]")


@app.command()
def banks(
    scope: str = typer.Option("visit", "--scope", help="visit | trip"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Every visit (or trip) as a picker entry — live soonest-first, then past — with its cover,
    dates, countdown and what it carries (outings, restaurants, photos, scheduled items). The
    Visits/Trips tab picker's source; `default` names the one the other widgets show first."""
    data = TravelService().trip_banks(scope)
    if as_json:
        console.print_json(data.model_dump_json())
        return
    for e in data.entries:
        mark = "→" if e.slug == data.default else " "
        bits = [e.when, f"{e.outings} ideas", f"{e.restaurants} restaurants", f"{e.photos} photos",
                f"{e.scheduled} scheduled"]
        console.print(f"{mark} [bold]{e.slug}[/bold]  [dim]{' · '.join(bits)}[/dim]")


@app.command()
def schedule(
    visit: str = typer.Option(
        None, "--visit", "--trip", help="Visit/trip slug or 'auto' (default: the soonest live one)"
    ),
    scope: str = typer.Option("visit", "--scope", help="Default resolution among: visit | trip"),
    no_weather: bool = typer.Option(False, "--no-weather", help="Skip the forecast (offline)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """A visit's schedule day by day — planned items, pinned markers, who's around, the home
    forecast (wet days flagged on the conditions thresholds) and the visit's bank. Resolves the
    soonest live visit whose folder carries a schedule.json. The Visits tab's timeline source."""
    svc = TravelService()
    try:
        data = svc.visit_schedule(visit, weather=not no_weather, scope=scope)
    except ValueError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(data.model_dump_json())
        return
    if not data.days:
        console.print(f"[dim]{data.title}[/dim]")
        return
    console.print(f"[bold]{data.title}[/bold]  [dim]({data.visit})[/dim]")
    if data.weather_note:
        console.print(f"[dim]{data.weather_note}[/dim]")
    for d in data.days:
        wx = d.weather
        temps = (
            f"{wx.condition} {wx.high:.0f}°/{wx.low:.0f}°"
            if wx and wx.high is not None and wx.low is not None else ""
        )
        bits = [temps, "WET" if wx and wx.wet else "", "+".join(d.availability)]
        console.print(f"\n[bold]{d.weekday} {d.date}[/bold]  [dim]{' · '.join(b for b in bits if b)}[/dim]")
        for m in d.markers:
            console.print(f"  ◆ {m.time + ' ' if m.time else ''}{m.label}")
        for it in d.items:
            span = f"{it.start}–{it.end} " if it.start else ""
            link = f"  [dim]→ {it.bank_id}[/dim]" if it.bank_id else ""
            console.print(f"  • {span}{it.label}{link}")


@app.command(name="bank-enrich")
def bank_enrich(
    visit: str = typer.Option(
        None, "--visit", "--trip", help="Visit/trip slug or 'auto' (default: the soonest live one)"
    ),
    scope: str = typer.Option("visit", "--scope", help="Default resolution among: visit | trip"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch every place (spends quota)"),
    cached_only: bool = typer.Option(
        False, "--cached-only", help="Free only: enrich what the cache already holds, skip the rest"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Write (and spend for uncached places); without it a dry run"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """QUOTA only for uncached places (1 search each): hours, a reservation / order link, phone,
    coordinates and place id for every place in a bank, written to the folder's places.json and
    merged into the tiles at read time. Places already resolved (bank-photos, a sweep) come from
    the cache for free. Flags any place >100 mi from the bank's median point."""
    svc = TravelService()
    try:
        run = svc.visit_bank_enrich(
            visit, scope=scope, dry_run=not yes, refresh=refresh, cached_only=cached_only
        )
    except (ValueError, ProviderError) as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(run))
        return
    spend = run["searches_spent"] if yes else run["searches_planned"]
    mode = "spent" if yes else "dry run — would spend"
    console.print(f"[bold]{run['visit']}[/bold]  [dim]{mode} {spend} SerpAPI searches[/dim]")
    for it in run["items"]:
        what = ", ".join(it["fields"]) or it["note"]
        console.print(f"  • {it['label']}  [dim]{it['source']} · {what}[/dim]")
    if run["outliers"]:
        console.print(f"[yellow]⚠ more than 100 mi from the rest: {', '.join(run['outliers'])}[/yellow]")


@app.command(name="bank-photos")
def bank_photos(
    visit: str = typer.Option(
        None, "--visit", help="Visit slug (default: the soonest live visit with a bank)"
    ),
    kind: str = typer.Option("all", "--kind", help="outings | restaurants | all"),
    ids: str = typer.Option("", "--ids", help="Comma-separated item ids (default: every place)"),
    per_item: int = typer.Option(8, "--per-item", help="Max photos kept per item"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch items that already have a gallery"),
    yes: bool = typer.Option(False, "--yes", help="Spend the quota (without it: a dry run that prices it)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """QUOTA (≤2 searches per item): photo galleries for a visit's bank tiles — resolves each place
    on Google Maps, keeps up to --per-item photos under assets/gallery/<item-id>/ (videos,
    thumbnails, panoramas and near-duplicates dropped), and records the place in places.json.
    Items marked "gallery": false are skipped. Without --yes it only prices the run."""
    id_set = {i.strip() for i in ids.split(",") if i.strip()} or None
    svc = TravelService()
    try:
        run = svc.visit_bank_photos(
            visit, kind, ids=id_set, per_item=per_item, refresh=refresh, dry_run=not yes
        )
    except (ValueError, ProviderError) as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(run))
        return
    mode = "spent" if yes else "dry run — would spend up to"
    console.print(
        f"[bold]{run['visit']}[/bold]  [dim]{mode} "
        f"{run['searches_spent'] if yes else run['searches_planned']}"
        " SerpAPI searches[/dim]"
    )
    for it in run["items"]:
        drops = ", ".join(f"{k} {v}" for k, v in it["dropped"].items())
        place = f" → {it['place']}" if it["place"] and it["place"] != it["label"] else ""
        bits = [x for x in (it["source"], f"{it['kept']} kept" if it["kept"] else "", drops, it["note"]) if x]
        console.print(f"  • {it['label']}{place}  [dim]{' · '.join(bits)}[/dim]")


@app.command()
def nudge(
    weeks: int = typer.Option(
        26, "--weeks", help="Horizon in weeks to enumerate weekends over (~6mo default — cross-season reach)"
    ),
    top: int = typer.Option(5, "--top", help="Max nudges surfaced (one per destination AND per window)"),
    live: bool = typer.Option(
        True,
        "--live/--no-live",
        help="Enrich the static top pairs with live events (Ticketmaster, free-tier) + the real "
        "forecast for in-horizon windows (Open-Meteo, keyless). --no-live = corpus + almanac only.",
    ),
    include_taken: bool = typer.Option(
        False, "--include-taken", help="Also nudge windows a live trip already occupies"
    ),
    lane: list[str] = typer.Option(
        None, "--lane", "-l",
        help="Lanes to surface (weekend / games / long-lead / horizon / family; repeatable); default all",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Proactive trip nudges across the horizon axis — WEEKENDS (windows × destinations, 0-26 wk),
    GAME WEEKENDS (the followed NBA/NFL markets' games as trip windows, game-first; needs --live),
    LONG-LEAD (week blocks 3-12 months out, where the far destinations win), HORIZON (season-shaped
    worth-a-look cards: never-tripped + screen-calibrated) and FAMILY (holidays the corpus names
    as family time — never escape-nudged; one practical card). Every card carries its lane + the
    scored components its reason was assembled from. Reads the corpus nudge registry
    (weights.yaml `destination_nudge:` + `nudge:`); spends NO paid quota."""
    valid = {"weekend", "games", "long-lead", "horizon", "family"}
    bad = [x for x in (lane or []) if x not in valid]
    if bad:
        console.print(f"[red]unknown lane(s): {', '.join(bad)} (valid: {', '.join(sorted(valid))})[/red]")
        raise typer.Exit(code=1)
    svc = TravelService()
    if not svc.weights.destination_nudge:
        # stderr, never stdout: `--json` consumers (the Planning tab's nudges widget) parse stdout
        # verbatim — an advisory there breaks every corpus without a registry, the exact case the
        # bare-list fallback exists to serve.
        Console(stderr=True).print(
            "[yellow]nudge registry empty — falling back to bare destination list; author "
            "`destination_nudge:` entries in the travel weights.yaml for real scoring[/yellow]"
        )
    result = svc.nudge(
        date.today(), horizon_days=weeks * 7, top=top, live=live, include_taken=include_taken,
        lanes=list(lane) if lane else None,
    )
    nudges = result["nudges"]
    if as_json:
        payload = {
            "nudges": [n.model_dump(mode="json") | {"line": n.line} for n in nudges],
            "skipped": result["skipped"],
            "note": result["note"],
            "lanes": result["lanes"],
            "decision_trip": result["decision_trip"],
        }
        console.print_json(json.dumps(payload))
        return
    if result["note"]:
        console.print(f"[yellow]{result['note']}[/yellow]")
    if not nudges:
        console.print("[dim]No nudges cleared the bar in this horizon (or every window is taken).[/dim]")
    lane_titles = {
        "weekend": "Weekends", "games": "Game weekends", "long-lead": "Long-lead (3–12 months)",
        "horizon": "Horizon — worth a look", "family": "Family windows",
    }
    current = ""
    for i, n in enumerate(nudges, start=1):
        if n.lane != current:
            current = n.lane
            console.print(f"\n[bold magenta]── {lane_titles.get(n.lane, n.lane)} ──[/bold magenta]")
        console.print(f"\n[bold]{i}. {n.line}[/bold]")
        if n.lane == "family":
            continue
        comp = " · ".join(f"{c.name} {c.points:+g}" for c in sorted(n.components, key=lambda c: -c.points))
        console.print(f"   [dim]score {n.score:g}  ·  {comp}[/dim]")
        for e in n.events[:3]:
            when = e.local_date + (f" {e.local_time}" if e.local_time else "")
            venue = f" · {e.venue}" if e.venue else ""
            console.print(f"   [cyan]•[/cyan] {e.name} [dim]({when}{venue})[/dim]")
        if n.weather_note:
            console.print(f"   [blue]☂[/blue] {n.weather_note}")
    if result["skipped"]:
        console.print("\n[dim]Taken windows skipped: " + " · ".join(result["skipped"]) + "[/dim]")


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
    radius: int | None = typer.Option(
        None, "--radius",
        help="Search miles around each place instead of matching the city name (default: 25 for "
        "the home base, city-name match for a named --city)",
    ),
    flat: bool = typer.Option(
        False,
        "--flat",
        help="With --json: emit ONE flat array of events (each carries city/venue/url/tier) instead "
        "of the per-city nesting — the dashboard-table shape (the TM `url` renders as a purchase link).",
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
            if not (frm and to):
                _fail("provide --trip, OR --from and --to (optionally --city; omit it to use your home base)")
                raise typer.Exit(code=1)
            cities = city or [svc.weights.conditions.home]
            ref_start, ref_end = date.fromisoformat(frm), date.fromisoformat(to)
            # the home base is a radius by default (a suburb has no venues of its own); a named
            # --city matches the city unless --radius asks for a radius
            radius_mi = radius if radius is not None else (0 if city else 25)
            scans = svc.scan_events(cities, ref_start, ref_end, classification=category,
                                    radius_mi=radius_mi)
            window = f"{frm} → {to}"
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    except (FileNotFoundError, KeyError, ValueError) as e:
        _fail(f"could not resolve trip {trip!r}: {e}")
        raise typer.Exit(code=1) from e

    if as_json:
        if flat:
            # one flat array (each event already carries city/venue/url) — the value_path-free table
            # shape for the dashboard; the TM `url` auto-renders as a "post ↗" purchase link. Sorted
            # soonest-first and capped at --limit (a long window near a big metro returns hundreds —
            # a dashboard wants the next N); --all lifts the cap.
            flat_events = sorted(
                (
                    {**e.model_dump(), "tier": svc.event_tier(e)}
                    for s in scans
                    for e in s.events
                ),
                key=lambda e: (e.get("local_date") or "", e.get("local_time") or ""),
            )
            if not all_events:
                flat_events = flat_events[:limit]
            console.print_json(json.dumps(flat_events))
            return
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
def calendar(
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    city: str | None = typer.Option(
        None, "--city", help="Live-events city (default: your home base, weights `conditions.home`)"
    ),
    variant: str = typer.Option(
        "grid", "--variant",
        help="Twin selector: 'grid' = the dense multi-month view; 'big' = the one-month wall board",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The calendar-grid contract: reference almanac + live ticketed events merged
    into per-day buckets — the Travel ▸ Calendar interactive grid's data source (also renders via
    the static `calendar` viz type through its derived months[]). Live events are best-effort;
    the almanac always renders."""
    svc = TravelService()
    data = svc.calendar_data(date.fromisoformat(frm), date.fromisoformat(to), city=city, variant=variant)
    if as_json:
        console.print_json(json.dumps(data))
        return
    days = data.get("days") or []
    console.print(f"[bold]{data.get('title')}[/bold]  [dim]{data.get('subtitle')}[/dim]")
    assert isinstance(days, list)
    for d in days[:30]:
        items = d.get("items") or []
        names = " · ".join(str(i.get("label")) for i in items[:4])
        more = f" (+{len(items) - 4})" if len(items) > 4 else ""
        console.print(f"  {d.get('date')}  {names}{more}")
    if len(days) > 30:
        console.print(f"[dim]  … {len(days) - 30} more day(s) — use --json for the full set[/dim]")


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
    kind: str = typer.Option(
        "", "--kind", help="Filter to one pipeline kind: 'trip' (outbound) or 'visit' (inbound)."
    ),
) -> None:
    """The travel horizon — every upcoming trip + visit from the corpus `trip:` frontmatter,
    countdown-ordered (the command-center spine read from corpus/travel/{trips,visits}/). `--all`
    appends completed/cancelled history (most-recent first). `--kind trip|visit` narrows the
    pipeline to one mode. Parked ideas (status `idea`) are never in the horizon or its count; they
    come back under their own `someday` key."""
    svc = TravelService()
    rows = svc.trip_pipeline(date.today(), include_past=all_trips)
    someday = svc.trip_pipeline(date.today(), phase="someday")
    # Optional kind filter: one shared pipeline read, narrowed to one mode
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
        someday = [r for r in someday if r.get("kind") == kind]
    if as_json:
        console.print_json(json.dumps({
            "as_of": date.today().isoformat(), "count": len(rows), "trips": rows, "someday": someday,
        }))
        return
    if not rows and not someday:
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
    if someday:
        console.print("\n[bold]Someday[/bold] [dim](parked ideas — not on the horizon)[/dim]")
        for r in someday:
            console.print(f"  • {r['destination']}  [dim]{r['date']} · {r['kind']} · {r['who']}[/dim]")


@app.command()
def upcoming(as_json: bool = typer.Option(False, "--json")) -> None:
    """What's on the calendar — one card per live trip or visit, soonest first: window, countdown,
    who, cover photo and whether an open decision hangs on it. Undated live items and parked ideas
    are listed apart under `someday`, never counted down. The Planning tab's top band."""
    data = TravelService().planning_calendar()
    if as_json:
        console.print_json(data.model_dump_json())
        return
    if not data.upcoming and not data.someday:
        console.print("[dim](nothing on the calendar)[/dim]")
        return
    for c in data.upcoming:
        flag = "  [yellow]open decision[/yellow]" if c.decision else ""
        console.print(f"[bold]{c.when:>8}[/bold]  {c.name}  [dim]{c.window} · {c.kind} · "
                      f"{', '.join(c.who) or '—'}[/dim]{flag}")
    if data.someday:
        console.print("\n[bold]Someday[/bold]")
        for c in data.someday:
            console.print(f"  • {c.name}  [dim]{c.window} · {c.kind} · {c.status}[/dim]")


@app.command()
def explore(
    origins: str = typer.Option(
        "", "--from", help="Origin IATA codes, comma-separated (default: home airport + hub)"
    ),
    month: int = typer.Option(0, "--month", help="Flexible dates: month 1-12 (0 = the next six months)"),
    duration: str = typer.Option(
        "week", "--duration", help="Flexible trip length: weekend | week | two-weeks"
    ),
    depart: str = typer.Option("", "--depart", help="Exact dates instead: outbound YYYY-MM-DD"),
    return_date: str = typer.Option("", "--return", help="Exact return YYYY-MM-DD (omit = one way)"),
    cabin: str = typer.Option("economy", "--class", help="economy | premium | business | first"),
    stops: str = typer.Option("any", "--stops", help="any | nonstop | 1 | 2 (max stops)"),
    max_price: int = typer.Option(0, "--max-price", help="Per-person round-trip USD ceiling (0 = none)"),
    to: str = typer.Option(
        "", "--to", help="Region (caribbean, mexico, hawaii, central-america, south-america, europe, "
        "asia, japan, canada), a Google /m/ id, or one airport code (default: Google's picks)",
    ),
    limit: int = typer.Option(25, "--limit", help="Rows to show (0 = all)"),
    refresh: bool = typer.Option(False, "--refresh", help="Force fresh searches (skip the 24h cache)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Where can we fly, and for how much: Google Travel Explore (via SerpAPI) lists destinations
    with the cheapest per-person fare from each origin, for exact dates or a flexible month + trip
    length. Merged across origins, cheapest first; the avoid list is excluded; destinations already
    profiled in the corpus are marked. **Quota: 1 search per origin** (24h cache, re-views free)."""
    svc = TravelService()
    try:
        res = svc.explore_destinations(
            origins=[o.strip() for o in origins.split(",") if o.strip()] or None,
            outbound=date.fromisoformat(depart) if depart else None,
            return_=date.fromisoformat(return_date) if return_date else None,
            month=month, duration=duration, travel_class=cabin, stops=stops,
            max_price=max_price or None, to=to or None, limit=limit, refresh=refresh,
        )
    except (ProviderError, ValueError) as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(res.model_dump(mode="json")))
        return
    spent = f"{res.searches} live search(es) — {res.searches} quota" if res.searches else "cached — 0 quota"
    console.print(f"\n[bold]Explore from {' + '.join(res.origins)}[/bold]  "
                  f"[dim]{res.dates}, {cabin}, per person[/dim]  [dim]({spent})[/dim]")
    for n in res.notes:
        console.print(f"[yellow]{n}[/yellow]")
    if not res.destinations:
        console.print("[yellow]No priced destinations (widen --month / --max-price / --stops).[/yellow]")
        return
    table = Table()
    for col in ("Destination", "Fare", *(f"{o}" for o in res.origins), "Dates", "Stops", "Flight",
                "Airline", "Hotel/n", "Corpus"):
        table.add_column(col)
    for d in res.destinations:
        best = d.best
        by_origin = {f.origin: f for f in d.fares}
        per_origin = []
        for o in res.origins:
            f = by_origin.get(o)
            per_origin.append("—" if f is None or f.price_usd is None else f"${f.price_usd:,.0f}")
        mins = best.flight_minutes if best else None
        when = best.start_date if best else ""
        if best and best.end_date:
            when = f"{best.start_date[5:]} → {best.end_date[5:]}"
        table.add_row(
            f"{d.name}, {d.country}" if d.country and d.country != "United States" else d.name,
            f"${best.price_usd:,.0f}" if best and best.price_usd is not None else "—",
            *per_origin,
            when,
            "—" if not best or best.stops is None else ("nonstop" if best.stops == 0 else str(best.stops)),
            f"{mins // 60}h{mins % 60:02d}" if mins else "—",
            best.airline if best else "",
            f"${best.hotel_price_usd:,.0f}" if best and best.hotel_price_usd else "—",
            ", ".join(d.corpus),
        )
    console.print(table)


@app.command()
def quota(as_json: bool = typer.Option(False, "--json")) -> None:
    """The SerpAPI budget: searches left this month, usage, renewal date. Free: the Account API
    does not count against the quota."""
    from harness.travel.config.settings import get_settings
    from harness.travel.providers.serpapi_explore_provider import account_quota

    try:
        q = account_quota(get_settings().serpapi_key)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(q))
        return
    console.print(
        f"SerpAPI {q.get('plan_name') or ''}: [bold]{q.get('total_searches_left')}[/bold] searches left of "
        f"{q.get('searches_per_month')} · used {q.get('this_month_usage')} this month · "
        f"{q.get('this_hour_searches')} this hour · renews {q.get('plan_renewal_date') or '—'}"
    )


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
    pick: str = typer.Option(
        "", "--pick", help="Comma-separated name fragments: keep exactly these properties (in this "
        "order) from the FULL result list, not the first --limit (e.g. 'Parker,Sparrows')",
    ),
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
            limit=100 if pick else limit,  # --pick chooses from the whole list (same search, 0 extra quota)
            vacation_rentals=vacation_rentals,
            refresh=refresh,
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if pick:
        from harness.travel.service import pick_offers

        try:
            res.offers = pick_offers(res.offers, [t for t in pick.split(",") if t.strip()])
        except ValueError as e:
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
                f"{np.name} ({np.duration} {np.transport})".strip().replace("( )", "")
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
    d0 = fc.days[0]
    if d0.sunrise and d0.sunset:  # the sun window rides the card header (same-call field)
        console.print(f"[dim]sun {d0.sunrise[11:16]}–{d0.sunset[11:16]} ({d0.date})[/dim]")
    table = Table()
    unit = fc.temperature_unit
    punit = fc.precipitation_unit
    cols = ("Date", "Conditions", f"High ({unit})", "Feels", f"Low ({unit})", "Precip %",
            "Rain (hrs)", "Snow", "UV", f"Gusts ({fc.wind_unit})")
    for col in cols:
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
            "—" if d.feels_max is None else f"{d.feels_max:.0f}",
            "—" if d.temp_min is None else f"{d.temp_min:.0f}",
            "—" if d.precip_prob is None else f"{d.precip_prob}%",
            rain, snow,
            "—" if d.uv_index_max is None else f"{d.uv_index_max:.0f}",
            "—" if d.wind_gusts_max is None else f"{d.wind_gusts_max:.0f}",
        )
    console.print(table)


@app.command()
def air(
    city: str | None = typer.Option(
        None, "--city",
        help="Place name, e.g. 'Denver'. Omit to use your home base (weights `conditions.home`, pack-aware).",
    ),
    frm: str = typer.Option(..., "--from", help="Window start YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="Window end YYYY-MM-DD"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Daily air-quality (US AQI / PM2.5 max) over a window — the wildfire-smoke sense (Open-Meteo,
    keyless). AQI horizon is shorter than weather (~5 days). With no `--city`, uses the home base."""
    svc = TravelService()
    where = city or svc.weights.conditions.home
    try:
        rep = svc.get_air_quality(where, date.fromisoformat(frm), date.fromisoformat(to))
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
    from harness.bus.store import harness_state_dir

    state_dir = harness_state_dir()  # HARNESS_STATE_DIR-sealed, like the finance pulse log
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


def _commit_conditions_reports(root: Path) -> str:
    """Auto-commit the standing agent's report dir to the corpus repo.

    The scheduled run WRITES reports/{date}.md; without a commit they accumulate as untracked drift.
    Discipline, multi-agent-safe by construction:
      - commits ONLY the reports pathspec (`git commit -- <dir>`), so another session's staged
        work is never swept into the standing agent's commit;
      - push is FAST-FORWARD-ONLY best-effort: a headless agent never mutates the working tree
        (no pull --rebase here — a dirty tree is common, and
        an autostash reapply-conflict could strand a live session's edits in a stash). If the
        remote has moved, the commit stays local and rides the next interactive session's routine
        pull-rebase + push. NEVER force;
      - every git failure degrades to a returned note for the pulse log — the morning run must
        never die on repo state.
    Deterministic side effect — no model in the loop; the message is a fixed template.
    """
    import subprocess

    rel = "travel/conditions/reports"

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
        )

    try:
        st = _git("status", "--porcelain", "--", rel)
        if st.returncode != 0:
            return f"commit:skip ({(st.stderr.strip() or 'git status failed')[:80]})"
        if not st.stdout.strip():
            return "commit:clean"
        if _git("add", "--", rel).returncode != 0:
            return "commit:add-failed"
        # count AFTER add — porcelain collapses a fully-untracked dir into one "?? dir/" line
        staged = _git("diff", "--cached", "--name-only", "--", rel)
        n = len(staged.stdout.strip().splitlines()) or 1
        msg = f"travel(conditions): standing-agent morning-report auto-commit ({n} file(s))"
        if _git("commit", "-m", msg, "--", rel).returncode != 0:
            return "commit:commit-failed"
        if _git("push", "--quiet").returncode != 0:
            return f"commit:ok({n}) push:deferred (remote moved or unreachable — rides the next session push)"
        return f"commit:ok({n}) push:ok"
    except Exception as e:  # noqa: BLE001 — git trouble is a note, never a dead morning run
        return f"commit:error ({str(e)[:80]})"


@app.command()
def pulse(
    as_json: bool = typer.Option(False, "--json", help="Machine output for the scheduled agent"),
    report: bool = typer.Option(
        False, "--report",
        help="The 6am morning mode (launchd): write the digest to travel/conditions/reports/{date}.md "
        "+ publish ONE escalating morning event to the bus. The always-fires daily ritual.",
    ),
    commit: bool = typer.Option(
        False, "--commit",
        help="After --report: commit the reports dir to the corpus repo + pull-rebase + push "
        "(path-scoped; any git failure degrades to a log note — the run never dies on repo state).",
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
        if commit:
            notes.append(_commit_conditions_reports(root))
    if notify:
        notes.append(_publish_conditions_to_bus(rep))
    if report or notify:
        _conditions_log(rep, bus_note=" ".join(n for n in notes if n))

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    console.print(f"\nconditions · {rep.as_of} · [bold]{rep.home}[/bold]")
    if rep.outdoor and rep.outdoor.note:  # the outdoor-windows one-liner rides every render
        console.print(f"[cyan]{rep.outdoor.note}[/cyan]")
    if rep.quiet:
        console.print(
            "[green]QUIET — nothing crossed. No heat / smoke / wet day / snow / UV / wind "
            "in the window.[/green]"
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
    yelp: bool = typer.Option(
        False, "--yelp", help="QUOTA: merge the Yelp texture layer — snippets/neighborhoods/Yelp "
        "ratings (1 SerpAPI search, cached; confirm first)"
    ),
    yelp_query: str = typer.Option(
        "restaurants", "--yelp-query",
        help="Yelp's find_desc lens ('brunch', 'cocktail bars', 'Best dinner restaurants')",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the ratings cache (re-spends quota)"),
    hero_images: bool = typer.Option(
        False, "--hero-images", help="KEYLESS: scrape each place's own-website og:image hero"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Eateries near a place — multi-tier food discovery (what exists + what's good + what
    locals say). Default =
    OSM Overpass enumeration (KEYLESS, free): what exists, from data not memory. --live-ratings
    layers Google ratings/price on top (1 quota search, day-cached). --yelp layers the Yelp
    texture tier — review snippets, neighborhoods, Yelp's own ratings (1 quota search, day-cached;
    --yelp-query drives the lens). --hero-images lifts og:image heroes from the places' own
    websites (keyless GETs, no quota)."""
    try:
        rep = TravelService().find_food(
            near, radius_m=radius, live_ratings=live_ratings, yelp=yelp, yelp_query=yelp_query,
            refresh=refresh, hero_images=hero_images,
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
        if ea.yelp_rating is not None:
            # Both lenses shown side-by-side — cross-source divergence is signal, not noise.
            rating = f"{rating} · Y {ea.yelp_rating:.1f} ({ea.yelp_reviews or 0})"
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


@app.command(name="food-photos")
def food_photos(
    near: str = typer.Option(..., "--near", help="Same place the live-ratings sweep ran against"),
    names: str = typer.Option(
        ..., "--names", help="Comma-separated finalist names (as they appear in the sweep)"
    ),
    radius: int = typer.Option(1500, "--radius", help="Match the sweep's radius"),
    limit_per: int = typer.Option(12, "--limit-per", help="Max gallery photos per place"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the per-place photo cache"),
    archive: str = typer.Option(
        "", "--archive",
        help="Vault-relative dir under travel/ to store the first N photos per place through the "
        "size cap (e.g. destinations/x/food/2026-09-assets) — the tool for the gallery-archive "
        "case; never curl originals into the vault",
    ),
    per_place: int = typer.Option(2, "--per-place", help="Photos stored per place with --archive"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """QUOTA (1 search PER NAME): full Google-Maps photo galleries for named finalists —
    deliberate-spend, finalists only (thumbnails already ride the live-ratings sweep free).
    Requires the sweep to have run (names resolve → data_id via its cache; the read costs 0)."""
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    svc = TravelService()
    try:
        galleries = svc.food_photos(
            near, name_list, radius_m=radius, limit_per=limit_per, refresh=refresh
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    stored = svc.archive_food_photos(galleries, archive, per_place=per_place) if archive else {}
    if as_json:
        console.print_json(json.dumps({"galleries": galleries, "archived": stored}))
        return
    for name, photos in galleries.items():
        console.print(f"\n[bold]{name}[/bold]  [dim]{len(photos)} photos[/dim]")
        for u in photos:
            console.print(f"  {u}")
        if not photos:
            console.print("  [yellow]no match in the sweep (name spelling? sweep not run?)[/yellow]")
        for rel in stored.get(name, []):
            console.print(f"  [green]archived[/green] {rel}")


@app.command(name="shrink-images")
def shrink_images(
    path: str = typer.Argument(
        ..., help="Vault-relative dir (e.g. travel/destinations/x/food/2026-09-assets)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing"),
    skip_png: bool = typer.Option(
        False, "--skip-png",
        help="Leave PNGs untouched (map canvases that SVG overlays are registered against)",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Apply the vault image-size discipline (≤1600px long edge · JPEG q85 · ~400KB) to images
    already on disk, IN PLACE with extensions kept so every embed still resolves. Prints
    before/after per file + the total saved. Pair with a git history rewrite for old blobs."""
    try:
        reports = TravelService().shrink_images(path, dry_run=dry_run, skip_png=skip_png)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps([
            {"path": str(r.path), "before": r.before, "after": r.after, "width": r.width,
             "height": r.height, "over_cap": r.over_cap, "changed": r.changed}
            for r in reports
        ]))
        return
    table = Table(title=f"shrink-images → {path}{' (dry run)' if dry_run else ''}")
    for col in ("File", "Before", "After", "Dims", "Note"):
        table.add_column(col)
    changed = [r for r in reports if r.changed]
    for r in changed:
        table.add_row(
            r.path.name, f"{r.before // 1024}KB", f"{r.after // 1024}KB", f"{r.width}x{r.height}",
            "over cap (best effort)" if r.over_cap else "",
        )
    console.print(table)
    console.print(
        f"{len(changed)} of {len(reports)} images shrunk · "
        f"saved {sum(r.saved for r in changed) / 1e6:.1f}MB"
        + (" · nothing written" if dry_run else "")
    )


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
    alerts on WSDOT-instrumented corridors (Washington state). Filter with --near (a town name) /
    --road (I-5) / --category (Construction)."""
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
        None, "--route", help="'Departing-Arriving', e.g. 'Edmonds-Kingston' → today's sailings"
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
        "(e.g. 'trips/2027-01-sample-trip' or 'viz-demo')",
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




def _mins(v: int | None) -> str:
    return f"{v}m" if v and v > 0 else "—"


def _delay(t: TravelTime) -> str:
    """Render a travel-time delay: red +N when congested, plain ±N otherwise, — when no reading."""
    d = t.delay_minutes
    if d is None:
        return "—"
    s = f"+{d}" if d > 0 else str(d)
    return f"[red]{s}[/red]" if t.congested else s


# Ferry sailings and vessel ETAs print in the ferry network's own zone (Washington State Ferries run on
# Pacific time), so a schedule reads the same wherever the terminal is queried from.
_FERRY_TZ = ZoneInfo("America/Los_Angeles")


def _ptime(iso: str | None) -> str:
    """An ISO-8601 UTC ferry timestamp → the ferry network's local time as 'h:MM AM/PM'."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(_FERRY_TZ).strftime("%-I:%M %p")
    except ValueError:
        return "—"


if __name__ == "__main__":
    app()
