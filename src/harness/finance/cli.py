"""Typer CLI adapter (Bash-callable / cron-able). Thin wrapper over FinanceService.

`app` is a domain-noun command group (`finance <verb>`) — standalone at its own root today, a
zero-refactor `add_typer()` mount under a future unified harness CLI (the noun-group sub-commands
pattern). See the operator skill.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from rich.console import Console
from rich.table import Table

from harness.finance.config.settings import get_settings
from harness.finance.providers.base import ProviderError
from harness.finance.service import FinanceService

if TYPE_CHECKING:
    from harness.finance.watch import PulseReport

# the shared D3 render engine (promoted to harness.viz; both lanes import it here)
from harness.finance.levels import support_levels
from harness.packs import PackGroup
from harness.viz import KNOWN_TYPES, VizError, render_diagram

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn finance networth --pack …)
    add_completion=False,
    help="Read-only market-data hands for the harness: live quotes + corpus-aware portfolio "
    "observation. Observation only — no trading.",
)
console = Console()


def _svc(feed: str = "iex") -> FinanceService:
    return FinanceService(feed=feed)


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
    if rep.quiet and not catalysts:
        return ""
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

        drafts = events_from_pulse(rep, ref_dirs) + events_from_catalysts(catalysts, ref_dirs)
        results = BusService().publish_many(drafts)
        published = sum(1 for r in results if r.status == "published")
        return f"bus: {published} published, {len(results) - published} dup ({len(catalysts)} catalyst)"
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
    from datetime import date, datetime

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


def _signed(v: float | None, money: bool = False) -> str:
    """Signed value, SIGN-COLORED (green +, red −, dim flat). Threaded through quote / positions /
    networth / watch / research / bars — one helper, consistent valence everywhere. Rich auto-strips
    color on non-TTY/piped output, so `--json` and the bus-app's subprocess reads stay clean."""
    if v is None:
        return "—"
    body = f"${abs(v):,.2f}" if money else f"{abs(v):.2f}%"
    text = f"{'+' if v >= 0 else '-'}{body}"
    style = "green" if v > 0 else "red" if v < 0 else "dim"
    return f"[{style}]{text}[/{style}]"


