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
    screenshot (do that at the start of finance work). Excludes assets not in the corpus (real
    estate, vehicle). READ-ONLY observation, not a recommendation."""
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
    math; the judgment is the user's. Serves the screened-core engine + research profiles."""
    return _svc().multiples(symbol.upper(), cik=cik, recent=recent).model_dump()


@mcp.tool()
def finance_compare(symbols: list[str], recent: int = 8) -> dict[str, Any]:
    """Side-by-side valuation + live price + values-screen for a pick-set (2+ US SEC filers) — the
    deterministic data behind the Compare tab. Composes a batch quote (price + day move) + per-symbol
    EDGAR multiples (P/S, P/E, EV/EBITDA + market cap; graceful on a failed resolve) + the corpus
    values-screen (clean/excluded). Multiples keep the honesty strings ("N/M" unprofitable,
    "unavailable" un-tagged); a per-row note flags a failed resolve or the mega-cap P/S sanity-guard
    (TTM mis-tag). Reported figures LAG (newest 10-Q/10-K); only price/day move are live.
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
def finance_wire(source: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Broad-market news WIRE — config/feeds.yaml headlines (MarketWatch/CNBC/FT/AP/Bloomberg markets
    + Al Jazeera geopolitics + thesis topics) aggregated NEWEST-FIRST across sources. The
    'what's the market narrative today?' layer for a market take. Distinct from finance_news
    (per-ticker Yahoo) and finance_watch (which dedupes the same feeds via the seen-cache): the wire
    is NEVER seen-filtered, so it returns the FULL wire every call. READ-ONLY; per-feed failures land
    in `notes`, never a silent empty. `source` filters to one feed by substring of its name."""
    return _svc().wire(source=source, limit=limit).model_dump()


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
def finance_screen(symbol: str) -> dict[str, Any]:
    """Check a ticker against the configured values screen (corpus-only, no network). Returns
    excluded (+category) or clean."""
    return _svc().screen(symbol).model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
