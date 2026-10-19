"""Research verbs of `hn finance`: CIK resolve, fundamentals, multiples, filings, compare, news,
the wire (+ `wire read`), the research deep-dive, scorecards, projections, the values screen.

Commands are registered on the `finance` app in `harness.finance.cli` (that file fixes the
order `hn finance --help` lists them in)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape
from rich.table import Table

from harness.finance.config.settings import get_settings
from harness.finance.providers.base import ProviderError

if TYPE_CHECKING:
    pass
from harness._cli import fail as _fail
from harness.finance._cli import _money_short, _signed, _svc, _trend_money, console

wire_app = typer.Typer(invoke_without_command=True, no_args_is_help=False)


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


def filing(
    symbol: str = typer.Argument(..., help="Ticker, e.g. AAPL (must be a US SEC filer)"),
    form: str = typer.Option("8-K", "--form", help="Form family to read (8-K also matches 8-K/A)"),
    cik: str | None = typer.Option(None, "--cik", help="Override: use this CIK directly (skip the map)"),
    accession: str | None = typer.Option(
        None, "--accession", help="Read a specific filing (dashed accession number) instead of the newest"
    ),
    doc: str | None = typer.Option(
        None, "--doc", help="Read a specific document from the filing directory (see --list)"
    ),
    list_docs: bool = typer.Option(
        False, "--list", help="List the filing's documents without fetching content"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch + READ a filing's content from the EDGAR Archives — the earnings-print reader.

    Default resolves to the newest 8-K's EX-99 press-release exhibit (headline numbers, guidance,
    KPI tables — on EDGAR within minutes of the wire release). The loop it enables: the pulse's
    print_landed flag fires -> this verb reads the release -> grade against the scenario map.
    Requires HARNESS_SEC_CONTACT in .env (SEC fair-access UA for www.sec.gov, fail-loud).
    Honest boundary: call transcripts + un-filed IR slides are NOT SEC documents — this reads
    what EDGAR carries."""
    try:
        r = _svc().filing(
            symbol.upper(), form=form, cik=cik, accession=accession, doc=doc, list_only=list_docs
        )
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(r.model_dump_json())
        return
    filed = f" filed {r.filed}" if r.filed else ""
    console.print(f"[bold]{r.symbol}[/bold]  {r.form}{filed}  [dim]accession {r.accession}[/dim]")
    if list_docs:
        table = Table(title="Filing documents")
        table.add_column("Document")
        table.add_column("Type")  # from the filing's own SGML header — how you spot EX-99.2 slides
        for name in r.documents:
            table.add_row(name, r.doc_types.get(name, ""))
        console.print(table)
    else:
        console.print(f"[dim]{r.url}[/dim]\n")
        # markup/highlight off: the document is foreign text — brackets/numbers must print verbatim.
        console.print(r.text, markup=False, highlight=False)
    for n in r.notes:
        console.print(f"[dim]  • {n}[/dim]")


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


