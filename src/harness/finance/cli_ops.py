"""Standing-agent and surface verbs of `hn finance`: pulse (+ its bus publish), watch, the gates
board, the trap map, viz, the MCP server.

Commands are registered on the `finance` app in `harness.finance.cli` (that file fixes the
order `hn finance --help` lists them in)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from harness.finance.config.settings import get_settings
from harness.finance.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.finance.watch import PulseReport
from harness._cli import fail as _fail
from harness.finance._cli import _money_short, _signed, _svc, console
from harness.viz import KNOWN_TYPES, VizError, render_diagram


def _publish_to_bus(rep: PulseReport) -> str:
    """Publish pulse flags to the harness bus — the durable human-event layer the
    tray app delivers from. Returns a 'N published, M dup' note for the run log: the forensic
    proof events reached the bus (next time delivery is questioned, the audit answers).

    Publishes BOTH the act/watch flags AND the info-severity single-name catalyst wire — the
    latter feeds the Inbox WIRE band, so it must publish even on a flag-quiet run
    (fresh catalysts exist without a threshold flag). Never kills the standing run — a bus failure
    degrades to an ERROR note in the log (graceful-degradation rule; one dead layer is a note)."""
    _news = rep.digest.fresh_news if rep.digest else []
    catalysts = [it for it in _news if it.symbol and it.source != "sec.gov"]
    filtered = list(rep.digest.filtered_news) if rep.digest else []
    if rep.quiet and not catalysts and not filtered:
        return ""
    link_note = ""
    if catalysts:
        # Google News links → the publisher's article (cached, capped). Only catalysts NEW to the
        # bus spend the cap — the watch re-hands many already-published ones, which the bus dedupes
        # anyway; the junk is never resolved.
        try:
            from harness.bus.service import BusService
            from harness.finance.catalysts import resolve_links
            from harness.finance.events import catalyst_key

            bus = BusService()
            try:
                known = bus.existing_keys([catalyst_key(it) for it in catalysts])
            finally:
                bus.close()
            fresh = [it for it in catalysts if catalyst_key(it) not in known]
            done, link_note = resolve_links(fresh)
            by_id = {id(a): b for a, b in zip(fresh, done, strict=True)}
            catalysts = [by_id.get(id(it), it) for it in catalysts]
        except Exception as e:  # noqa: BLE001 — resolution is a nicety; publishing must go on
            link_note = f"links ERROR: {e}"
    try:
        from harness.bus.service import BusService
        from harness.finance.events import events_from_catalysts, events_from_pulse
        from harness.finance.research import resolve_research_dir

        # symbol → vault-relative research dir, existence-checked (no dead deep-links). The Inbox
        # renders payload.ref as a "go to →" jump to the stock's newest report.
        tracker = get_settings().tracker_path
        ref_dirs: dict[str, str] = {}
        for sym in {f.symbol for f in rep.flags} | {it.symbol for it in catalysts}:
            d = resolve_research_dir(sym, tracker, set())
            if d.is_dir():
                ref_dirs[sym] = str(d.relative_to(tracker))

        drafts = events_from_pulse(rep, ref_dirs) + events_from_catalysts(catalysts + filtered, ref_dirs)
        results = BusService().publish_many(drafts)
        published = sum(1 for r in results if r.status == "published")
        note = (f"bus: {published} published, {len(results) - published} dup "
                f"({len(catalysts)} catalyst, {len(filtered)} filtered)")
        return f"{note} · {link_note}" if link_note else note
    except Exception as e:  # noqa: BLE001 — standing loop must survive any bus failure
        return f"bus ERROR: {e}"


def _pulse_notify(rep: PulseReport, bus_note: str = "") -> None:
    """Standing-agent side effects: run-log always; osascript notification on NEW
    flags only.

    ⚠️ DEPRECATED TRANSPORT: the osascript banner is transient + posts under an
    unauthorized identity, and pulse-flags.json duplicates dedup the bus now owns — both retire
    once the console is the verified human transport. Until then this stays as the
    fallback so delivery never regresses below today's (imperfect) baseline."""
    import json
    import subprocess
    from datetime import datetime

    from harness.bus.store import harness_state_dir

    state_dir = harness_state_dir()  # HARNESS_STATE_DIR-sealed (a sandbox pulse never writes the real log)
    state_dir.mkdir(parents=True, exist_ok=True)
    log = state_dir / "pulse.log"
    flag_state = state_dir / "pulse-flags.json"

    today = date.today().isoformat()
    seen: dict[str, list[str]] = {}
    if flag_state.exists():
        try:
            seen = json.loads(flag_state.read_text())
        except json.JSONDecodeError:
            seen = {}
    seen_today = set(seen.get(today, []))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if rep.quiet:
        line = f"{stamp} quiet"
    else:
        keys = [f"{f.kind}:{f.symbol}" for f in rep.flags]
        fresh = [f for f, k in zip(rep.flags, keys, strict=True) if k not in seen_today]
        msgs = "; ".join(f.message for f in rep.flags)
        line = f"{stamp} {len(rep.flags)} flag(s) ({len(fresh)} new): {msgs}"
        if bus_note:
            line += f" [{bus_note}]"
        if fresh:
            title = f"hn pulse — {len(fresh)} new flag(s)"
            body = "; ".join(f.message for f in fresh)[:180].replace('"', "'")
            script = f'display notification "{body}" with title "{title}" sound name "Glass"'
            subprocess.run(
                ["osascript", "-e", script], check=False, capture_output=True, timeout=10
            )
        flag_state.write_text(json.dumps({today: sorted(seen_today | set(keys))}))
    with log.open("a") as f:
        f.write(line + "\n")


