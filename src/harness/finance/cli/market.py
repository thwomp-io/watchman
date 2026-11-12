"""Market-data verbs of `hn finance`: quotes, the market overview, the Fed, bars + levels,
correlation, options gauges, the macro calendar, the global rails, analyst ratings.

Commands are registered on the `finance` app in `harness.finance.cli` (that file fixes the
order `hn finance --help` lists them in)."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.table import Table

from harness.errors import HarnessError, UsageError
from harness.finance.cli.common import console, finance_service, signed
from harness.finance.config.settings import get_settings
from harness.finance.levels import support_levels
from harness.finance.providers.alpaca_provider import bars_feed
from harness.finance.providers.base import ProviderError
from harness.finance.sessions import local_stamp, tz_label

if TYPE_CHECKING:
    pass

# Alpaca intraday timeframes (15Min / 15T / 1Hour / 1H): the table shows time of day for these.
_INTRADAY = re.compile(r"^\d+(Min|T|Hour|H)$")
_INTRADAY_BAR = re.compile(r"^(\d+)Min$")
_WINDOW = re.compile(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


def quote(
    symbols: list[str] = typer.Argument(..., help="One or more tickers, e.g. AAPL MSFT GOOGL"),
    feed: str = typer.Option("iex", "--feed", help="iex (free real-time) | sip | delayed_sip"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Live quote(s) for one or more symbols (price + day change vs. previous close)."""
    quotes = finance_service(feed).quote([s.upper() for s in symbols])

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
            signed(q.day_change, money=True),
            signed(q.day_change_pct),
            f"${q.prev_close:,.2f}" if q.prev_close is not None else "—",
            q.feed,
            q.note if q.available else (q.note or "unavailable"),
        )
    console.print(table)