def news(
    symbols: list[str] = typer.Argument(None, help="Symbols (default: portfolio stocks + ETFs)"),
    limit: int = typer.Option(5, "--limit", help="Headlines / filings per symbol"),
) -> None:
    """Keyless news scan — PER-TICKER wire headlines (Yahoo RSS) + recent SEC filings rail.

    Read-only observation for the assistant (the 'what hit AAPL today?' layer). Mutual funds
    are skipped by default (thin ticker feeds — a non-intraday fund's direction is `fund-proxy`); feed errors
    print loud, never as empty results.

    For the BROAD market wire (the configured feeds.yaml wires + geopolitics, not keyed on a
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


@wire_app.command("read")
def wire_read(
    url: str = typer.Argument(None, help="An article URL (a wire item's link)"),
    match: str = typer.Option(
        None, "--match", help="Instead of a URL: read the NEWEST wire headline containing this text"
    ),
    max_chars: int = typer.Option(6000, "--max-chars", help="Body characters to print (0 = all)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch ONE article and extract its body (keyless) — the read behind a wire headline.

    Order of trust: the publisher's JSON-LD articleBody → <article> paragraphs → page paragraphs →
    the dek. Google News links (the AP/Reuters/Bloomberg lanes) resolve to the publisher first.
    Paywalls, bot walls and JS-only pages are reported as such, never papered over."""
    from dataclasses import asdict

    from harness.finance.providers.article_provider import read_article

    if (url is None) == (match is None):
        _fail("give a URL or --match TEXT (one, not both)")
        raise typer.Exit(code=2)
    if match is not None:
        needle = match.lower()
        hits = [it for it in _svc().wire(limit=8).items if needle in it.title.lower() and it.url]
        if not hits:
            _fail(f"no wire headline contains {match!r} (the wire keeps each feed's 8 newest)")
            raise typer.Exit(code=1)
        url = hits[0].url
        if not as_json:
            console.print(f"[dim]{len(hits)} match(es) — reading the newest: {hits[0].source} "
                          f"{hits[0].published}[/dim]")
            for other in hits[1:4]:
                console.print(f"[dim]  also: {other.source} {other.published} "
                              f"{escape(other.title[:90])}[/dim]")
    assert url is not None
    try:
        art = read_article(url)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if as_json:
        console.print_json(json.dumps(asdict(art)))
        return
    console.print(f"[bold]{escape(art.title or '(no title)')}[/bold]")
    meta = " · ".join(x for x in (art.site, art.published[:16], f"{art.words} words",
                                   f"via {art.method}") if x)
    console.print(f"[dim]{escape(meta)}[/dim]")
    if art.dek and art.method != "dek":
        console.print(f"[italic]{escape(art.dek)}[/italic]")
    body = art.body if max_chars <= 0 or len(art.body) <= max_chars else art.body[:max_chars].rstrip() + " …"
    console.print()
    console.print(body, markup=False)
    for n in art.notes:
        console.print(f"[yellow]  ⚠ {escape(n)}[/yellow]")