def pulse(
    as_json: bool = typer.Option(False, "--json", help="Machine output for the scheduled agent"),
    no_mark_seen: bool = typer.Option(False, "--no-mark-seen", help="Don't consume the news seen-cache"),
    notify: bool = typer.Option(
        False, "--notify",
        help="Standing-agent mode (launchd): macOS notification when flags exist; silent when quiet; "
        "appends one line to ~/.local/state/harness/pulse.log either way",
    ),
) -> None:
    """Market pulse: watch digest + open-GTC trap distances + DETERMINISTIC flags
    (day-move / trap-proximity / print-soon; thresholds in portfolio.yaml `pulse:`). Quiet days say
    quiet — the cron consumer notifies on flags only."""
    try:
        rep = _svc().pulse(mark_seen=not no_mark_seen)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if notify:
        _pulse_notify(rep, bus_note=_publish_to_bus(rep))
    if as_json:
        console.print_json(rep.model_dump_json(exclude={"digest"}))
        return

    console.print(f"\npulse · {rep.as_of}")
    if rep.quiet:
        console.print(
            "[green]QUIET — no flags. Holdings calm; traps not in range; no print imminent.[/green]"
        )
    else:
        console.print(f"[bold red]{len(rep.flags)} FLAG(S):[/bold red]")
        for f in rep.flags:
            console.print(f"  [red]•[/red] [{f.kind}] {f.message}")
    if rep.orders:
        table = Table(title="Open orders (the GTC ledger)")
        for col in ("Side", "Symbol", "Qty", "Limit", "Price", "Distance", "Expires"):
            table.add_column(col)
        for o in rep.orders:
            table.add_row(
                o.side.upper(), o.symbol, f"{o.qty:g}", f"${o.limit:,.2f}",
                f"${o.price:,.2f}" if o.price is not None else "—",
                f"{o.distance_pct:+.1f}%" if o.distance_pct is not None else "—",
                o.expires or "—",
            )
        console.print(table)
    if rep.digest and rep.digest.fresh_news:
        n = len(rep.digest.fresh_news)
        console.print(f"[dim]  {n} fresh headline(s) since last pulse — `hn finance watch` for detail[/dim]")


