"""MCP server adapter (FastMCP) — the Claude-native surface. Thin wrapper over FinanceService.

Tools are namespaced `finance_*` so they coexist cleanly when toolkits eventually consolidate under
one harness MCP surface. READ-ONLY — observation tools only; no order/trade tool exists here.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from harness.finance.service import FinanceService

mcp = FastMCP("harness-finance")


def _svc(feed: str = "iex") -> FinanceService:
    return FinanceService(feed=feed)


# Agent-facing news payloads are COMPACTED: a tool result has a size cap, and the full wire (every feed,
# every summary, every empty field) runs to megabytes. Summaries are trimmed, empty fields dropped, and
# every cut is reported (a `*_total` count + a note) so a caller knows there is more and how to narrow.
SUMMARY_CHARS = 240


def _compact(item: dict[str, Any], summary_chars: int = SUMMARY_CHARS) -> dict[str, Any]:
    out = {k: v for k, v in item.items() if v not in ("", None, [], {})}
    summary = out.get("summary")
    if isinstance(summary, str) and len(summary) > summary_chars:
        out["summary"] = summary[: summary_chars - 1].rstrip() + "…"
    out.pop("image_url", None)
    return out


@mcp.tool()
def finance_quote(symbols: list[str], feed: str = "iex") -> list[dict[str, Any]]:
    """Live quote(s) for one or more tickers — price + day change vs. previous close. A symbol the
    feed can't cover (mutual fund / OTC) comes back available=False with a note (never dropped).
    feed: 'iex' (free real-time) | 'sip' | 'delayed_sip'."""
    quotes = _svc(feed).quote([s.upper() for s in symbols])
    return [
        {**q.model_dump(), "day_change": q.day_change, "day_change_pct": q.day_change_pct}
        for q in quotes
    ]


@mcp.tool()
def finance_market(feed: str = "iex") -> dict[str, Any]:
    """Bird's-eye, point-in-time market read — indices + breadth, the 11 SPDR sectors, semis, and
    Mag7 dispersion (one Alpaca snapshots call). Returns grouped quotes (indices/sectors/semis/
    megacap), leaders/laggards, and deterministic breadth facts (sectors advancing/declining,
    equal-weight−cap gap = RSP−SPY, Mag7 avg/spread, semis avg). The breadth is FACTS, not a
    risk-on/off verdict — that interpretation is the agent's 'take'. READ-ONLY."""
    return _svc(feed).market().model_dump()


@mcp.tool()
def finance_correlate(
    symbols: list[str] | None = None, held: bool = False, limit: int = 20, days: int = 120,
    factor: list[str] | None = None, feed: str = "iex",
) -> dict[str, Any]:
    """Daily-return Pearson correlation across names — the 'is this a real diversifier?' facts:
    the matrix, annualized vol, and with `factor` (an equal-weight basket) each name's corr + beta
    to it plus the first name's divergence days. `held=True` uses the book (quotable holdings,
    largest cost basis first, up to `limit`) instead of `symbols`. Facts only; the reading is the
    agent's. READ-ONLY."""
    svc = _svc(feed)
    syms = svc.held_symbols()[:limit] if held else [s.upper() for s in symbols or []]
    fac = [s.upper() for s in factor] if factor else None
    return svc.correlate(syms, days=days, factor=fac).model_dump()


@mcp.tool()
def finance_fed() -> dict[str, Any]:
    """Latest FOMC decision from federalreserve.gov (keyless, Fed-direct): statement text +
    target-rate range + vote + the SEP/dot-plot LINK (not parsed — eyeball the dots). Lets a
    post-FOMC market read be confirmed rather than tape-inferred. The hawkish/dovish interpretation
    is the agent's; this surfaces the primary-source facts. READ-ONLY."""
    return _svc().fed().model_dump()


@mcp.tool()
def finance_history(
    symbol: str, start: str, end: str | None = None, timeframe: str = "1Day", feed: str = "iex"
) -> list[dict[str, Any]]:
    """Historical OHLCV bars (chart-able data) for a symbol. Dates YYYY-MM-DD; timeframe e.g.
    1Day/1Hour/1Week."""
    bars = _svc(feed).history(symbol.upper(), start=start, end=end, timeframe=timeframe)
    return [b.model_dump(by_alias=True) for b in bars]


