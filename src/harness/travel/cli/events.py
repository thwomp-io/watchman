"""Events in a window: live ticketed events, the static reference almanac, and the merged calendar grid."""

from __future__ import annotations

import json
from datetime import date

import typer
from rich.table import Table

from harness.errors import HarnessError, UsageError
from harness.travel.cli.common import console
from harness.travel.models import EventResult
from harness.travel.providers.base import ProviderError
from harness.travel.service import TravelService


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
                raise UsageError(
                    "provide --trip, or --from and --to",
                    hint="--city is optional; without it, your home base is used.",
                )
            cities = city or [svc.weights.conditions.home]
            ref_start, ref_end = date.fromisoformat(frm), date.fromisoformat(to)
            # the home base is a radius by default (a suburb has no venues of its own); a named
            # --city matches the city unless --radius asks for a radius
            radius_mi = radius if radius is not None else (0 if city else 25)
            scans = svc.scan_events(cities, ref_start, ref_end, classification=category,
                                    radius_mi=radius_mi)
            window = f"{frm} → {to}"
    except ProviderError as e:
        raise HarnessError(str(e)) from e
    except (FileNotFoundError, KeyError, ValueError) as e:
        raise HarnessError(f"could not resolve trip {trip!r}: {e}") from e

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