@wire_app.command("audit")
def wire_audit(
    ticker: str = typer.Option("NVDA", "--ticker", help="The ticker the per-ticker feeds are sampled for"),
    sample: int = typer.Option(8, "--sample", help="Newest items read per feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """What each feed ships beyond the headline — of its newest items, how many carry an image, an
    author, category tags, a Google News outlet and a real summary. Covers the per-ticker Google News
    and Yahoo feeds plus every feeds.yaml wire; rerun when the roster changes."""
    from urllib.parse import quote_plus

    from harness.finance.providers.news_provider import _GNEWS_SEARCH_URL, _YAHOO_RSS_URL, audit_feed
    from harness.finance.service import _load_feeds

    sym = ticker.upper()
    feeds = [
        (f"gnews:{sym}", f"{_GNEWS_SEARCH_URL}?q={quote_plus(sym + ' stock')}&hl=en-US&gl=US&ceid=US:en"),
        (f"yahoo:{sym}", f"{_YAHOO_RSS_URL}?s={sym}&region=US&lang=en-US"),
    ] + [(f["name"], f["url"]) for f in _load_feeds()]
    rows = [audit_feed(url, name, sample=sample) for name, url in feeds]
    if as_json:
        console.print_json(json.dumps({"sample": sample, "feeds": rows}))
        return
    table = Table(title=f"Wire feed audit — newest {sample} items per feed")
    for col in ("Feed", "Items", "Image", "Author", "Tags", "Outlet", "Summary"):
        table.add_column(col, justify="left" if col == "Feed" else "right")
    for r in rows:
        if r["error"]:
            table.add_row(r["feed"], "[red]error[/red]", "", "", "", "", escape(r["error"][:40]))
            continue
        table.add_row(r["feed"], str(r["items"]), *(str(r[k]) for k in
                      ("image", "author", "tags", "publisher", "summary")))
    console.print(table)


@wire_app.callback()
def wire(
    ctx: typer.Context,
    source: str = typer.Option(
        None, "--source", help="Filter to one feed by name-substring (any feeds.yaml name fragment)"
    ),
    limit: int = typer.Option(8, "--limit", help="Headlines per feed"),
    full: bool = typer.Option(False, "--full", help="Show each item's summary/excerpt under its headline"),
    as_json: bool = typer.Option(False, "--json", help="Raw JSON digest"),
) -> None:
    """Broad-market news WIRE — config/feeds.yaml headlines aggregated NEWEST-FIRST across sources.

    The 'what's the market narrative today?' layer. Sources: the configured market wires
    (markets) + geopolitics feeds + thesis-topic searches — tune them in
    config/feeds.yaml. Distinct from `news` (per-ticker Yahoo, 'what hit AAPL?') and from the
    `watch`/`pulse` fresh-headlines rail (which dedupes the same feeds via the seen-cache): `wire` is
    NEVER seen-filtered, so it returns the FULL wire every run — read it for a `market take`.
    Read-only; per-feed errors print loud (a dead feed is a note, never a silent empty).
    `hn finance wire read <url>` reads one item's article body.
    """
    if ctx.invoked_subcommand is not None:
        return
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
        # markup=False — headline text carries arbitrary markup chars Rich would otherwise parse.
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


def scorecards(
    as_json: bool = typer.Option(False, "--json", help="Emit the PRINTS-tab dashboard contract"),
) -> None:
    """Print-scorecard registry — every graded print, newest first (the scorecard book's data feed).

    Reads the agent-appended registry (finance/reference/print-scorecards.yaml; pack-overridable).
    Deterministic load/validate/sort only — the grades themselves were written at print time by
    the agent (or you), never here (no model in the render path).
    """
    import yaml

    from harness.finance.scorecards import ScorecardError, load_scorecards, to_contract

    path = get_settings().scorecards_path
    if path is None:
        if as_json:
            console.print_json(json.dumps({"scorecards": [], "summary": {"total": 0, "by_grade": {}}}))
        else:
            console.print(
                "[yellow]no print-scorecard registry found[/yellow] — expected "
                "finance/reference/print-scorecards.yaml in the tracker corpus (entries are "
                "appended by the agent (or you) at grade time)"
            )
        return
    try:
        cards = load_scorecards(path)
    except (OSError, ScorecardError, yaml.YAMLError) as e:
        _fail(f"scorecard registry unreadable ({path}): {e}")
        raise typer.Exit(code=1) from e
    contract = to_contract(cards)
    if as_json:
        console.print_json(json.dumps(contract))
        return
    grade_style = {
        "GREAT": "bold green", "GOOD": "green", "OK": "yellow",
        "BAD": "dark_orange", "DISASTER": "red", "PENDING": "dim",
    }
    table = Table(title=f"Print scorecards · {contract['summary']['total']} graded prints")
    table.add_column("Print", style="bold")
    table.add_column("Period")
    table.add_column("Date")
    table.add_column("Grade")
    table.add_column("Reaction")
    table.add_column("Headline", max_width=64)
    for c in cards:
        style = grade_style.get(c.grade, "")
        reaction = "—" if c.price_reaction is None else f"{c.price_reaction:+.1f}%"
        held = " ·H" if c.held else ""
        table.add_row(
            f"{c.symbol}{held}", c.period, c.print_date.isoformat(),
            f"[{style}]{c.grade}[/{style}]", reaction, c.headline,
        )
    console.print(table)
    console.print("[dim]  • H = held at print · full card: the doc each block opens in Watchman[/dim]")


def projections(
    as_json: bool = typer.Option(False, "--json", help="Emit the PROJECTIONS-tab dashboard contract"),
    feed: str = typer.Option("iex", "--feed", help="Alpaca data feed (iex free / sip paid)"),
) -> None:
    """Scenario-grid projections — per-name risk/reward bands per horizon (the projections tab's feed).

    Reads the agent-authored params registry (finance/reference/projection-params.yaml;
    pack-overridable): forward EPS + a growth band + a multiple band per name, with provenance.
    Deterministic arithmetic only (price = EPS × multiple, compounded per horizon) against the live
    quote (ref_price fallback, source named) — scenarios to reason against, never forecasts; the
    bands themselves were authored at research time by the agent (or you), never here. Each name
    also carries an item_level (the 12-month mid-scenario return %) and a rarity tier (legendary /
    epic / rare / common / poor, bucketed on that number) for at-a-glance scanning.
    """
    import yaml

    from harness.finance.projections import ProjectionError, build_contract, load_params

    path = get_settings().projections_path
    empty = {"projections": [], "summary": {"total": 0, "held": 0, "stale": 0}}
    if path is None:
        if as_json:
            console.print_json(json.dumps(empty))
        else:
            console.print(
                "[yellow]no projection-params registry found[/yellow] — expected "
                "finance/reference/projection-params.yaml in the tracker corpus (entries are "
                "authored by the agent (or you) at research time)"
            )
        return
    try:
        params = load_params(path)
    except (OSError, ProjectionError, yaml.YAMLError) as e:
        _fail(f"projection registry unreadable ({path}): {e}")
        raise typer.Exit(code=1) from e
    # Live prices (best-effort — offline/keyless degrades per-name to ref_price, source named)
    quotes: dict[str, float] = {}
    try:
        for q in _svc(feed).quote([p.symbol for p in params]):
            if q.available and q.price is not None:
                quotes[q.symbol] = q.price
    except Exception:  # noqa: BLE001 — a dead provider is a degradation, never a dead verb
        pass
    holdings: dict[str, float] = {}
    try:
        for h in _svc(feed).reader.read_portfolio().holdings:
            if h.avg_cost:
                holdings[h.symbol] = float(h.avg_cost)
    except Exception:  # noqa: BLE001 — projections must render even with no/broken corpus seed
        pass
    contract = build_contract(params, quotes, holdings)
    if as_json:
        console.print_json(json.dumps(contract))
        return
    for entry in contract["projections"]:
        held = " ·H" if entry["held"] else ""
        stale = " [dark_orange](params stale)[/dark_orange]" if entry["stale"] else ""
        src = "" if entry["price_source"] == "live" else " [dim](ref price)[/dim]"
        console.print(
            f"\n[bold]{entry['symbol']}{held}[/bold] @ ${entry['price']:,.2f}{src} · "
            f"fwd {entry['fwd_multiple_now']}x on ${entry['fwd_eps']}/sh · "
            f"growth {entry['growth_pct_low']:g}–{entry['growth_pct_high']:g}%/yr · "
            f"mult {entry['mult_low']:g}/{entry['mult_mid']:g}/{entry['mult_high']:g}x{stale}"
        )
        table = Table(show_edge=False, pad_edge=False)
        table.add_column("Horizon")
        table.add_column("Tier", style="dim")
        table.add_column("Low", justify="right")
        table.add_column("Mid", justify="right")
        table.add_column("High", justify="right")
        for row in entry["grid"]:
            rets = row["return_pct"]

            def fmt(v: float) -> str:
                color = "green" if v >= 0 else "red"
                return f"[{color}]{v:+.1f}%[/{color}]"

            table.add_row(row["horizon"], row["tier"], fmt(rets["low"]), fmt(rets["mid"]), fmt(rets["high"]))
        console.print(table)
        console.print(f"[dim]  {entry['eps_basis']} · {entry['provenance']} · as of {entry['as_of']}[/dim]")
    console.print(f"\n[dim]{contract['disclaimer']}[/dim]")


def screen(
    symbol: str = typer.Argument(..., help="Ticker to check against the values screen"),
) -> None:
    """Check a symbol against the values screen (corpus-only; no network)."""
    r = _svc().screen(symbol)
    color = "red" if r.status == "excluded" else "green"
    console.print(f"[{color}]{r.symbol}: {r.status}[/{color}]")
    console.print(f"  {r.note}")


def take_write(
    file: Path = typer.Option(None, "--file", "-f", help="Markdown body to write (default: stdin)"),
    session: str = typer.Option("", "--session", help="Session label, e.g. overnight-asia"),
    tag: list[str] = typer.Option(None, "--tag", help="A tag (repeatable)"),
    author: str = typer.Option("", "--author", help="Author label (default: $BEADS_ACTOR, else agent)"),
) -> None:
    """Write ONE new market take to finance/market/takes/<date>-<HHMM>-take.md (create-only, never
    overwrites; the writer owns the frontmatter; no git — commit it yourself)."""
    import os
    import sys

    from harness.finance.config.settings import get_settings
    from harness.finance.takes import TakeError, announce, write_take

    body = file.read_text(encoding="utf-8") if file else sys.stdin.read()
    who = author or os.environ.get("BEADS_ACTOR") or "agent"
    try:
        written = write_take(body, tracker_path=get_settings().tracker_path, author=who,
                             session=session, tags=list(tag or []))
    except TakeError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    console.print(f"wrote {written.path} ({written.bytes} bytes, author {written.author}) — uncommitted")
    note = announce(written)
    if note:
        console.print(f"[yellow]  ⚠ {escape(note)}[/yellow]")