@mcp.tool()
def finance_positions(feed: str = "iex") -> dict[str, Any]:
    """The full asset table across brokerages — live quotes where possible, last-known NAV for mutual
    funds, static balances for retirement/cash. Each row carries a `valuation` basis
    (live | last_known | static) + `as_of`. Returns a `net_worth` total + live/last-known/static
    breakdown. For a non-intraday fund's direction use finance_fund_proxy; for the by-account rollup use
    finance_networth. READ-ONLY observation, not a recommendation."""
    return _svc(feed).positions().model_dump()


@mcp.tool()
def finance_networth() -> dict[str, Any]:
    """Full-picture net worth across institutions (live brokerage + last-known mutual-fund NAV +
    static retirement/cash balances), grouped by account with per-group valuation basis + as-of.
    Static / last-known balances are manually synced — stale until refreshed from a current broker
    screenshot (do that at the start of finance work). Excludes assets not in the corpus (e.g. real
    estate, vehicles). READ-ONLY observation, not a recommendation."""
    return _svc().networth().model_dump()


@mcp.tool()
def finance_fund_proxy(feed: str = "iex") -> dict[str, Any]:
    """Estimate a mutual fund's end-of-day direction from a live proxy basket (a fund that doesn't
    price intraday). The fund + its proxies come from the `fund_proxy:` config block. Returns
    per-symbol moves + an equal-weight mean + which proxies were unavailable on the feed. Rough
    directional sign/magnitude ONLY (a cap-weighted fund won't match an equal-weight basket) — not a
    NAV prediction."""
    return _svc(feed).fund_proxy().model_dump()


@mcp.tool()
def finance_daygl(feed: str = "iex") -> dict[str, Any]:
    """Full-book intraday day G/L in one number, decomposed by valuation basis: live-quoted
    positions EXACT (cross-brokerage) + non-intraday funds ESTIMATED via the fund-proxy day% ×
    last-known NAV value + static balances flat by definition. The est sleeve is directional
    (partial proxy coverage — see est_coverage_pct); NAV truth arrives at the EOD sync. READ-ONLY
    observation, not a recommendation."""
    return _svc(feed).daygl().model_dump()


@mcp.tool()
def finance_resolve_cik(symbol: str) -> dict[str, Any]:
    """Resolve a ticker → SEC CIK (the permanent filer ID every EDGAR call needs) via the bundled
    company_tickers.json. Returns found/cik/title/source. A miss (found=False) means the symbol isn't
    a US XBRL filer (ETFs/mutual funds/ADRs) or is unknown — pass cik= to finance_fundamentals."""
    return _svc().resolve_cik(symbol.upper()).model_dump()


@mcp.tool()
def finance_fundamentals(symbol: str, cik: str | None = None, recent: int = 6) -> dict[str, Any]:
    """Reported GAAP/XBRL financials for a US SEC filer from EDGAR (keyless) — revenue, net income,
    operating income, gross profit, R&D, per fiscal period (quarter + annual), newest-first. Figures
    LAG real-time (newest = most recent 10-Q/10-K filing). A concept with no facts wasn't reported
    under the us-gaap tags tried (XBRL varies by issuer). `cik` overrides the ticker→CIK map for
    symbols it can't resolve. READ-ONLY observation — surfaces the numbers; the judgment is the user's."""
    return _svc().fundamentals(symbol.upper(), cik=cik, recent=recent).model_dump()


@mcp.tool()
def finance_filing(
    symbol: str,
    form: str = "8-K",
    cik: str | None = None,
    accession: str | None = None,
    doc: str | None = None,
    list_only: bool = False,
) -> dict[str, Any]:
    """Fetch + READ a filing's content from the EDGAR Archives — the earnings-print reader.
    Defaults to the newest 8-K's EX-99 press-release exhibit (headline numbers, guidance, KPI
    tables — on EDGAR within minutes of a print). `list_only` returns the filing's document
    inventory without content; `accession`/`doc` re-aim at older filings / specific exhibits.
    Requires HARNESS_SEC_CONTACT in the env (SEC fair-access UA; fails loud when unset).
    Honest boundary: call transcripts + un-filed IR slides are not SEC documents. READ-ONLY."""
    return (
        _svc()
        .filing(symbol.upper(), form=form, cik=cik, accession=accession, doc=doc, list_only=list_only)
        .model_dump()
    )


