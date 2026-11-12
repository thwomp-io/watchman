"""Corpus-facing verbs: the parsed preferences digest, and D3 diagrams rendered into the vault."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from harness.errors import HarnessError, UsageError
from harness.travel.cli.common import console, travel_service
from harness.travel.config.settings import get_settings
from harness.travel.models import CityEvents
from harness.travel.viz import (
    VizError,
    embed_markdown,
    events_to_timeline,
    reference_to_calendar,
    render_diagram,
    render_map_annotate,
    weather_to_strip,
)


def prefs() -> None:
    """Print the parsed preferences digest (sanity-check the corpus read)."""
    d = travel_service("serpapi").read_preferences()
    console.print("[bold]Travel preferences digest[/bold]")
    console.print(f"  Avoided airlines : {', '.join(d.avoid_airline_names) or '(none found)'}")
    console.print(f"  Lodging bar      : {d.lodging_bar}")
    console.print(f"  Home airport pref: {d.home_airport_preferred}")


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
            data = travel_service("serpapi").map_data_for_trip(trip)
        except (FileNotFoundError, KeyError, ValueError) as e:
            raise HarnessError(f"could not build map for trip {trip!r}: {e}") from e
    elif diagram == "calendar" and frm and to:
        s, e_ = date.fromisoformat(frm), date.fromisoformat(to)
        ref = travel_service("serpapi").scan_reference(s, e_)
        data = reference_to_calendar(
            ref, start=s, end=e_,
            title=title or f"Travel calendar ({frm} → {to})",
            subtitle=subtitle or "long-weekends · centerpiece games · key dates — from the reference almanac",
        )
    elif city and frm and to and diagram == "weather-strip":
        fc = travel_service("serpapi").get_weather(city, date.fromisoformat(frm), date.fromisoformat(to))
        data = weather_to_strip(
            fc,
            title=title or f"{city} — weather ({frm} → {to})",
            subtitle=subtitle or "live forecast · °F hi/lo + precip chance",
        )
    elif city and frm and to:
        scans = travel_service("serpapi").scan_events(
            [city], date.fromisoformat(frm), date.fromisoformat(to), classification=category
        )
        ce = scans[0] if scans else CityEvents(city=city)
        data = events_to_timeline(
            ce,
            start=frm,
            end=to,
            title=title or f"{city} — what's on ({frm} → {to})",
            subtitle=subtitle,
        )
    else:
        raise UsageError("provide --data <json>, OR --city with --from/--to, OR --trip (for diagram=map)")

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
        raise HarnessError(str(e)) from e

    console.print(f"[green]✓[/green] wrote {written}")
    console.print("\n[bold]Obsidian embed[/bold] (paste into the doc):")
    console.print(embed_markdown(written), markup=False)  # markup=False: keep the [[...]] literal


def mcp() -> None:
    """Launch the MCP server (stdio)."""
    from harness.travel.mcp_server import main as mcp_main

    mcp_main()