def _money_short(v: float) -> str:
    """Compact USD for big reported figures: 1.23B / 456.7M / 12,345 (signed)."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:,.0f}"


def _trend_money(value: float | None, prior: float | None) -> str:
    """`_money_short` colored by trend vs the prior same-type period (green=grew, red=shrank, dim=flat
    / no prior). For income-statement concepts (revenue/income/operating income) growth reads as good —
    lets the eye scan a column for the trajectory before reading a single number."""
    if value is None:
        return "—"
    cell = _money_short(value)
    if prior is None or value == prior:
        return cell
    style = "green" if value > prior else "red"
    return f"[{style}]{cell}[/{style}]"


@app.command()
def bars(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    days: int = typer.Option(90, "--days", help="Lookback window (calendar days)"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
    as_viz: bool = typer.Option(
        False, "--viz", help="Emit the viz `line` data contract (closes) — plugs straight into "
        "the watchman LineChart as a dashboard widget / live viz"
    ),
    wing: int = typer.Option(2, "--wing", help="Swing-low window half-width (bars)"),
    tol: float = typer.Option(1.5, "--tol", help="Level clustering tolerance (%)"),
) -> None:
    """Historical daily bars + DETERMINISTIC support levels (clustered swing lows).

    The trap-setting observation surface: levels are descriptions of past price behavior with
    touch counts and recency — never predictions or recommendations (read-only doctrine).
    """
    sym = symbol.upper()
    start = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    try:
        bar_list = _svc(feed).history(sym, start=start)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if not bar_list:
        if as_viz:
            # Offline / keyless (the static-quote demo path has no historical bars): emit a VALID empty
            # line contract so the position-chart widget degrades gracefully — an empty chart + a note,
            # never a red "exited 1" error. (The Core quote-based widgets render fully from the fixture;
            # only intraday history needs a live key.)
            console.print_json(json.dumps({
                "title": f"{sym} — price history",
                "subtitle": "historical bars need a live market-data key (set ALPACA_API_KEY_ID)",
                "yPrefix": "$",
                "series": [{"label": sym, "points": []}],
            }))
            return
        _fail(f"no bars returned for {sym} (unknown symbol / not on feed?)")
        raise typer.Exit(code=1)
    levels = support_levels(bar_list, wing=wing, tol_pct=tol)
    last = bar_list[-1]

    if as_viz:
        data = {
            "title": f"{sym} — {days}d closes",
            "subtitle": f"daily bars (split-adjusted, {feed}) · last ${last.c:,.2f} ({last.t[:10]})",
            "yPrefix": "$",
            "series": [
                {"label": sym, "points": [{"x": b.t[:10], "y": b.c} for b in bar_list]}
            ],
            # reference lines: clustered swing-low supports drawn ON the chart (optional
            # contract field — the static engine and older renderers ignore it)
            "levels": [
                {"label": f"${lv.level:,.2f} ×{lv.touches}", "y": lv.level} for lv in levels
            ],
        }
        console.print_json(json.dumps(data))
        return
    if as_json:
        console.print_json(json.dumps({
            "symbol": sym,
            "days": days,
            "last_close": last.c,
            "as_of": last.t[:10],
            "levels": [lv.model_dump() for lv in levels],
            "bars": [b.model_dump() for b in bar_list],
        }))
        return

    table = Table(title=f"{sym} — last 12 of {len(bar_list)} daily bars ({days}d lookback)")
    for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
        table.add_column(col)
    for b in bar_list[-12:]:
        table.add_row(b.t[:10], f"{b.o:,.2f}", f"{b.h:,.2f}", f"{b.low:,.2f}",
                      f"{b.c:,.2f}", f"{b.v:,}")
    console.print(table)

    lt = Table(title=f"Support levels — clustered swing lows (wing={wing}, tol={tol}%)")
    for col in ("Level", "Touches", "Last touch", "vs close"):
        lt.add_column(col)
    for lv in levels:
        lt.add_row(f"${lv.level:,.2f}", str(lv.touches), lv.last_touch,
                   _signed(lv.distance_pct))
    console.print(lt)
    console.print(
        f"[dim]  • last close ${last.c:,.2f} ({last.t[:10]}) · levels describe PAST swing lows "
        "(touches + recency = evidence), not predictions. Order placement is the user's.[/dim]"
    )


@app.command()
def correlate(
    symbols: list[str] = typer.Argument(..., help="Symbols; the FIRST is the focal name"),
    days: int = typer.Option(120, "--days", help="Lookback window (calendar days)"),
    feed: str = typer.Option("iex", "--feed"),
    factor: str = typer.Option(
        None, "--factor", help="Comma-separated symbols → an equal-weight FACTOR basket "
        "(e.g. an illustrative factor basket 'NVDA,AMD,AVGO') → each name's corr + beta to it + the focal's "
        "divergence days",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Daily-return correlation across holdings — the "is this name a real diversifier?" surface.

    Pulls daily bars, aligns on common dates, computes the Pearson correlation matrix + annualized vol.
    With --factor, builds an equal-weight basket and reports each name's correlation + beta to it, plus
    the days the FOCAL (first) name moved most INDEPENDENTLY of the factor. Read-only: the math is
    deterministic; the interpretation (is it ballast?) is yours, for the research.
    """
    syms = [s.upper() for s in symbols]
    fac = [s.strip().upper() for s in factor.split(",") if s.strip()] if factor else None
    try:
        rep = _svc(feed).correlate(syms, days=days, factor=fac)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(rep.model_dump_json())
        return
    if rep.n_obs < 3:
        _fail("insufficient overlapping history — " + "; ".join(rep.notes or ["no data"]))
        raise typer.Exit(code=1)

    def _cstyle(v: float) -> str:  # warm = correlated (less diversifying), cool = decorrelated
        return "red" if v >= 0.5 else "green" if v <= 0.2 else "yellow"

    console.print(
        f"[bold]correlate[/bold] · {rep.n_obs} trading days ({rep.start} → {rep.end}) · daily-return Pearson"
    )
    mt = Table(title="Correlation matrix (daily returns)")
    mt.add_column("")
    for s in rep.symbols:
        mt.add_column(s, justify="right")
    for i, a in enumerate(rep.symbols):
        row = [a]
        for j in range(len(rep.symbols)):
            v = rep.matrix[i][j]
            row.append(f"{v:.2f}" if i == j else f"[{_cstyle(v)}]{v:.2f}[/{_cstyle(v)}]")
        mt.add_row(*row)
    console.print(mt)

    if rep.factor and rep.factor_corr and rep.factor_beta:
        ft = Table(title=f"vs FACTOR (equal-weight {'+'.join(rep.factor)}) — corr · beta · ann.vol")
        for col in ("Symbol", "corr→factor", "beta→factor", "ann.vol %"):
            ft.add_column(col, justify="right")
        for s in rep.symbols:
            c = rep.factor_corr[s]
            ft.add_row(s, f"[{_cstyle(c)}]{c:.2f}[/{_cstyle(c)}]", f"{rep.factor_beta[s]:.2f}",
                       f"{rep.vol_annual[s]:.1f}")
        console.print(ft)

    if rep.divergence_days:
        dt = Table(title=f"{rep.symbols[0]} vs factor — biggest divergence days (it moved on its own)")
        for col in ("Date", f"{rep.symbols[0]} %", "factor %", "gap %"):
            dt.add_column(col, justify="right")
        for d in rep.divergence_days:
            dt.add_row(d.date, _signed(d.focal_ret_pct), _signed(d.factor_ret_pct), _signed(d.gap_pct))
        console.print(dt)
    if rep.notes:
        console.print("[dim]  • " + " · ".join(rep.notes) + "[/dim]")