@mcp.tool()
def finance_multiples(symbol: str, cik: str | None = None, recent: int = 8) -> dict[str, Any]:
    """Valuation multiples for a US SEC filer from SEC EDGAR (keyless) + a LIVE Alpaca price:
    EV/EBITDA, P/E (= mktcap / net income TTM), P/S (= mktcap / revenue TTM). Returns the FULL
    auditable component breakdown — shares, price, market_cap, total debt, cash, enterprise_value,
    operating income (TTM), D&A (TTM), EBITDA (TTM), net income (TTM), revenue (TTM) — each traced to
    its resolving XBRL tag + TTM basis (4Q / annual / instant). GAAP-HONEST: unprofitable (EBITDA or
    net income ≤ 0) returns the multiple as the STRING "N/M" (not a misleading huge/negative number);
    a concept not reported under the tags tried returns "unavailable" with the missing piece named —
    never a fabricated figure. TTM = sum of the 4 most-recent discrete quarters (fallback: most-recent
    annual). Only the price is real-time; reported figures LAG (newest = most recent 10-Q/10-K). `cik`
    overrides the ticker→CIK map for symbols it can't resolve. READ-ONLY observation — surfaces the
    math; the judgment is the user's. Serves valuation research."""
    return _svc().multiples(symbol.upper(), cik=cik, recent=recent).model_dump()


@mcp.tool()
def finance_compare(symbols: list[str], recent: int = 8) -> dict[str, Any]:
    """Side-by-side valuation + live price + values-screen for a pick-set (2+ US SEC filers) — the
    deterministic data behind the Compare tab. Composes a batch quote (price + day move) + per-symbol
    EDGAR multiples (P/S, P/E, EV/EBITDA + market cap; graceful on a failed resolve) + the corpus
    values-screen (clean/excluded). Multiples keep the honesty strings ("N/M" unprofitable,
    "unavailable" un-tagged); a per-row note flags a failed resolve or the mega-cap P/S sanity-guard
    (the Q4-in-10-K TTM mis-tag). Reported figures LAG (newest 10-Q/10-K); only price/day move are live.
    The interpretive comparison (which wins, why) is the agent's separate written artifact, not this.
    READ-ONLY observation — surfaces the side-by-side; the judgment is the user's."""
    return _svc().compare([s.upper() for s in symbols], recent=recent).model_dump()