def watch(
    no_mark: bool = typer.Option(
        False, "--no-mark", help="Don't mark headlines as seen (peek without consuming the delta)"
    ),
    limit: int = typer.Option(4, "--limit", help="Headlines pulled per symbol before delta filter"),
    no_ticker_news: bool = typer.Option(
        False, "--no-ticker-news",
        help="Skip the per-held-stock Google News search (single-name catalyst breadth) for a faster run",
    ),
    as_json: bool = typer.Option(False, "--json", help="Raw JSON digest"),
) -> None:
    """One-shot standing-watch digest: day moves, rebalance-band drift, the concentrated-holding
    wash-sale window, days-to-print, and only-new headlines (seen-cache delta).

    Read-only observation. Designed to be cron-able later — v1 is on-demand.
    """
    try:
        d = _svc().watch(mark_seen=not no_mark, news_limit=limit, ticker_news=not no_ticker_news)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(d.model_dump_json())
        return

    console.print(f"[bold]watch · {d.as_of}[/bold]")
    if d.day_moves:
        console.print("\n[bold]day moves[/bold]")
        for p in d.day_moves:
            arrow = "▲" if (p.day_change_pct or 0) >= 0 else "▼"
            console.print(
                f"  {p.symbol:<6} {arrow} {_signed(p.day_change_pct)}   "
                f"{_signed(p.day_gl, money=True)}  [dim]@ ${p.price:,.2f}[/dim]"
            )
    if d.watchlist_moves:
        console.print("\n[bold]watchlist[/bold] [dim](non-held — graduated research candidates)[/dim]")
        for m in d.watchlist_moves:
            if m.available and m.price is not None:
                arrow = "▲" if (m.day_change_pct or 0) >= 0 else "▼"
                console.print(
                    f"  {m.symbol:<6} {arrow} {_signed(m.day_change_pct)}  [dim]@ ${m.price:,.2f}"
                    f"{' · ' + m.note if m.note else ''}[/dim]"
                )
            else:
                console.print(
                    f"  {m.symbol:<6} [dim]— no IEX feed (OTC/ADR or fund) · news-covered below"
                    f"{' · ' + m.note if m.note else ''}[/dim]"
                )
    if d.drift:
        console.print("\n[bold]rebalance bands[/bold] [dim](illustrative — set your own targets)[/dim]")
        for f in d.drift:
            color = "yellow" if f.status != "in-band" else "green"
            console.print(
                f"  {f.symbol:<6} {f.pct_of_account:.1f}% of account  "
                f"band {f.band_min:.0f}–{f.band_max:.0f}%  [{color}]{f.status}[/{color}]"
            )
    if d.wash_sale:
        w = d.wash_sale
        color = "red" if w.today_poisoned else "green"
        state = "POISONED" if w.today_poisoned else "clean"
        console.print(f"\n[bold]Wash-sale window[/bold]  [{color}]{state}[/{color}] — {w.reason}")
        window_end = w.next_clean_end or "open-ended (no later known vest)"
        console.print(f"  next clean window: {w.next_clean_start} → {window_end}")
        if w.harvestable_loss is not None and w.harvestable_loss < 0:
            verb = "harvestable now" if not w.today_poisoned else "harvestable when clean"
            console.print(
                f"  [yellow]TLH inventory: {_money_short(w.harvestable_loss)} across "
                f"{w.harvestable_shares:,.0f} sh ({verb})[/yellow]"
            )
        console.print(f"  [dim]{w.note}[/dim]")
    if d.prints:
        console.print("\n[bold]days to print[/bold]")
        for pc in d.prints:
            days = (f"{pc.days_out}d out" if pc.days_out is not None else "?").ljust(8)
            if pc.days_out is not None and pc.days_out <= 3:  # print-soon = caution
                days = f"[yellow]{days}[/yellow]"
            console.print(f"  {pc.symbol:<6} {days} [dim]{pc.estimate}[/dim]")
    console.print(f"\n[bold]fresh headlines[/bold] ({len(d.fresh_news)} new since last watch)")
    for h in d.fresh_news:
        console.print(f"  {h.symbol:<6} {h.published or '—':<17} {h.title}", markup=False)
    for n in d.notes:
        console.print(f"[dim]  • {n}[/dim]")


def gates(
    horizon: int = typer.Option(30, "--horizon", help="Days ahead to include"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the gates contract — the Plans-tab board's source"
    ),
) -> None:
    """The plan gates — upcoming held-name prints + macro-calendar events + the concentrated
    holding's RSU vests inside the horizon, imminent-first. The fast structured twin of pulse's
    print_soon/macro_soon flags (no quotes, no wire): the Plans tab's countdown board reads
    this; the terminal render is the same calendar at a glance. Confirmed print dates say so;
    estimates are labeled (est)."""
    try:
        board = _svc(feed).gates(horizon_days=horizon)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(board.model_dump_json())
        return

    if not board.gates:
        console.print(f"[dim]no gates inside {board.horizon_days}d — clear runway[/dim]")
        return
    t = Table(title=f"Plan gates — next {board.horizon_days}d (as of {board.as_of})")
    t.add_column("in", justify="right")
    t.add_column("date")
    t.add_column("kind")
    t.add_column("gate")
    for g in board.gates:
        when = "TODAY" if g.days == 0 else f"{g.days}d"
        mark = "" if g.confirmed else " [dim](est)[/dim]"
        t.add_row(when, g.date or "—", g.kind, f"{g.label}{mark}")
    console.print(t)
    for n in board.notes:
        console.print(f"[dim]  • {n}[/dim]")


