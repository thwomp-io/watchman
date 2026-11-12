"""The trip horizon: every trip and visit on the calendar, and proactive nudges toward new ones."""

from __future__ import annotations

import json
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from harness.errors import HarnessError
from harness.travel.cli.common import console
from harness.travel.service import TravelService


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
        raise HarnessError(f"unknown lane(s): {', '.join(bad)}", hint=f"Valid: {', '.join(sorted(valid))}.")
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