@mcp.tool()
def finance_news(symbols: list[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Keyless news scan for portfolio symbols (default: the corpus's stocks + ETFs): wire
    headlines (Yahoo per-ticker RSS) + a recent-SEC-filings rail (8-K = material event;
    10-Q/10-K = the real prints). READ-ONLY observation — the 'what hit this position today?'
    layer; per-feed errors are surfaced on the result, never as empty."""
    return [sn.model_dump() for sn in _svc().news(symbols=symbols, limit=limit)]


@mcp.tool()
def finance_wire(source: str | None = None, limit: int = 40, per_feed: int = 8) -> dict[str, Any]:
    """Broad-market news WIRE — config/feeds.yaml headlines (the configured feeds.yaml sources — market wires
    + geopolitics feeds + thesis-topic searches) aggregated NEWEST-FIRST across sources. The
    'what's the market narrative today?' layer for a market take. Distinct from finance_news
    (per-ticker Yahoo) and finance_watch (which dedupes the same feeds via the seen-cache): the wire
    is NEVER seen-filtered, so a repeat call sees the same items. READ-ONLY; per-feed failures land
    in `notes`, never a silent empty. `source` filters to one feed by substring of its name.

    `limit` caps the NEWEST items across all feeds (default 40); `per_feed` is how many each feed
    contributes before the cut. Items are compacted (summaries trimmed, empty fields dropped);
    `items_total` + a note report what the cap left out — narrow with `source` or read a story's
    body with finance_wire_read_match."""
    data = _svc().wire(source=source, limit=max(1, per_feed)).model_dump()
    items = data.get("items") or []
    data["items_total"] = len(items)
    data["items"] = [_compact(it) for it in items[: max(1, limit)]]
    if len(items) > len(data["items"]):
        data.setdefault("notes", []).append(
            f"showing the newest {len(data['items'])} of {len(items)} items — narrow with source=")
    return data


@mcp.tool()
def finance_wire_read(url: str) -> dict[str, Any]:
    """Read ONE article's body behind a wire headline (keyless). Extraction order: the publisher's
    JSON-LD articleBody → <article> paragraphs → page paragraphs → the dek. Google News links (the
    AP/Reuters/Bloomberg lanes) resolve to the publisher first. Returns title/site/published/body/
    method/words/partial/notes; a paywall is labeled partial, and a bot wall (401/403) raises with
    the reason — the wire's headline + dek is then the reachable read. READ-ONLY."""
    from dataclasses import asdict

    from harness.finance.providers.article_provider import read_article

    return asdict(read_article(url))


@mcp.tool()
def finance_research(symbol: str, months: int = 6, threshold: float = 3.0) -> str:
    """Event-anchored deep-dive for a symbol: ~N months of bars -> big-move days -> date-windowed
    headlines per move-day + per month + material filings + next-print estimate. Writes the
    catch-up artifact to finance/research/{SYM}/ and returns its path — read the artifact and
    synthesize the narrative arc for the user (the tool gathers; the agent narrates). Slow (~30s,
    self-paced keyless calls)."""
    from harness.finance.config.settings import get_settings
    from harness.finance.research import write_research_report

    bundle, bars = _svc().research(symbol.upper(), months=months, threshold=threshold)
    held = {h.symbol for h in _svc().reader.read_portfolio().holdings}
    out = write_research_report(bundle, bars, get_settings().tracker_path, held_symbols=held)
    return str(out)


@mcp.tool()
def finance_watch(mark_seen: bool = True, news_limit: int = 4) -> dict[str, Any]:
    """One-shot standing-watch digest: live day moves + rebalance-band drift
    (bands are illustrative until targets are set) + the wash-sale window (known vests only)
    + days-to-print estimates + ONLY-new headlines via the local seen-cache. READ-ONLY observation;
    set mark_seen=False to peek without consuming the news delta."""
    return _svc().watch(mark_seen=mark_seen, news_limit=news_limit).model_dump()


@mcp.tool()
def finance_watch_peek(news_limit: int = 4, max_news: int = 60) -> dict[str, Any]:
    """The standing-watch digest WITHOUT consuming the news delta (finance_watch with mark_seen fixed
    off — the market-take read of the book: day moves, bands, wash window, days-to-print, fresh
    headlines). A separate tool so a read-only agent can be granted it without the seen-cache write.
    `fresh_news` is the newest `max_news` items, compacted; `filtered_news` keeps symbol, title and
    the filter reason; `fresh_news_total` / `filtered_news_total` + a note report the cut."""
    data = _svc().watch(mark_seen=False, news_limit=news_limit).model_dump()
    fresh = sorted(data.get("fresh_news") or [], key=lambda it: it.get("published") or "", reverse=True)
    data["fresh_news_total"] = len(fresh)
    data["fresh_news"] = [_compact(it) for it in fresh[: max(0, max_news)]]
    filtered = data.get("filtered_news") or []
    data["filtered_news_total"] = len(filtered)
    data["filtered_news"] = [{k: it.get(k) for k in ("symbol", "title", "filtered") if it.get(k)}
                             for it in filtered]
    if len(fresh) > len(data["fresh_news"]):
        data.setdefault("notes", []).append(
            f"fresh_news: the newest {len(data['fresh_news'])} of {len(fresh)} — "
            "finance_news(symbols=[…]) reads one name's headlines")
    return data


@mcp.tool()
def finance_global(sections: list[str] | None = None) -> dict[str, Any]:
    """Follow-the-sun board — index futures (each row names its contract), rates + the Treasury cash
    curve, Asia/Europe, oil, metals, FX; each row says LIVE or CLOSED <session>. The overnight read
    the US tape can't show. `sections` narrows (futures/rates/asia/europe/oil/metals/fx). READ-ONLY."""
    from harness.finance.reads import global_payload

    return global_payload(sections)


@mcp.tool()
def finance_calendar(days: int = 7, show_all: bool = False) -> dict[str, Any]:
    """The macro release calendar from primary sources: releases with actual/consensus/previous once
    printed, FOMC decisions + minutes, Fed speakers, Treasury auctions. High tier by default;
    `show_all` for every release. Use it for any date/time claim — never a remembered date. READ-ONLY."""
    from harness.finance.reads import calendar_payload

    return calendar_payload(days, show_all=show_all)


@mcp.tool()
def finance_wire_read_match(match: str, max_chars: int = 6000) -> dict[str, Any]:
    """Read the article BODY behind the newest wire headline containing `match` (case-insensitive
    words from the headline). Only articles already on the configured feeds are reachable — it takes
    no URL. A paywall is labeled partial; a bot wall raises with the reason (the headline + dek is
    then the reachable read — never infer the body). READ-ONLY."""
    from harness.finance.reads import wire_read_match

    return wire_read_match(match, max_chars=max_chars)


@mcp.tool()
def finance_take_write(body: str, session: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    """WRITE one new market take to finance/market/takes/<date>-<HHMM>-take.md (local time now).
    Send the markdown BODY only (start with an H1 — one is added if missing); the writer adds the
    frontmatter (date, time, `session` label such as overnight-asia, `tags`, and the author, which
    the host sets). Create-only: an existing file is never overwritten — a second take in the same
    minute is refused. No git: the file is left uncommitted for you to review. Returns the path."""
    import os

    from harness.finance.config.settings import get_settings
    from harness.finance.takes import announce, write_take

    author = os.environ.get("HARNESS_TAKE_AUTHOR") or os.environ.get("BEADS_ACTOR") or "agent"
    written = write_take(body, tracker_path=get_settings().tracker_path, author=author,
                         session=session, tags=tags)
    out: dict[str, Any] = {"path": written.path, "author": written.author, "date": written.date,
                           "time": written.time, "bytes": written.bytes, "committed": False}
    note = announce(written)
    if note:
        out["notes"] = [note]
    return out


@mcp.tool()
def finance_gauges(symbol: str) -> dict[str, Any]:
    """Options-positioning gauges for one underlying — the broker-card read as data: put/call ratio
    (session volume + an open-interest ratio when the contracts roster is reachable), IV30 (median
    near-the-money implied vol, 20-45d contracts within ±10% of spot), HV30 (trailing realized vol,
    31 closes, sample std, annualized), and the IV−HV spread with a braced/neutral/complacent label (±8-pt
    thresholds). SENTIMENT THERMOMETERS, never advice — heavy puts can be fear or longs buying
    insurance; the tape can't distinguish, so the interpretation stays the agent's. Honest
    boundaries in `notes`: indicative (free) options feed; IV missing on illiquid strikes (coverage
    reported); short interest NOT available via Alpaca — broker card / FINRA is the source.
    READ-ONLY."""
    return _svc().gauges(symbol.upper()).model_dump()


@mcp.tool()
def finance_projections() -> dict[str, Any]:
    """Scenario-grid projections for every name in the agent-authored params registry
    (finance/reference/projection-params.yaml): per-horizon (6/12/24/36mo) price + return bands
    from forward-EPS × growth-band × multiple-band arithmetic against the live quote (ref_price
    fallback, source named). Horizons beyond 1y are sketch-tier; entries older than ~a quarter
    flag stale. SCENARIOS to reason against, never forecasts — the bands were authored at
    research time by the agent (or you). READ-ONLY."""
    from harness.finance.config.settings import get_settings
    from harness.finance.projections import build_contract, load_params

    path = get_settings().projections_path
    if path is None:
        return {"projections": [], "summary": {"total": 0, "held": 0, "stale": 0}}
    params = load_params(path)
    quotes: dict[str, float] = {}
    try:
        for q in _svc().quote([p.symbol for p in params]):
            if q.available and q.price is not None:
                quotes[q.symbol] = q.price
    except Exception:  # noqa: BLE001 — degrade to ref prices, never a dead tool
        pass
    holdings = {
        h.symbol: float(h.avg_cost)
        for h in _svc().reader.read_portfolio().holdings
        if h.avg_cost
    }
    return build_contract(params, quotes, holdings)


@mcp.tool()
def finance_holdings() -> dict[str, Any]:
    """Holdings appraisal — the held book through the projections lens (the inventory to
    finance_projections' shop): every held instrument valued from the positions snapshot and
    appraised against the params registry — item_level (the modeled 12-month mid-scenario return %,
    rarity-tiered as finance_projections scores it) from the live price + entry_item_level
    from the average cost ("how good was the buy, against the current model"), entry_edge the
    delta the entry banked. Unappraised names (no registry entry / fund structures) render with
    the reason named. Present-knowledge lens (params re-anchor per print), never a verdict.
    READ-ONLY."""
    from harness.finance.config.settings import get_settings
    from harness.finance.holdings import build_holdings_contract
    from harness.finance.projections import load_params

    path = get_settings().projections_path
    params = load_params(path) if path is not None else []
    snap = _svc().positions()
    rows = [
        {
            "symbol": pos.symbol,
            "name": pos.name,
            "account": pos.account,
            "valuation": pos.valuation,
            "shares": pos.shares,
            "avg_cost": pos.avg_cost,
            "cost_basis": pos.cost_basis,
            "price": pos.price,
            "market_value": pos.market_value,
        }
        for pos in snap.positions
        if pos.valuation in ("live", "last_known") and pos.shares > 0
    ]
    return build_holdings_contract(params, rows)


@mcp.tool()
def finance_screen(symbol: str) -> dict[str, Any]:
    """Check a ticker against the configured values screen (corpus-only, no network). Returns
    excluded (+category) or clean."""
    return _svc().screen(symbol).model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
