"""Live conditions: weather, air quality, earthquakes, sun times, WA traffic and ferries, and the
standing conditions watch (`pulse`) with its bus and corpus outputs."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.table import Table

from harness.autocommit import commit_path
from harness.errors import UsageError
from harness.travel.cli.common import console
from harness.travel.conditions import ConditionsReport
from harness.travel.config.settings import get_settings
from harness.travel.models import TravelTime
from harness.travel.service import TravelService


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
    fc = svc.get_weather(
        where, date.fromisoformat(frm), date.fromisoformat(to), fahrenheit=not celsius
    )

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
    rep = svc.get_air_quality(where, date.fromisoformat(frm), date.fromisoformat(to))

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
    """Commit the morning reports the scheduled run wrote (see `harness.autocommit` for the rules).
    Returns the note for the run log."""
    return commit_path(
        root,
        "travel/conditions/reports",
        "travel(conditions): standing-agent morning-report auto-commit ({n} file(s))",
    ).note


def pulse(
    as_json: bool = typer.Option(False, "--json", help="Machine output for the scheduled agent"),
    report: bool = typer.Option(
        False, "--report",
        help="The 6am morning mode (launchd): write the digest to travel/conditions/reports/{date}.md "
        "+ publish ONE escalating morning event to the bus. The always-fires daily ritual.",
    ),
    commit: bool = typer.Option(
        False, "--commit",
        help="After --report: commit the reports dir to the corpus repo and push if it fast-forwards "
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
    rep = TravelService().conditions_pulse()

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


def quakes(
    city: str = typer.Option(..., "--city", help="Place name, e.g. 'Denver' or 'Reykjavik'"),
    radius: int = typer.Option(300, "--radius", help="Search radius km"),
    days: int = typer.Option(90, "--days", help="Lookback window (days)"),
    min_mag: float = typer.Option(2.5, "--min-mag", help="Minimum magnitude"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Recent earthquakes near a place — the geological-screen sense (USGS, keyless). Real data to
    calibrate the screen, not categorical priors."""
    rep = TravelService().get_earthquakes(
        city, radius_km=radius, days_back=days, min_magnitude=min_mag
    )

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


def sun(
    place: str = typer.Argument(..., help="Place name (geocoded), e.g. 'San Diego'"),
    on: str = typer.Option(..., "--date", help="Date YYYY-MM-DD"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Sunrise/sunset/twilight + golden-hour approximations for a place-day (keyless,
    sunrise-sunset.org). Times are ISO-8601 UTC. Golden-hour fields are approximate."""
    st = TravelService().sun_times(place, date_str=on)
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
    report = TravelService().get_traffic(
        near=near,
        road=road,
        category=category,
        congested_only=congested,
        include_times=not alerts_only,
        include_alerts=not times_only,
        congestion_threshold=threshold,
    )

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
        raise UsageError("pass --route (schedule) and/or --space / --vessels")
    report = TravelService().get_ferry(
        route=route, space=space, vessels=vessels, only_remaining=not all_today
    )

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