def market(
    feed: str = typer.Option("iex", "--feed", help="iex (free real-time) | sip | delayed_sip"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Bird's-eye market read — indices + breadth, the 11 SPDR sectors, semis, Mag7 dispersion.

    A point-in-time regime snapshot (one Alpaca snapshots call). Deterministic gather + computed
    breadth facts; the interpretive 'take' lives separately in finance/market/take.md (an agent
    writes it on request, so this never depends on a model call)."""
    ov = finance_service(feed).market()

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
                signed(r.day_change_pct),
                signed(r.day_change, money=True),
            )
        return t

    console.print(_grp("Indexes", ov.indices))
    console.print(_grp("Sectors", ov.sectors, by_move=True))
    console.print(_grp("Semis", ov.semis, by_move=True))
    console.print(_grp("Mega-cap (Mag7)", ov.megacap, by_move=True))
    console.print(_grp("Credit", ov.credit))  # fixed order (HYG/LQD/IEF/KRE) — a rail, not a race
    console.print(_grp("Rates (Treasury ETFs)", ov.rates))  # curve order (SHY→TLT, TIP) — a rail

    b = ov.breadth
    spread = f"{b.megacap_spread_pct:.2f}pp" if b.megacap_spread_pct is not None else "—"
    console.print(
        f"\n[bold]Breadth[/bold]  sectors {b.sectors_advancing}▲/{b.sectors_declining}▼  ·  "
        f"equal-wt − cap (RSP−SPY) {signed(b.equal_weight_minus_cap_pct)}  ·  "
        f"Mag7 avg {signed(b.megacap_avg_pct)} (spread {spread})  ·  "
        f"semis avg {signed(b.semis_avg_pct)}  ·  "
        f"credit HY−Tsy {signed(b.credit_hy_minus_tsy_pct)}  ·  "
        f"KRE {signed(b.kre_pct)}"
    )
    console.print(
        f"[bold]Rates[/bold]    duration TLT−SHY {signed(b.tsy_long_minus_short_pct)}  ·  "
        f"breakevens TIP−IEF {signed(b.tip_minus_ief_pct)}  "
        f"[dim](intraday ETF tape; cash-curve levels + 2s10s on `hn finance global -s rates`)[/dim]"
    )

    def _movers(ms: list[MarketMover]) -> str:
        return ", ".join(f"{m.symbol} {signed(m.day_change_pct)}" for m in ms)

    console.print(f"[green]Leaders[/green]  {_movers(ov.leaders)}")
    console.print(f"[red]Laggards[/red] {_movers(ov.laggards)}")
    if ov.as_of:
        console.print(f"[dim]as of {ov.as_of} · feed {feed}[/dim]")
    for n in ov.notes:
        console.print(f"[dim]{n}[/dim]")


def fed(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Latest FOMC decision — statement text + target-rate range + vote, from federalreserve.gov
    (keyless, Fed-direct). The SEP/dot-plot is LINKED, not parsed (eyeball the dots). So a post-FOMC
    read is confirmed, not tape-inferred. The hawkish/dovish call is yours. READ-ONLY."""
    d = finance_service().fed()

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


def history(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str | None = typer.Option(None, "--end", help="YYYY-MM-DD (default: today)"),
    timeframe: str = typer.Option("1Day", "--timeframe", help="e.g. 1Day, 1Hour, 1Week"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Historical OHLCV bars (chart-able data) for a symbol."""
    bars = finance_service(feed).history(symbol.upper(), start=start, end=end, timeframe=timeframe)

    if as_json:
        console.print_json(json.dumps([b.model_dump(by_alias=True) for b in bars]))
        return
    intraday = _INTRADAY.match(timeframe) is not None
    first = local_stamp(bars[0].t) if bars and intraday else None
    when = f"Time ({tz_label(first)})" if first else "Date"
    table = Table(title=f"{symbol.upper()} — {timeframe} bars")
    for col in (when, "Open", "High", "Low", "Close", "Volume"):
        table.add_column(col)
    for b in bars:
        stamp = f"{local_stamp(b.t):%Y-%m-%d %H:%M}" if intraday else b.t[:10]
        table.add_row(stamp, f"{b.o:,.2f}", f"{b.h:,.2f}", f"{b.low:,.2f}", f"{b.c:,.2f}", f"{b.v:,}")
    console.print(table)
    console.print(f"[dim]{len(bars)} bars[/dim]")
    if bars_feed(feed) != feed:
        console.print(f"[dim]{feed} isn't a bars feed on Alpaca: served from {bars_feed(feed)} "
                      "(the free plan's SIP bars are 15-minute delayed)[/dim]")


def session(
    symbols: list[str] = typer.Argument(..., help="One or more tickers, e.g. SPY QQQ AAPL"),
    day: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: the current session)"),
    bar: str = typer.Option("15Min", "--bar", help="5Min | 15Min"),
    window: str | None = typer.Option(None, "--window", help="HH:MM-HH:MM ET: the raw bars for a slice"),
    feed: str = typer.Option("delayed_sip", "--feed", help="delayed_sip (SIP volume, default) | iex"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One regular session's intraday shape: low/high with ET times, close vs low/high, the last-15/30-min
    move, last-15-min volume vs the session's average, the closing-auction size."""
    m = _INTRADAY_BAR.match(bar)
    if not m or int(m.group(1)) not in (5, 15):
        raise UsageError("--bar must be 5Min or 15Min")
    span: tuple[str, str] | None = None
    if window:
        w = _WINDOW.match(window)
        if not w:
            raise UsageError("--window must look like 14:00-16:00 (ET)")
        span = (w.group(1).zfill(5), w.group(2).zfill(5))
    try:
        target = _dt.date.fromisoformat(day) if day else None
        shapes = finance_service(feed).session_shapes(
            [s.upper() for s in symbols], day=target, minutes=int(m.group(1))
        )
    except (ProviderError, ValueError) as e:
        raise HarnessError(str(e)) from e
    if as_json:
        console.print_json(json.dumps([s.model_dump() for s in shapes]))
        return
    first = shapes[0]
    src = "SIP" if feed in ("delayed_sip", "sip") else feed.upper()
    table = Table(title=f"Session {first.session} ({first.state}) · {first.bar} {src} bars · times ET")
    for col in ("Symbol", "Open", "High (ET)", "Low (ET)", "Close", "vs Low", "vs High", "Last 15m",
                "Last 30m", "Last-15 vol", "Closing auction"):
        table.add_column(col)
    for s in shapes:
        if s.open is None:
            table.add_row(s.symbol, *([""] * 9), f"[dim]{s.state}[/dim]")
            continue
        auction = (f"{s.auction_volume:,} @ {s.auction_price:,.2f}" if s.auction_volume is not None
                   else "[dim]—[/dim]" if s.state != "complete" else "[dim]n/a[/dim]")
        table.add_row(
            s.symbol, f"{s.open:,.2f}", f"{s.high:,.2f} ({s.high_bar})", f"{s.low:,.2f} ({s.low_bar})",
            f"{s.close:,.2f}", signed(s.close_vs_low_pct), signed(s.close_vs_high_pct),
            signed(s.last15_pct), signed(s.last30_pct),
            f"{s.last15_rel_volume:.1f}× avg" if s.last15_rel_volume is not None else "—", auction,
        )
    console.print(table)
    bases = sorted({s.close_basis for s in shapes if s.close_basis})
    console.print(f"[dim]  close = {', '.join(bases)} · high/low = the ET start of the bar it printed in · "
                  "the auction is the listing exchange's closing print[/dim]")
    for s in shapes:
        for n in s.notes:
            console.print(f"[dim]  • {s.symbol}: {n}[/dim]")
    if span:
        for s in shapes:
            rows = [b for b in s.bars if span[0] <= b.t < span[1]]
            t2 = Table(title=f"{s.symbol} {span[0]}-{span[1]} ET · {s.bar} bars")
            for col in ("Time (ET)", "Open", "High", "Low", "Close", "Volume"):
                t2.add_column(col)
            for b in rows:
                t2.add_row(b.t, f"{b.o:,.2f}", f"{b.h:,.2f}", f"{b.low:,.2f}", f"{b.c:,.2f}", f"{b.v:,}")
            console.print(t2)


def bars(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL"),
    days: int = typer.Option(
        90, "--days", help="Lookback window in CALENDAR days (250 calendar days ≈ 171 sessions)"
    ),
    rows: int = typer.Option(12, "--rows", help="Bars to print in the table (0 = all)"),
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
    bar_list = finance_service(feed).history(sym, start=start)
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
        if as_json and not get_settings().has_alpaca_keys:
            # the same graceful path for the price chart: an empty report that says why
            from harness.finance.models import BarsReport

            empty = BarsReport(symbol=sym, days=days, last_close=None, as_of=None, sessions=0,
                               levels=[], bars=[],
                               notes=["historical bars need a live market-data key (set ALPACA_API_KEY_ID)"])
            console.print_json(empty.model_dump_json(by_alias=True))
            return
        raise HarnessError(f"no bars returned for {sym} (unknown symbol / not on feed?)")
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
        from harness.finance.models import BarsReport

        report = BarsReport(symbol=sym, days=days, last_close=last.c, as_of=last.t[:10],
                            sessions=len(bar_list), levels=levels, bars=bar_list)
        console.print_json(report.model_dump_json(by_alias=True))
        return

    shown = bar_list if rows <= 0 else bar_list[-rows:]
    table = Table(
        title=f"{sym} — {len(shown)} of {len(bar_list)} sessions ({days} calendar-day lookback)"
    )
    for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
        table.add_column(col)
    for b in shown:
        table.add_row(b.t[:10], f"{b.o:,.2f}", f"{b.h:,.2f}", f"{b.low:,.2f}",
                      f"{b.c:,.2f}", f"{b.v:,}")
    console.print(table)

    lt = Table(title=f"Levels — clustered swing lows (wing={wing}, tol={tol}%)")
    for col in ("Level", "Kind", "Touches", "Last touch", "vs close"):
        lt.add_column(col)
    for lv in levels:
        kind = "support" if lv.kind == "support" else "[yellow]overhead (broken support)[/yellow]"
        lt.add_row(f"${lv.level:,.2f}", kind, str(lv.touches), lv.last_touch,
                   signed(lv.distance_pct))
    console.print(lt)
    console.print(
        f"[dim]  • last close ${last.c:,.2f} ({last.t[:10]}) · levels describe PAST swing lows "
        "(touches + recency = evidence), not predictions. Order placement is the user's.[/dim]"
    )


def correlate(
    symbols: list[str] = typer.Argument(None, help="Symbols; the FIRST is the focal name"),
    held: bool = typer.Option(
        False, "--held", help="Correlate the book: the quotable holdings, largest cost basis first "
        "(capped by --limit)",
    ),
    limit: int = typer.Option(20, "--limit", help="With --held: the most names to include"),
    days: int = typer.Option(120, "--days", help="Lookback window (calendar days)"),
    feed: str = typer.Option("iex", "--feed"),
    factor: str = typer.Option(
        None, "--factor", help="Comma-separated symbols → an equal-weight FACTOR basket "
        "(e.g. 'NVDA,AMD,MSFT') → each name's corr + beta to it + the focal's "
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
    syms = finance_service(feed).held_symbols()[:limit] if held else [s.upper() for s in symbols or []]
    if len(syms) < 2:
        raise UsageError("give two or more symbols, or --held for the book")
    fac = [s.strip().upper() for s in factor.split(",") if s.strip()] if factor else None
    rep = finance_service(feed).correlate(syms, days=days, factor=fac)
    if as_json:
        console.print_json(rep.model_dump_json())
        return
    if rep.n_obs < 3:
        raise HarnessError("insufficient overlapping history — " + "; ".join(rep.notes or ["no data"]))

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
            dt.add_row(d.date, signed(d.focal_ret_pct), signed(d.factor_ret_pct), signed(d.gap_pct))
        console.print(dt)
    if rep.notes:
        console.print("[dim]  • " + " · ".join(rep.notes) + "[/dim]")


def gauges(
    symbol: str = typer.Argument(..., help="Underlying ticker (e.g. SPY)"),
    feed: str = typer.Option(
        "iex", "--feed", help="Stock feed for spot/bars (options are always indicative)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Flat scalars — dashboard-tile ready"),
) -> None:
    """Options-positioning gauges — put/call ratio + IV30 vs HV30 with the spread read.

    The broker-card gauges as a deterministic verb: session put/call (plus an open-interest ratio
    when the contracts roster is reachable), IV30 (median near-the-money implied vol 20-45d out),
    HV30 (trailing realized vol off the daily tape), and IV−HV with a braced/neutral/complacent
    label. SENTIMENT THERMOMETERS, never advice — heavy puts can be fear or longs buying insurance;
    the interpretation is yours. Short interest is NOT here (broker card / FINRA is the source).
    """
    rep = finance_service(feed).gauges(symbol.upper())
    if as_json:
        console.print_json(rep.model_dump_json())
        return

    spot = f"${rep.spot:,.2f}" if rep.spot is not None else "unquotable"
    console.print(
        f"[bold]gauges[/bold] · {rep.symbol} @ {spot} · chain: {rep.n_contracts} contracts "
        f"({rep.n_calls}C / {rep.n_puts}P) · session {rep.chain_session or '—'}"
    )
    t = Table(title="Positioning gauges (sentiment thermometers, not advice)")
    for col in ("Gauge", "Value", "Detail"):
        t.add_column(col)
    t.add_row(
        "Put/Call (volume)",
        f"{rep.pc_ratio_volume:.2f}" if rep.pc_ratio_volume is not None else "—",
        f"{rep.put_volume:,} put / {rep.call_volume:,} call contracts traded",
    )
    if rep.oi_available:
        t.add_row(
            "Put/Call (open int)",
            f"{rep.pc_ratio_oi:.2f}" if rep.pc_ratio_oi is not None else "—",
            f"{rep.put_oi:,} put / {rep.call_oi:,} call open interest · as of {rep.oi_as_of or '?'}",
        )
    iv_detail = f"median IV of {rep.iv30_n} contracts 20-45d out, strike ±10% of spot"
    if rep.iv30_low_confidence:
        iv_detail += " [yellow](LOW-CONFIDENCE)[/yellow]"
    t.add_row("IV30", f"{rep.iv30:.1f}%" if rep.iv30 is not None else "—", iv_detail)
    t.add_row(
        "HV30",
        f"{rep.hv30:.1f}%" if rep.hv30 is not None else "—",
        f"realized: {rep.hv30_returns} daily log returns, annualized",
    )
    if rep.iv_hv_spread is not None and rep.spread_read is not None:
        style = {"braced": "yellow", "complacent": "red", "neutral": "green"}[rep.spread_read]
        gloss = {
            "braced": "options pricing notably more movement than the tape delivered",
            "complacent": "options pricing notably less movement than the tape delivered",
            "neutral": "options pricing roughly the movement the tape delivered",
        }[rep.spread_read]
        t.add_row(
            "IV − HV spread",
            f"[{style}]{rep.iv_hv_spread:+.1f} pts · {rep.spread_read}[/{style}]",
            gloss,
        )
    console.print(t)
    for n in rep.notes:
        console.print(f"[dim]  • {n}[/dim]")


def calendar(
    days: int = typer.Option(14, "--days", help="Days ahead, today included (Nasdaq publishes ~2 weeks)"),
    show_all: bool = typer.Option(False, "--all", help="Every US release + auction, not just the high tier"),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore the cache"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The macro release calendar from primary sources — economic releases (with actual /
    consensus / previous once printed, Nasdaq), FOMC decisions + estimated minutes (the Fed's own
    calendar), coupon auctions (TreasuryDirect), market holidays and early closes (SIFMA, NYSE).
    A printed number is read from its agency where one is reachable (BLS, BEA, Census) and shown
    with the agency's name; a figure the agency left out reads 'not published'.
    High tier by default: CPI, PPI, payrolls, PCE, GDP,
    retail sales, ISM, S&P Global PMIs, JOLTS, claims, the Fed (decisions, minutes, every scheduled
    Fed speaker), UMich, consumer confidence, EIA crude, durables. A release past its time with no
    number yet reads 'printed · actual not posted yet' (the source lags; read the wire).
    Cached (today 30 min, future 12 h); the gates board + pulse merge the high tier beside the
    hand-kept macro_events ledger. A dead source is a note, never a dead read."""

    from harness.finance.calendar import build_calendar

    if as_json:
        from harness.finance.reads import calendar_payload

        console.print_json(json.dumps(calendar_payload(days, show_all=show_all, refresh=refresh)))
        return
    cal = build_calendar(days, refresh=refresh)
    events = [e for e in cal.events if show_all or e.tier == "high" or e.source == "treasury"]
    console.print(f"[bold]calendar · {cal.start} + {days}d · {len(events)} events"
                  f"{'' if show_all else ' (high tier + auctions; --all for every release)'}[/bold]")
    current = ""
    for e in events:
        if e.date != current:
            current = e.date
            console.print(f"\n[bold]{_dt.date.fromisoformat(e.date):%a %m-%d}[/bold]")
        mark = "[bold]●[/bold]" if e.tier == "high" else " "
        via = f" ({e.actual_source})" if e.actual_source and e.actual_source != e.source else ""
        num = f"  [green]actual {escape(e.actual)}{via}[/green]" if e.actual else (
            "  [yellow]printed · actual not posted yet (read the wire)[/yellow]" if e.awaiting_actual else "")
        rest = " · ".join(x for x in (
            f"cons {e.consensus}" if e.consensus else "", f"prev {e.previous}" if e.previous else "",
        ) if x)
        est = " [yellow](est)[/yellow]" if not e.confirmed else ""
        console.print(f"  {mark} {e.time_et or '     ':>7}  {escape(e.name)}{est}{num}"
                      f"  [dim]{escape(rest)} · {e.source}[/dim]")
    for n in cal.notes:
        console.print(f"[yellow]  ⚠ {escape(n)}[/yellow]")


def _fmt_spread(curve: Any, name: str) -> str:
    """'+39bp (-8.0)' — the spread level in bp with its day change, or '—' when a leg is missing."""
    sp = curve.spreads_bp
    if name not in sp:
        return "—"
    d = curve.spread_changes_bp.get(name)
    return f"{sp[name]:+.0f}bp" + (f" ({d:+.1f})" if d is not None else "")


def global_markets(
    section: list[str] = typer.Option(
        None, "--section", "-s",
        help="Rails to show (futures/rates/asia/europe/oil/metals/fx); default all. The `rates` "
        "rail also carries the Treasury cash curve (2-yr level, 2s10s/3m10s/5s30s).",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Follow-the-sun global read — index futures, rates + the Treasury curve, Asia/Europe, oil,
    metals, FX (keyless Yahoo + home.treasury.gov).

    The overnight surface the US tape can't show: is the futures market panicking? did Asia
    confirm? where's Brent? what did the curve do? Each rail degrades per-card, never bricks on
    one symbol; the curve degrades to a note. Deterministic gather — interpretation stays with
    the agent takes."""
    from datetime import datetime

    from harness.finance.providers.global_provider import (
        GLOBAL_RAILS,
        resolve_rails,
    )
    from harness.finance.providers.treasury_provider import fetch_yield_curve

    secs = list(section) if section else None
    if as_json:
        from harness.finance.reads import global_payload

        console.print_json(json.dumps(global_payload(secs)))
        return
    bad = [s for s in (secs or []) if s not in GLOBAL_RAILS]
    if bad:
        raise HarnessError(f"unknown section(s): {', '.join(bad)} (valid: {', '.join(GLOBAL_RAILS)})")
    resolved = resolve_rails(secs)
    rails = resolved.rails

    # The cash curve rides with the rates rail (default read includes it). Best-effort: a
    # Treasury outage is a note on the payload, never a failed overnight read.
    curve = None
    curve_note = ""
    if secs is None or "rates" in secs:
        try:
            curve = fetch_yield_curve()
        except ProviderError as e:
            curve_note = f"treasury curve unavailable: {e}"


    for sec, cards in rails.items():
        t = Table(title=sec.upper())
        t.add_column("")
        t.add_column("Price", justify="right")
        t.add_column("Day %", justify="right")
        t.add_column("Day", justify="right")
        t.add_column("State", justify="right")
        for q in cards:
            pct = q.change_pct
            color = "green" if (pct or 0) > 0 else "red" if (pct or 0) < 0 else "dim"
            # LIVE + a time, or CLOSED + the session's own date — a bare time cannot say which day
            # a closed venue's number is from (a Friday close read as a live Sunday move).
            if q.market_time is None:
                when = "—"
            elif q.live:
                when = "LIVE " + datetime.fromtimestamp(q.market_time, tz=_dt.UTC).strftime("%H:%MZ")
            else:
                when = "CLOSED " + q.session_label.replace(" close", "")
            t.add_row(
                q.name,
                f"{q.price:,.2f}" if q.price is not None else "—",
                f"[{color}]{pct:+.2f}%[/{color}]" if pct is not None else "—",
                f"{q.change:+,.2f}" if q.change is not None else "—",
                f"[dim]{when}[/dim]",
            )
        console.print(t)
        missing = len(GLOBAL_RAILS[sec]) - len(cards)
        if missing:
            console.print(f"[dim]  ({missing} symbol(s) unavailable on the feed — omitted)[/dim]")
        if sec == "rates":
            if curve is not None:
                ct = Table(title=f"TREASURY CURVE · {curve.as_of} (EOD, par yields)")
                ct.add_column("Tenor")
                ct.add_column("Yield", justify="right")
                ct.add_column("Day (bp)", justify="right")
                for row in curve.rows:
                    chg = row.chg_bp
                    color = "red" if (chg or 0) > 0 else "green" if (chg or 0) < 0 else "dim"
                    ct.add_row(
                        row.label,
                        f"{row.yield_pct:.2f}%",
                        f"[{color}]{chg:+.1f}[/{color}]" if chg is not None else "—",
                    )
                console.print(ct)
                console.print(
                    f"[bold]Curve[/bold]  2s10s {_fmt_spread(curve, '2s10s')}  ·  "
                    f"3m10s {_fmt_spread(curve, '3m10s')}  ·  5s30s {_fmt_spread(curve, '5s30s')}  "
                    f"[dim](Δ vs {curve.prior_date or 'prior'})[/dim]"
                )
                if curve.note:
                    console.print(f"[dim]  {curve.note}[/dim]")
            elif curve_note:
                console.print(f"[yellow]  {curve_note}[/yellow]")
    for note in resolved.notes:
        # roll days, alias fallbacks and continuity gaps — the lines a take must not skip
        color = "yellow" if note.startswith(("CONTINUITY", "continuity")) or "ROLL" in note else "dim"
        console.print(f"[{color}]{escape(note)}[/{color}]")


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
    r = finance_service().ratings(sym)

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
        return signed(round((v - cur) / cur * 100.0, 2)) if (v and cur) else "—"
    t.add_row("Mean", f"${r.target_mean:,.2f}" if r.target_mean else "—", _vs(r.target_mean))
    t.add_row("Median", f"${r.target_median:,.2f}" if r.target_median else "—", _vs(r.target_median))
    t.add_row("High", f"${r.target_high:,.2f}" if r.target_high else "—", _vs(r.target_high))
    t.add_row("Low", f"${r.target_low:,.2f}" if r.target_low else "—", _vs(r.target_low))
    console.print(t)
    console.print(
        "[dim]  • Sell-side consensus (Yahoo) — INFORMATION, not a verdict. Targets skew bullish, "
        "herd, and lag price (~1/3 hit at 12mo). Read the spread + count, not a single number.[/dim]"
    )


def _parse_groups(spec: str) -> dict[str, list[str]]:
    """'buyers=AAA,BBB;power=CCC' -> {'buyers': ['AAA', 'BBB'], 'power': ['CCC']}."""
    out: dict[str, list[str]] = {}
    for part in (p.strip() for p in spec.split(";") if p.strip()):
        name, _, members = part.partition("=")
        syms = [m.strip().upper() for m in members.split(",") if m.strip()]
        if not name.strip() or not syms:
            raise ValueError(f"--groups: {part!r} isn't NAME=SYM,SYM")
        out[name.strip()] = syms
    return out


def _cell(v: float | None) -> str:
    return signed(v) if v is not None else ""


def moves(
    symbols: list[str] = typer.Argument(..., help="Tickers, e.g. AAA BBB CCC"),
    start: str = typer.Option(
        ..., "--from", help="YYYY-MM-DD: the base close (the last one on or before it)"
    ),
    end: str = typer.Option(None, "--to", help="YYYY-MM-DD (default: today)"),
    groups: str = typer.Option(
        "", "--groups", help="Group means: 'buyers=AAA,BBB;power=CCC' (members not listed are fetched too)"
    ),
    feed: str = typer.Option("delayed_sip", "--feed", help="delayed_sip (SIP bars, default) | iex"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Several names' close-to-close moves on each session of a window, plus the cumulative: the event-study
    table after a shock. OTC names the exchange feed lacks come from their Yahoo daily bars."""
    try:
        grp = _parse_groups(groups) if groups else None
        lo = _dt.date.fromisoformat(start)
        hi = _dt.date.fromisoformat(end) if end else _dt.date.today()
        table = finance_service(feed).moves([s.upper() for s in symbols], lo, hi, groups=grp)
    except (ProviderError, ValueError) as e:
        raise HarnessError(str(e)) from e
    if as_json:
        console.print_json(table.model_dump_json())
        return
    t = Table(title=f"Moves after the {table.start} close, through {table.end}")
    t.add_column("Symbol")
    for d in table.sessions:
        t.add_column(_dt.date.fromisoformat(d).strftime("%a %m-%d"), justify="right")
    t.add_column("Cumulative", justify="right")
    for r in table.rows:
        label = r.symbol + (" [dim](yahoo)[/dim]" if r.source == "yahoo" else "")
        if r.reason and not any(m is not None for m in r.moves):
            t.add_row(label, *([""] * len(table.sessions)), f"[dim]{escape(r.reason)}[/dim]")
            continue
        t.add_row(label, *(_cell(m) for m in r.moves), f"[bold]{_cell(r.cumulative)}[/bold]")
    for g in table.groups:
        name = f"[bold]{escape(g.name)}[/bold] [dim]({len(g.members)})[/dim]"
        t.add_row(name, *(_cell(m) for m in g.moves), f"[bold]{_cell(g.cumulative)}[/bold]")
    console.print(t)
    for n in table.notes:
        console.print(f"[dim]  • {escape(n)}[/dim]")


def crashdays(
    symbol: str = typer.Argument(..., help="Ticker, e.g. QQQ"),
    threshold: float = typer.Option(-7.0, "--threshold", help="A day at or below this move (%), e.g. -7"),
    since: str = typer.Option("2016-01-01", "--since", help="YYYY-MM-DD: the first day to consider"),
    cluster_days: int = typer.Option(
        0, "--cluster-days", help="Fold events within N sessions into one episode, shown on its deepest day"
    ),
    feed: str = typer.Option("delayed_sip", "--feed", help="delayed_sip (SIP bars, default) | iex"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """A symbol's worst days and what followed: the day's move, the drawdown from the prior high, forward
    returns at +5/+20/+60/+120/+250 sessions, and the lowest close in the next 60. A base rate, not a
    forecast: why each day happened is research, not arithmetic."""
    if threshold >= 0:
        raise UsageError("--threshold is a loss, e.g. -7")
    try:
        rep = finance_service(feed).crashdays(symbol.upper(), threshold, _dt.date.fromisoformat(since),
                                   cluster_days=cluster_days)
    except (ProviderError, ValueError) as e:
        raise HarnessError(str(e)) from e
    if as_json:
        console.print_json(rep.model_dump_json())
        return
    unit = "episodes" if cluster_days else "days"
    t = Table(title=f"{rep.symbol}: {rep.count} {unit} at or below {rep.threshold:g}% since {rep.since} "
                    f"(data through {rep.through})")
    cols = ["Date", "Day", "Off high", *(f"+{k}d" for k in rep.horizons), f"Worst close in {rep.low_window}d"]
    if cluster_days:
        cols.insert(1, "Episode")
    for c in cols:
        t.add_column(c, justify="left" if c in ("Date", "Episode") else "right")
    for r in rep.rows:
        fwd = [_cell(r.forward.get(str(k))) for k in rep.horizons]
        row = [r.date, _cell(r.move_pct), _cell(r.off_high_pct), *fwd, _cell(r.worst_low_pct)]
        if cluster_days:
            span = f"{r.episode_start} → {r.episode_end} ({r.episode_days})"
            row.insert(1, span if r.episode_days > 1 else "")
        t.add_row(*row)
    console.print(t)
    for n in rep.notes:
        console.print(f"[dim]  • {escape(n)}[/dim]")
