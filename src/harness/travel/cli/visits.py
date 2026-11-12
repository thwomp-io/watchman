"""A visit's plan: the idea bank (outings + restaurants), its enrichment and photos, the day-by-day
schedule, and the active decision's compare data."""

from __future__ import annotations

import json

import typer

from harness.errors import HarnessError
from harness.travel.cli.common import console
from harness.travel.providers.base import ProviderError
from harness.travel.service import TravelService


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
        raise HarnessError(str(e)) from e
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
        raise HarnessError(str(e)) from e
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
        raise HarnessError(str(e)) from e
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
        raise HarnessError(str(e)) from e
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