def trap_map(
    days: int = typer.Option(90, "--days", help="Price-history lookback for the support shelves"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the ladder contract — the dashboard/viz source"
    ),
) -> None:
    """The trap-map (the GTC slate as price ladders): per symbol, the live price + every resting
    rung (distance-to-fill, committed value) + bars-derived support shelves.

    The strategy surface for the staged-entry discipline — which knives the resting rungs are
    positioned to catch. Read-only observation of the portfolio.yaml `open_orders:` ledger; the
    harness never places or cancels an order.
    """
    try:
        tm = _svc(feed).trap_map(days=days)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(tm.model_dump_json())
        return

    if not tm.symbols:
        console.print("[dim]no resting orders — the slate is empty[/dim]")
        return
    console.print(
        f"\n[bold]TRAP MAP[/bold] — {len(tm.symbols)} laddered symbol(s), "
        f"[bold]{_money_short(tm.committed)}[/bold] committed by resting buys · as of {tm.as_of}\n"
    )
    for lad in tm.symbols:
        head = f"[bold]{lad.symbol}[/bold]"
        if lad.price is not None:
            head += f" @ ${lad.price:,.2f}  {_signed(lad.day_change_pct)}"
        else:
            head += "  [dim](unquotable — rungs shown without distance)[/dim]"
        console.print(head)
        t = Table(show_header=True)
        for col in ("Rung", "Qty", "Limit", "Value", "To fill", "Expires", "Note"):
            t.add_column(col)
        for r in lad.rungs:
            dist = f"{r.distance_pct:+.1f}%" if r.distance_pct is not None else "—"
            t.add_row(
                r.side.upper(), f"{r.qty:g}", f"${r.limit:,.2f}", _money_short(r.value),
                dist, r.expires or "—", r.note or "",
            )
        console.print(t)
        if lad.supports:
            shelves = " · ".join(
                f"${s.level:,.2f} (×{s.touches})" for s in lad.supports[:4]
            )
            console.print(f"  [dim]support shelves: {shelves}[/dim]")
        console.print()
    for n in tm.notes:
        console.print(f"[dim]  • {n}[/dim]")


def viz(
    diagram: str = typer.Argument(..., help="Diagram type: pie | treemap | sankey (+ all travel types)"),
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Vault path under tracker/ — SVG written to {dest}/visuals/ "
        "(e.g. 'finance/plans/concentration-unwind')",
    ),
    data_file: str = typer.Option(..., "--data", help="Path to the diagram's JSON data"),
    name: str = typer.Option(..., "--name", help="Output file stem"),
    theme: str = typer.Option(
        "light", "--theme", help="Render theme: light (default) | instrument (the bus-app console palette)"
    ),
) -> None:
    """Render a D3 diagram into the finance corpus ({dest}/visuals/{name}.svg) + print the embed.

    Reuses the shared render engine — finance-coded types (pie/treemap/sankey) plus every travel type.
    Read-only artifact; nothing here trades.
    """
    if diagram not in KNOWN_TYPES:
        _fail(f"unknown diagram type {diagram!r}; known: {', '.join(KNOWN_TYPES)}")
        raise typer.Exit(code=1)
    try:
        data = json.loads(Path(data_file).read_text())
    except (OSError, ValueError) as e:
        _fail(f"could not read --data {data_file!r}: {e}")
        raise typer.Exit(code=1) from e
    tracker = get_settings().tracker_path
    out = tracker / dest / "visuals" / f"{name}.svg"
    try:
        written = render_diagram(diagram, data, out, theme=theme)
    except VizError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    rel = written.resolve().relative_to(tracker.resolve())
    console.print(f"wrote {written}")
    console.print("\nObsidian embed (paste into the doc):")
    # markup=False: Rich otherwise parses [[...]] as markup and mangles the embed (caught by the
    # career-lane test; travel's viz already had this right — keep the wikilink literal).
    console.print(f"![[{rel.as_posix()}|640]]", markup=False)


def mcp() -> None:
    """Launch the MCP server (stdio)."""
    from harness.finance.mcp_server import main as mcp_main

    mcp_main()