@app.command(name="unwind")
def unwind(
    symbol: str | None = typer.Option(
        None, "--symbol", help="Concentrated holding to analyze (needs `lots:` in portfolio.yaml; "
        "defaults to the lotted holding)"
    ),
    days: int = typer.Option(120, "--days", help="Price-history lookback for bars + support levels"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the full unwind contract — the dashboard source (one source, "
        "two renderers: static SVG + the live visx widget cluster)"
    ),
) -> None:
    """Concentration-unwind sell-planning contract: per-lot LIVE gain/loss + wash-sale
    harvestability, the vest calendar, wash windows, the holding's price + support levels.

    The lot is the atom — gains never wash (sellable anytime), losses are the TLH inventory
    (wash-gated). Read-only observation; order placement is always the user's.
    """
    try:
        report = _svc(feed).unwind(symbol=symbol, days=days)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(report.model_dump_json())
        return

    p = report.position
    console.print(
        f"\n[bold]{report.symbol}[/bold] @ ${report.price:,.2f}  "
        f"{_signed(report.day_change_pct)}  ·  position {p.shares:,.0f} sh  "
        f"mkt {_money_short(p.market_value)}  unreal {_signed(p.unrealized_gl, money=True)} "
        f"({_signed(p.unrealized_gl_pct)})\n"
    )

    lt = Table(title="Tax lots — live gain/loss at the current price (the atom)")
    for col in ("Acquired", "Qty", "Unit cost", "Mkt value", "Unreal G/L", "%", "Class", "Harvest"):
        lt.add_column(col)
    for lot in report.lots:
        cls = "[green]gain[/green]" if lot.klass == "gain" else "[red]loss[/red]"
        harvest = (
            "[yellow]✓ now[/yellow]"
            if lot.harvestable_now
            else ("wash-gated" if lot.klass == "loss" else "—")
        )
        lt.add_row(
            lot.acquired, f"{lot.qty:,.0f}", f"${lot.unit_cost:,.3f}",
            _money_short(lot.market_value), _signed(lot.unrealized_gl, money=True),
            _signed(lot.unrealized_gl_pct), cls, harvest,
        )
    console.print(lt)

    t = report.tlh
    console.print(
        f"\n  [bold]TLH split[/bold] @ ${report.price:,.2f} — "
        f"[red]loss inventory {_money_short(t.harvestable_loss)} across {t.harvestable_shares:,.0f} sh[/red] "
        f"(wash-gated) · [green]gain lots {_money_short(t.gain_lot_value)} / "
        f"{t.gain_lot_shares:,.0f} sh[/green] (sellable anytime)"
    )
    w = report.wash_sale
    if w.today_poisoned:
        console.print(
            f"  [yellow]wash: POISONED today ({w.reason}); next clean window "
            f"{w.next_clean_start} → {w.next_clean_end or '—'}[/yellow]"
        )
    else:
        console.print(f"  [green]wash: clean today[/green] ({w.reason})")

    vt = Table(title="RSU vest calendar — timeline markers (units × price)")
    for col in ("Date", "Units", "Est $", "Days away", "Status"):
        vt.add_column(col)
    for v in report.vests:
        vt.add_row(
            v.date, f"{v.units:,}", _money_short(v.est_value),
            f"{v.days_away:+d}", "upcoming" if v.future else "vested",
        )
    console.print(vt)
    console.print(
        "[dim]  • Read-only: lot-level tax state, not advice. Lots are manually synced "
        "(broker vest feed); gain/loss + harvestability derive from the live price.[/dim]"
    )


@app.command()
def quote(
    symbols: list[str] = typer.Argument(..., help="One or more tickers, e.g. AAPL MSFT GOOGL"),
    feed: str = typer.Option("iex", "--feed", help="iex (free real-time) | sip | delayed_sip"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Live quote(s) for one or more symbols (price + day change vs. previous close)."""
    try:
        quotes = _svc(feed).quote([s.upper() for s in symbols])
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps([q.model_dump() for q in quotes]))
        return
    table = Table(title="Quotes")
    for col in ("Symbol", "Price", "Day Δ", "Day %", "Prev close", "Feed", "Note"):
        table.add_column(col)
    for q in quotes:
        table.add_row(
            q.symbol,
            f"${q.price:,.2f}" if q.price is not None else "—",
            _signed(q.day_change, money=True),
            _signed(q.day_change_pct),
            f"${q.prev_close:,.2f}" if q.prev_close is not None else "—",
            q.feed,
            "" if q.available else (q.note or "unavailable"),
        )
    console.print(table)


@app.command()
def market(
    feed: str = typer.Option("iex", "--feed", help="iex (free real-time) | sip | delayed_sip"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Bird's-eye market read — indices + breadth, the 11 SPDR sectors, semis, Mag7 dispersion.

    A point-in-time regime snapshot (one Alpaca snapshots call). Deterministic gather + computed
    breadth facts; the interpretive 'take' lives separately in finance/market/take.md (an agent
    writes it on request, so this never depends on a model call)."""
    try:
        ov = _svc(feed).market()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(ov.model_dump_json())
        return

    from harness.finance.models import MarketMover, MarketQuote

    def _grp(title: str, rows: list[MarketQuote], *, by_move: bool = False) -> Table:
        t = Table(title=title)
        for col in ("Symbol", "", "Price", "Day %", "Day Δ"):
            t.add_column(col)
        ordered = (
            sorted(rows, key=lambda r: r.day_change_pct or 0.0, reverse=True) if by_move else rows
        )
        for r in ordered:
            t.add_row(
                r.symbol,
                r.label,
                f"${r.price:,.2f}" if r.price is not None else "—",
                _signed(r.day_change_pct),
                _signed(r.day_change, money=True),
            )
        return t

    console.print(_grp("Indexes", ov.indices))
    console.print(_grp("Sectors", ov.sectors, by_move=True))
    console.print(_grp("Semis", ov.semis, by_move=True))
    console.print(_grp("Mega-cap (Mag7)", ov.megacap, by_move=True))

    b = ov.breadth
    spread = f"{b.megacap_spread_pct:.2f}pp" if b.megacap_spread_pct is not None else "—"
    console.print(
        f"\n[bold]Breadth[/bold]  sectors {b.sectors_advancing}▲/{b.sectors_declining}▼  ·  "
        f"equal-wt − cap (RSP−SPY) {_signed(b.equal_weight_minus_cap_pct)}  ·  "
        f"Mag7 avg {_signed(b.megacap_avg_pct)} (spread {spread})  ·  "
        f"semis avg {_signed(b.semis_avg_pct)}"
    )

    def _movers(ms: list[MarketMover]) -> str:
        return ", ".join(f"{m.symbol} {_signed(m.day_change_pct)}" for m in ms)

    console.print(f"[green]Leaders[/green]  {_movers(ov.leaders)}")
    console.print(f"[red]Laggards[/red] {_movers(ov.laggards)}")
    if ov.as_of:
        console.print(f"[dim]as of {ov.as_of} · feed {feed}[/dim]")
    for n in ov.notes:
        console.print(f"[dim]{n}[/dim]")


@app.command()
def fed(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Latest FOMC decision — statement text + target-rate range + vote, from federalreserve.gov
    (keyless, Fed-direct). The SEP/dot-plot is LINKED, not parsed (eyeball the dots). So a post-FOMC
    read is confirmed, not tape-inferred. The hawkish/dovish call is yours. READ-ONLY."""
    try:
        d = _svc().fed()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(d.model_dump_json())
        return

    console.print(f"[bold]{d.title}[/bold]" + (f"  [dim]{d.released}[/dim]" if d.released else ""))
    if d.target_rate:
        line = f"Target range: [bold]{d.target_rate}[/bold]"
        if d.vote:
            line += f"  ·  vote {d.vote}"
        console.print(line)
    if d.statement_text:
        console.print()
        console.print(d.statement_text)
    if d.sep_url:
        console.print(f"\n[dim]SEP / dot-plot (eyeball it): {d.sep_url}[/dim]")
    for n in d.notes:
        console.print(f"[dim]· {n}[/dim]")
    if d.statement_url:
        console.print(f"[dim]{d.statement_url}[/dim]")


@app.command()
def history(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str | None = typer.Option(None, "--end", help="YYYY-MM-DD (default: today)"),
    timeframe: str = typer.Option("1Day", "--timeframe", help="e.g. 1Day, 1Hour, 1Week"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Historical OHLCV bars (chart-able data) for a symbol."""
    try:
        bars = _svc(feed).history(symbol.upper(), start=start, end=end, timeframe=timeframe)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(json.dumps([b.model_dump(by_alias=True) for b in bars]))
        return
    table = Table(title=f"{symbol.upper()} — {timeframe} bars")
    for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
        table.add_column(col)
    for b in bars:
        table.add_row(
            b.t[:10], f"{b.o:,.2f}", f"{b.h:,.2f}", f"{b.low:,.2f}", f"{b.c:,.2f}", f"{b.v:,}"
        )
    console.print(table)
    console.print(f"[dim]{len(bars)} bars[/dim]")


@app.command()
def positions(
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Portfolio holdings (from the corpus seed) joined to live quotes — read-only observation."""
    try:
        snap = _svc(feed).positions()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(snap.model_dump_json())
        return
    table = Table(title="Positions — across brokerages")
    for col in ("Acct", "Symbol", "Type", "Shares", "Price", "Mkt value", "Unreal G/L", "%", "Day %"):
        table.add_column(col)
    positions = sorted(snap.positions, key=lambda p: (p.account, p.symbol))
    for p in positions:
        table.add_row(
            p.account,
            p.symbol,
            p.asset_type,
            f"{p.shares:g}",
            f"${p.price:,.2f}" if p.price is not None else "—",
            f"${p.market_value:,.2f}" if p.market_value is not None else "—",
            _signed(p.unrealized_gl, money=True),
            _signed(p.unrealized_gl_pct),
            _signed(p.day_change_pct),
        )
    console.print(table)

    # Per-brokerage quoted subtotals (the cross-brokerage view) + the all-brokerage total.
    accounts = sorted({p.account for p in positions})
    if len(accounts) > 1:
        for acct in accounts:
            acct_pos = [p for p in positions if p.account == acct]
            mv = sum(p.market_value or 0.0 for p in acct_pos)
            gl = sum(p.unrealized_gl or 0.0 for p in acct_pos)
            unreal = _signed(gl, money=True)
            console.print(f"[dim]  {acct}: value ${mv:,.2f}   unrealized {unreal}[/dim]")
    console.print(
        f"\n[bold]Net worth ${snap.net_worth:,.2f}[/bold]  "
        f"[dim](live ${snap.live_value:,.2f} + last-known ${snap.last_known_value:,.2f} + "
        f"static ${snap.static_value:,.2f})[/dim]   day {_signed(snap.quoted_day_gl, money=True)} (live)"
    )
    for p in positions:
        if p.note:
            console.print(f"[dim]  {p.symbol}: {p.note}[/dim]")
    for n in snap.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def networth(
    as_json: bool = typer.Option(False, "--json"),
    log: bool = typer.Option(False, "--log", help="Upsert today's total into finance/networth-history.json"),
) -> None:
    """Full-picture net worth across institutions (brokerage + retirement + cash)."""
    try:
        nw = _svc().networth()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(nw.model_dump_json())
        return
    table = Table(title="Net worth — full picture")
    for col in ("Account", "Value", "Basis", "As of"):
        table.add_column(col)
    for g in nw.groups:
        table.add_row(g.account, f"${g.value:,.2f}", g.valuation, g.as_of or "live")
    console.print(table)
    console.print(
        f"\n[bold]Net worth ${nw.total:,.2f}[/bold]  "
        f"[dim](live ${nw.live_value:,.2f} + last-known ${nw.last_known_value:,.2f} + "
        f"static ${nw.static_value:,.2f})[/dim]"
    )
    for n in nw.notes:
        console.print(f"[dim]  • {n}[/dim]")
    if log:
        _log_networth(nw.total)


def _render_derived(diagram: str, data: dict[str, object], dest: str, name: str) -> None:
    """Render a service-derived viz data dict to {dest}/visuals/{name}.svg (composes the viz engine).
    The `--write` path for the derived net-worth-portrait verbs — same render call the `viz` verb uses,
    so a derived treemap/pie lands exactly where the hand-authored one did (the plan-doc embeds keep
    working, now backed by always-current data)."""
    tracker = get_settings().tracker_path
    out = tracker / dest / "visuals" / f"{name}.svg"
    try:
        written = render_diagram(diagram, data, out)
    except VizError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    rel = written.resolve().relative_to(tracker.resolve())
    console.print(f"wrote {written}")
    console.print(f"![[{rel.as_posix()}|640]]", markup=False)


@app.command()
def concentration(
    as_json: bool = typer.Option(False, "--json"),
    write: bool = typer.Option(False, "--write", help="Render the treemap SVG to {dest}/visuals/{name}.svg"),
    dest: str = typer.Option(
        "finance/plans/concentration-unwind", "--dest", help="Vault path under the corpus for --write"
    ),
    name: str = typer.Option("concentration", "--name", help="Output SVG stem for --write"),
) -> None:
    """LIVE net-worth concentration treemap — DERIVED from portfolio.yaml + live quotes.

    Replaces the hand-authored concentration.json: holdings grouped by the semantic risk taxonomy
    (concentrated single-stock + single-theme fund isolated, then diversified-brokerage / retirement /
    cash). Never stale — a fresh portfolio.yaml sync + this command reproduces it current. `--json`
    emits the `treemap` contract; `--write` renders the SVG. Read-only observation.
    """
    try:
        data = _svc().concentration_treemap()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if write:
        _render_derived("treemap", data, dest, name)
        return
    if as_json:
        console.print_json(json.dumps(data))
        return
    nodes = cast("list[dict[str, object]]", data["nodes"])
    table = Table(title="Concentration — LIVE (treemap data)")
    for col in ("Holding", "Value", "Group"):
        table.add_column(col)
    for n in nodes:
        table.add_row(str(n["label"]), f"${float(cast('float', n['value'])):,.2f}", str(n["group"]))
    console.print(table)


@app.command()
def allocation(
    as_json: bool = typer.Option(False, "--json"),
    write: bool = typer.Option(False, "--write", help="Render the pie SVG to {dest}/visuals/{name}.svg"),
    dest: str = typer.Option(
        "finance/plans/concentration-unwind", "--dest", help="Vault path under the corpus for --write"
    ),
    name: str = typer.Option("allocation", "--name", help="Output SVG stem for --write"),
) -> None:
    """Current → target allocation pies — Current DERIVED live, Target from config.

    Replaces the hand-authored allocation.json: the Current pie is the liquid pool (every non-retirement
    holding, each its own slice) computed live from portfolio.yaml; the Target pie is the ratified
    strategic plan read from `allocation_target:` (config-stored judgment, synced when the strategy
    changes — not per-fill). `--json` emits the `pie` contract; `--write` renders the SVG.
    """
    try:
        data = _svc().allocation_pie()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if write:
        _render_derived("pie", data, dest, name)
        return
    if as_json:
        console.print_json(json.dumps(data))
        return
    for pie in cast("list[dict[str, object]]", data["pies"]):
        slices = cast("list[dict[str, object]]", pie["slices"])
        total = sum(float(cast("float", s["value"])) for s in slices) or 1.0
        table = Table(title=f"{pie['label']} — {pie.get('caption', '')}")
        for col in ("Slice", "Value", "%"):
            table.add_column(col)
        for s in sorted(slices, key=lambda x: -float(cast("float", x["value"]))):
            v = float(cast("float", s["value"]))
            table.add_row(str(s["label"]), f"${v:,.0f}", f"{v / total * 100:.1f}%")
        console.print(table)


@app.command(name="fund-proxy")
def fund_proxy(
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Estimate a mutual fund's EOD direction from a live proxy basket (it doesn't price intraday).
    The fund + its proxies come from the `fund_proxy:` block in portfolio.yaml."""
    try:
        est = _svc(feed).fund_proxy()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(est.model_dump_json())
        return
    table = Table(title=f"{est.fund} proxy basket")
    for col in ("Symbol", "Price", "Prev close", "Move %", "Status"):
        table.add_column(col)
    for c in est.components:
        table.add_row(
            c.symbol,
            f"${c.price:,.2f}" if c.price is not None else "—",
            f"${c.prev_close:,.2f}" if c.prev_close is not None else "—",
            _signed(c.move_pct),
            "ok" if c.available else "unavailable",
        )
    console.print(table)
    headline = _signed(est.estimate_pct) if est.estimate_pct is not None else "n/a"
    console.print(
        f"\n[bold]Rough EOD estimate[/bold]: {headline}  "
        f"[dim](equal-weight mean of {est.available_count} available names)[/dim]"
    )
    for n in est.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def resolve(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve a ticker → SEC CIK (the permanent filer ID every EDGAR call needs) via the bundled
    company_tickers.json. Misses (ETFs/mutual funds/ADRs aren't US filers) are reported honestly."""
    look = _svc().resolve_cik(symbol.upper())
    if as_json:
        console.print_json(look.model_dump_json())
        return
    if look.found:
        console.print(f"[green]{look.symbol}[/green] → CIK [bold]{look.cik}[/bold]  [dim]{look.title}[/dim]")
    else:
        console.print(
            f"[yellow]{look.symbol}[/yellow]: not in SEC's ticker→CIK map "
            "(ETFs/mutual funds/ADRs aren't US XBRL filers, or unknown ticker)."
        )


@app.command()
def fundamentals(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL (must be a US SEC filer)"),
    cik: str | None = typer.Option(None, "--cik", help="Override: use this CIK directly (skip the map)"),
    recent: int = typer.Option(6, "--recent", help="Facts to show per concept (newest-first)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Reported GAAP/XBRL financials from SEC EDGAR (keyless) — revenue, net income, etc. per
    fiscal period. Read-only; figures LAG real-time (newest = most recent 10-Q/10-K)."""
    try:
        fun = _svc().fundamentals(symbol.upper(), cik=cik, recent=recent)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(fun.model_dump_json())
        return
    console.print(f"[bold]{fun.entity_name or fun.symbol}[/bold]  [dim](CIK {fun.cik})[/dim]")
    for c in fun.concepts:
        if not c.facts:
            console.print(f"[dim]  {c.label}: {c.note or 'no data'}[/dim]")
            continue
        table = Table(title=f"{c.label}  [dim]({c.tag})[/dim]")
        for col in ("Period", "Type", "Value", "Form", "Filed"):
            table.add_column(col)
        for i, f in enumerate(c.facts):
            period = f"{f.fiscal_year or ''} {f.fiscal_period or ''}".strip() or (f.end or "—")
            # prior = next-older fact of the SAME period type (quarter-vs-quarter, annual-vs-annual)
            prior = next((g.value for g in c.facts[i + 1:] if g.period_type == f.period_type), None)
            table.add_row(
                period, f.period_type, _trend_money(f.value, prior), f.form, f.filed or "—"
            )
        console.print(table)
    console.print(
        "[dim]  value color = trend vs the prior same-type period (green ↑ / red ↓)[/dim]"
    )
    for n in fun.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def multiples(
    symbol: str = typer.Argument(..., help="Ticker, e.g. COST (must be a US SEC filer)"),
    cik: str | None = typer.Option(None, "--cik", help="Override: use this CIK directly (skip the map)"),
    recent: int = typer.Option(
        8, "--recent", help="Facts pulled per concept before TTM assembly (≥8 for 4 clean quarters)"
    ),
    source: str = typer.Option(
        "auto", "--source", help="auto|fmp|edgar — auto prefers FMP (clean TTM ratios) if keyed, else EDGAR"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Valuation multiples (EV/EBITDA, P/E, P/S, + PEG on FMP) — read-only.

    Two sources: **FMP** (Financial Modeling Prep PRE-COMPUTED TTM ratios — sidesteps the Q4-in-10-K
    XBRL-assembly trap + covers foreign filers; needs FMP_API_KEY) and **EDGAR** (keyless SEC figures
    + a LIVE price, with the full auditable component breakdown). `--source auto` (default) prefers
    FMP when keyed, else EDGAR. GAAP-honest either way: unprofitable reads "N/M", a missing input
    reads "unavailable" — never a fabricated number.
    """
    try:
        m = _svc().multiples(symbol.upper(), cik=cik, recent=recent, source=source)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(m.model_dump_json())
        return

    price_str = f"${m.price:,.2f}" if m.price is not None else "[red]no live price[/red]"
    src = "FMP (pre-computed TTM)" if m.source == "fmp" else "EDGAR + live price"
    cik_str = f"  [dim]CIK {m.cik}[/dim]" if m.cik else ""
    console.print(
        f"\n[bold]{m.entity_name or m.symbol}[/bold] ({m.symbol}){cik_str}  ·  {price_str}  "
        f"·  [dim]source: {src}[/dim]"
    )

    def _mult(v: float | str | None) -> str:
        if isinstance(v, str):
            color = "yellow" if v == "N/M" else "dim"
            return f"[{color}]{v}[/{color}]"
        return f"[bold]{v:.1f}x[/bold]" if v is not None else "[dim]—[/dim]"

    peg_str = f"   ·   PEG {_mult(m.peg)}" if m.peg is not None else ""
    console.print(
        f"  EV/EBITDA {_mult(m.ev_ebitda)}   ·   P/E {_mult(m.pe)}   ·   P/S {_mult(m.ps)}{peg_str}\n"
    )

    def _fmt(c: object) -> str:
        comp = c  # MultiplesComponent
        if comp.value is None:  # type: ignore[attr-defined]
            return "[dim]unavailable[/dim]"
        v = comp.value  # type: ignore[attr-defined]
        if comp.label == "Shares outstanding":  # type: ignore[attr-defined]
            return f"{v / 1e6:,.1f}M sh" if v >= 1e6 else f"{v:,.0f} sh"
        if comp.label == "Live price":  # type: ignore[attr-defined]
            return f"${v:,.2f}"
        return _money_short(v)

    table = Table(title="Components — every figure surfaced so the math audits")
    for col in ("Component", "Value", "Basis", "XBRL tag / note"):
        table.add_column(col)
    for c in m.components:
        src = c.tag or c.note or ""
        table.add_row(c.label, _fmt(c), c.period, src if len(src) <= 70 else src[:67] + "…")
    console.print(table)
    # surface gap / fallback notes from the components themselves (the honesty trail)
    for c in m.components:
        if c.note and c.tag:  # a resolved component carrying a caveat (e.g. the TTM gap)
            console.print(f"[dim]  {c.label}: {c.note}[/dim]")
    for n in m.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def compare(
    symbols: list[str] = typer.Argument(..., help="Two or more tickers, e.g. AAPL MSFT GOOGL"),
    recent: int = typer.Option(8, "--recent", help="Facts pulled per concept before TTM assembly"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Side-by-side valuation + price + values-screen for a pick-set (the Compare-tab data).

    Composes existing surfaces: a batch live quote + per-symbol EDGAR multiples (graceful on a failed
    resolve) + the corpus values-screen. The interpretive comparison (which name wins, and why) is an
    agent-written doc — not computed here. Reported figures LAG; only price/day move are live.
    Read-only."""
    try:
        rep = _svc().compare([s.upper() for s in symbols], recent=recent)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(rep.model_dump_json())
        return

    def _mult(v: float | str | None) -> str:
        if isinstance(v, str):
            return f"[yellow]{v}[/yellow]" if v == "N/M" else f"[dim]{v}[/dim]"
        return f"{v:.1f}x" if v is not None else "[dim]—[/dim]"

    def _scr(status: str, cat: str | None) -> str:
        if status == "clean":
            return "[green]clean[/green]"
        if status == "excluded":
            return f"[red]excluded[/red] [dim]({cat})[/dim]" if cat else "[red]excluded[/red]"
        return "[dim]—[/dim]"

    table = Table(title="Compare — valuation × price × screen (EDGAR TTM + live price)")
    for col in ("Symbol", "Price", "Day %", "P/S", "P/E", "EV/EBITDA", "Mkt cap", "Screen"):
        table.add_column(col)
    for r in rep.rows:
        table.add_row(
            r.symbol,
            f"${r.price:,.2f}" if r.price is not None else "[dim]—[/dim]",
            _signed(r.day_change_pct),
            _mult(r.ps),
            _mult(r.pe),
            _mult(r.ev_ebitda),
            _money_short(r.market_cap) if r.market_cap is not None else "[dim]—[/dim]",
            _scr(r.screen, r.screen_category),
        )
    console.print(table)
    for r in rep.rows:
        if r.note:
            console.print(f"[dim]  {r.symbol}: {r.note}[/dim]")
    for n in rep.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
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


@app.command()
def ratings(
    symbol: str = typer.Argument(..., help="Ticker, e.g. NVDA"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Sell-side analyst consensus (Yahoo, keyless): mean/high/low price target + recommendation +
    analyst count.

    INFORMATION, not a verdict — sell-side targets skew bullish, herd, and lag price (~1/3 hit at
    12mo). Read the consensus + the range + the count; never bet on a single target.
    """
    sym = symbol.upper()
    try:
        r = _svc().ratings(sym)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(r.model_dump_json())
        return

    rec = (r.recommendation_key or "—").replace("_", " ").upper()
    rec_color = (
        "green" if r.recommendation_key in {"strong_buy", "buy"}
        else "red" if r.recommendation_key in {"sell", "strong_sell"}
        else "yellow"
    )
    mean_str = f" ({r.recommendation_mean:.2f}/5)" if r.recommendation_mean is not None else ""
    n = r.num_analysts if r.num_analysts is not None else "?"
    cur_str = f"  ·  current ${r.current_price:,.2f}" if r.current_price else ""
    console.print(
        f"\n[bold]{sym}[/bold] analyst consensus  [{rec_color}]{rec}[/{rec_color}]{mean_str}  · "
        f"{n} analysts{cur_str}\n"
    )
    t = Table(show_header=True)
    for col in ("Price target", "Value", "vs current"):
        t.add_column(col)
    cur = r.current_price
    def _vs(v: float | None) -> str:
        return _signed(round((v - cur) / cur * 100.0, 2)) if (v and cur) else "—"
    t.add_row("Mean", f"${r.target_mean:,.2f}" if r.target_mean else "—", _vs(r.target_mean))
    t.add_row("Median", f"${r.target_median:,.2f}" if r.target_median else "—", _vs(r.target_median))
    t.add_row("High", f"${r.target_high:,.2f}" if r.target_high else "—", _vs(r.target_high))
    t.add_row("Low", f"${r.target_low:,.2f}" if r.target_low else "—", _vs(r.target_low))
    console.print(t)
    console.print(
        "[dim]  • Sell-side consensus (Yahoo) — INFORMATION, not a verdict. Targets skew bullish, "
        "herd, and lag price (~1/3 hit at 12mo). Read the spread + count, not a single number.[/dim]"
    )


@app.command()
def screen(
    symbol: str = typer.Argument(..., help="Ticker to check against the values screen"),
) -> None:
    """Check a symbol against the values screen (corpus-only; no network)."""
    r = _svc().screen(symbol)
    color = "red" if r.status == "excluded" else "green"
    console.print(f"[{color}]{r.symbol}: {r.status}[/{color}]")
    console.print(f"  {r.note}")


@app.command()
def mcp() -> None:
    """Launch the MCP server (stdio)."""
    from harness.finance.mcp_server import main as mcp_main

    mcp_main()


@app.command()
def news(
    symbols: list[str] = typer.Argument(None, help="Symbols (default: portfolio stocks + ETFs)"),
    limit: int = typer.Option(5, "--limit", help="Headlines / filings per symbol"),
) -> None:
    """Keyless news scan — PER-TICKER wire headlines (Yahoo RSS) + recent SEC filings rail.

    Read-only observation for the sounding-board (the 'what hit AAPL today?' layer). Mutual funds
    are skipped by default (thin ticker feeds — a non-intraday fund's direction is `fund-proxy`); feed errors
    print loud, never as empty results.

    For the BROAD market wire (MarketWatch/CNBC/FT/AP/Bloomberg + geopolitics, not keyed on a
    ticker), use `hn finance wire` (config/feeds.yaml). `watch`/`pulse` also fold those broad feeds
    into their fresh-headlines rail, deduped via the seen-cache.
    """
    try:
        scans = _svc().news(symbols=list(symbols) if symbols else None, limit=limit)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    for sn in scans:
        console.print(f"\n[bold]{sn.symbol}[/bold]")
        if sn.headline_error:
            console.print(f"  [red]⚠ headlines: {sn.headline_error}[/red]")
        for h in sn.headlines:
            console.print(f"  {h.published or '—':<17} {h.title}", markup=False)
            console.print(f"                    {h.url}", markup=False, style="dim")
        if sn.filings:
            row = " · ".join(f"{f.form} {f.filed}" for f in sn.filings)
            console.print(f"  [cyan]filings:[/cyan] {row}")
        elif sn.filings_note:
            console.print(f"  [dim]filings: {sn.filings_note}[/dim]")


@app.command()
def wire(
    source: str = typer.Option(
        None, "--source", help="Filter to one feed by name-substring (e.g. 'ft', 'bloomberg', 'aljazeera')"
    ),
    limit: int = typer.Option(8, "--limit", help="Headlines per feed"),
    full: bool = typer.Option(False, "--full", help="Show each item's summary/excerpt under its headline"),
    as_json: bool = typer.Option(False, "--json", help="Raw JSON digest"),
) -> None:
    """Broad-market news WIRE — config/feeds.yaml headlines aggregated NEWEST-FIRST across sources.

    The 'what's the market narrative today?' layer. Sources: MarketWatch / CNBC / FT / AP / Bloomberg
    (markets) + Al Jazeera (geopolitics) + thesis-topic searches — tune them in
    config/feeds.yaml. Distinct from `news` (per-ticker Yahoo, 'what hit AAPL?') and from the
    `watch`/`pulse` fresh-headlines rail (which dedupes the same feeds via the seen-cache): `wire` is
    NEVER seen-filtered, so it returns the FULL wire every run — read it for a `market take`.
    Read-only; per-feed errors print loud (a dead feed is a note, never a silent empty).
    """
    try:
        d = _svc().wire(source=source, limit=limit)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(d.model_dump_json())
        return
    console.print(
        f"[bold]wire · {len(d.items)} headlines · {len(d.sources_read)} feeds[/bold]"
    )
    for it in d.items:
        # ● marks a headline naming a held/watchlist name (the relevance tag).
        mark = "● " if it.holdings_hit else "  "
        book = f"  [{','.join(it.holdings_hit)}]" if it.holdings_hit else ""
        # markup=False — headline text carries arbitrary brackets/markup chars (the news-verb lesson).
        console.print(f"{mark}{it.published or '—':<16} {it.source:<13} {it.title}{book}", markup=False)
        if full and it.summary:
            # strip HTML tags to a plain-text excerpt for the terminal (the bus-app reader renders
            # the raw summary itself). Truncate generously — this is a scan view, not the full read.
            excerpt = re.sub(r"<[^>]+>", "", it.summary)
            excerpt = re.sub(r"\s+", " ", excerpt).strip()
            if len(excerpt) > 280:
                excerpt = excerpt[:279].rstrip() + "…"
            if excerpt:
                console.print(f"{'':<18}{excerpt}", markup=False, style="dim")
    for n in d.notes:
        console.print(f"  ⚠ {n}", markup=False, style="yellow")
    if d.sources_read:
        console.print(f"  sources: {', '.join(d.sources_read)}", markup=False, style="dim")


@app.command()
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


@app.command()
def research(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    months: int = typer.Option(6, "--months", help="Lookback window"),
    threshold: float = typer.Option(3.0, "--threshold", help="Big-move-day cutoff (close-over-close %)"),
) -> None:
    """Event-anchored deep-dive: bars -> big-move days -> date-windowed headlines + filings +
    next-print estimate. Writes finance/research/{SYM}/{date}-catchup.md (+ price chart) — the
    agentic catch-up artifact. ~10-20 self-paced Google News calls; takes ~30s.
    """
    from harness.finance.research import write_research_report

    try:
        bundle, bars = _svc().research(symbol.upper(), months=months, threshold=threshold)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    held = {h.symbol for h in _svc().reader.read_portfolio().holdings}
    out = write_research_report(bundle, bars, get_settings().tracker_path, held_symbols=held)
    sign = "+" if (bundle.window_pct or 0) >= 0 else ""
    console.print(
        f"[bold]{bundle.symbol}[/bold] {bundle.start} → {bundle.end}: "
        f"{sign}{bundle.window_pct:.1f}%  ·  {len(bundle.move_days)} move-days  ·  "
        f"{len(bundle.filings)} material filings"
        if bundle.window_pct is not None
        else f"[bold]{bundle.symbol}[/bold] — gathered"
    )
    if bundle.next_print_estimate:
        console.print(f"next print {bundle.next_print_estimate}")
    for n in bundle.notes:
        console.print(f"[dim]  • {n}[/dim]")
    console.print(f"wrote {out}")


@app.command()
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


def _log_networth(total: float) -> None:
    """Upsert today's net-worth total into finance/networth-history.json (the `line` viz data shape).
    Non-destructive: replaces today's point if present, else appends; keeps the series date-sorted."""
    path = get_settings().tracker_path / "finance" / "networth-history.json"
    today = date.today().isoformat()
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = {
            "title": "Net worth over time",
            "subtitle": "logged via `hn finance networth --log`",
            "yPrefix": "$",
            "series": [{"label": "Net worth", "points": []}],
        }
    points = [p for p in data["series"][0]["points"] if p.get("x") != today]
    points.append({"x": today, "y": round(total, 2)})
    points.sort(key=lambda p: str(p["x"]))
    data["series"][0]["points"] = points
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[dim]  ↳ logged ${total:,.2f} for {today} → {path.name}[/dim]")


def _fail(msg: str) -> None:
    console.print(f"[red]error:[/red] {msg}")


if __name__ == "__main__":
    app()
