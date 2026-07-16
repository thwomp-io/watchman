"""Single orchestration surface. MCP + CLI adapters both call here (parity is structural).

READ-ONLY: every method observes market data + the corpus. Nothing here places, schedules, or
recommends a trade — this is the sounding-board's eyes, not its hands on the order book.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness.finance.corpus.reader import PortfolioSeed
    from harness.finance.research import ResearchBundle
    from harness.finance.unwind import UnwindReport
    from harness.finance.watch import PulseReport, WatchDigest

from harness.finance.cik_resolver import CikResolver
from harness.finance.corpus.reader import CorpusReader
from harness.finance.models import (
    AnalystRatings,
    Bar,
    CikLookup,
    CompareReport,
    CorrelationReport,
    DayGL,
    DayGLRow,
    FomcDecision,
    Fundamentals,
    MarketOverview,
    Multiples,
    NetWorth,
    NetWorthGroup,
    NewsItem,
    PortfolioSnapshot,
    Position,
    PrintCountdown,
    ProxyComponent,
    ProxyEstimate,
    Quote,
    ScreenResult,
    SymbolNews,
    TrapMap,
    WireDigest,
)
from harness.finance.providers import get_fundamentals_provider, get_market_data_provider
from harness.finance.providers.base import FundamentalsProvider, MarketDataProvider, ProviderError
from harness.finance.providers.news_provider import fetch_recent_filings, fetch_yahoo_headlines


class FinanceService:
    def __init__(
        self,
        reader: CorpusReader | None = None,
        provider_name: str = "alpaca",
        feed: str = "iex",
        cik_resolver: CikResolver | None = None,
    ) -> None:
        self.reader = reader or CorpusReader()
        self._provider_name = provider_name
        self._feed = feed
        self._cik_resolver = cik_resolver or CikResolver()

    def _provider(self) -> MarketDataProvider:
        return get_market_data_provider(self._provider_name, feed=self._feed)

    def _fundamentals_provider(self, recent: int = 6) -> FundamentalsProvider:
        return get_fundamentals_provider("edgar", recent=recent)

    # ---- quotes ----
    def quote(self, symbols: list[str]) -> list[Quote]:
        return self._provider().get_quotes(symbols)

    # ---- bird's-eye market overview ----
    def market(self) -> MarketOverview:
        """Point-in-time market-regime read: indices + breadth + sector rotation + semis + mega-cap
        dispersion. ONE snapshots call covers the whole basket; `build_overview` does the pure
        deterministic compute. The interpretive 'take' is a separate agent artifact, never computed
        here (the dashboard reads finance/market/take.md, not a live model call)."""
        from harness.finance.market import all_symbols, build_overview

        return build_overview(self.quote(all_symbols()))

    # ---- the latest FOMC decision (federalreserve.gov RSS, keyless) ----
    def fed(self) -> FomcDecision:
        """The latest FOMC decision (statement text + target-rate range + vote + SEP link) from the
        Fed's own monetary-policy RSS — so a post-FOMC market take is CONFIRMED, not tape-inferred.
        Read-only; the hawkish/dovish interpretation stays the agent's."""
        from harness.finance.fed import fetch_fomc_decision

        return fetch_fomc_decision()

    # ---- CIK resolution: ticker → SEC CIK (the first hop before any EDGAR data call) ----
    def resolve_cik(self, symbol: str, *, cik: str | None = None) -> CikLookup:
        """Resolve a ticker to its SEC CIK via the bundled map. An explicit `cik` overrides the map
        (escape hatch for tickers SEC doesn't list under that symbol)."""
        sym = symbol.upper()
        if cik:
            return CikLookup(symbol=sym, cik=f"{int(cik):010d}", found=True, source="override")
        entry = self._cik_resolver.lookup(sym)
        if entry is None:
            return CikLookup(symbol=sym, found=False)
        return CikLookup(symbol=sym, cik=entry.cik, title=entry.title, found=True, source="map")

    # ---- fundamentals: reported GAAP/XBRL financials (SEC EDGAR, keyless) ----
    def fundamentals(self, symbol: str, *, cik: str | None = None, recent: int = 6) -> Fundamentals:
        lookup = self.resolve_cik(symbol, cik=cik)
        if not lookup.found or lookup.cik is None:
            raise ProviderError(
                f"could not resolve a CIK for {symbol.upper()} — it's not in SEC's ticker→CIK map "
                "(ETFs / mutual funds / foreign ADRs aren't US XBRL filers, so they won't be), or "
                "it's an unknown ticker. Pass --cik <number> if you have it."
            )
        return self._fundamentals_provider(recent).get_fundamentals(
            symbol, lookup.cik, entity_name=lookup.title
        )

    # ---- valuation multiples: EDGAR fundamentals + live price ----
    def multiples(
        self, symbol: str, *, cik: str | None = None, recent: int = 8, source: str = "auto"
    ) -> Multiples:
        """Valuation multiples (EV/EBITDA, P/E, P/S, + PEG on the FMP path), read-only, from one of
        two sources:

        - **fmp** — Financial Modeling Prep PRE-COMPUTED TTM ratios (free tier). Sidesteps the
          Q4-in-10-K TTM-assembly trap that mis-tags multiples on the EDGAR path,
          and covers foreign filers/ADRs (SONY/ASML) absent from SEC's CIK map. Needs FMP_API_KEY.
        - **edgar** — the keyless auditable-from-XBRL path: SEC reported figures + a LIVE Alpaca
          price, every figure traced to its tag + TTM basis. GAAP-honest ("N/M"/"unavailable"); the
          fallback when no FMP key, and the source for the component breakdown.

        `source="auto"` (default) prefers FMP when FMP_API_KEY is set, else EDGAR; if FMP is keyed
        but errors (no coverage / limit), auto falls back to EDGAR. `recent` ≥ 8 gives the EDGAR TTM
        assembly ≥ 4 discrete quarters. Read-only surface for the screened-core engine + profiles."""
        from harness.finance.config.settings import get_settings

        if source not in ("auto", "fmp", "edgar"):
            raise ValueError(f"source must be auto|fmp|edgar, got {source!r}")

        settings = get_settings()
        if source in ("auto", "fmp") and settings.has_fmp_key:
            from harness.finance.providers.fmp_provider import fetch_fmp_multiples

            try:
                return fetch_fmp_multiples(symbol.upper(), settings.fmp_api_key or "")
            except ProviderError:
                if source == "fmp":
                    raise
                # auto → fall through to the keyless EDGAR path
        elif source == "fmp":
            raise ProviderError(
                "--source fmp needs FMP_API_KEY (set it in ./.env). "
                "Omit --source (or use --source edgar) for the keyless EDGAR path."
            )

        from harness.finance.multiples import compute_multiples
        from harness.finance.providers.edgar_provider import EdgarProvider

        lookup = self.resolve_cik(symbol, cik=cik)
        if not lookup.found or lookup.cik is None:
            raise ProviderError(
                f"could not resolve a CIK for {symbol.upper()} — it's not in SEC's ticker→CIK map "
                "(ETFs / mutual funds / foreign ADRs aren't US XBRL filers, so they won't be), or "
                "it's an unknown ticker. Pass --cik <number> if you have it."
            )
        quotes = self.quote([symbol.upper()])
        quote = (
            quotes[0]
            if quotes
            else Quote(symbol=symbol.upper(), available=False, note="no quote returned")
        )
        provider = EdgarProvider(recent=recent)
        return compute_multiples(
            provider, symbol.upper(), lookup.cik, quote, entity_name=lookup.title
        )

    # ---- side-by-side comparison of a pick-set ----
    def compare(self, symbols: list[str], *, recent: int = 8) -> CompareReport:
        """Side-by-side valuation + price + screen across a selected pick-set — the deterministic data
        half of the Compare tab. Composes the existing surfaces: ONE batch `quote` (price + day move)
        + per-symbol `multiples` (valuation, graceful on a failed EDGAR resolve) + `screen` (corpus,
        no network). `build_compare` does the pure assembly. The interpretive comparison is a separate
        agent-written doc — never computed here (no model in the loop)."""
        from harness.finance.compare import build_compare
        from harness.finance.config.settings import get_settings
        from harness.finance.research import resolve_research_dir

        syms = [s.upper() for s in symbols]
        quotes = {q.symbol: q for q in self.quote(syms)} if syms else {}
        mults: dict[str, Multiples] = {}
        mult_errors: dict[str, str] = {}
        screens: dict[str, ScreenResult] = {}
        for sym in syms:
            try:
                mults[sym] = self.multiples(sym, recent=recent)
            except ProviderError as e:
                mult_errors[sym] = str(e).split(" — ")[0]  # the terse head, not the full hint
            screens[sym] = self.screen(sym)

        # research-dir deep-link anchors — resolved at the I/O edge (existence-checked), passed pure
        tracker = get_settings().tracker_path
        held = {h.symbol for h in self.reader.read_portfolio().holdings}
        research_dirs: dict[str, str] = {}
        for sym in syms:
            d = resolve_research_dir(sym, tracker, held)
            if d.is_dir():
                research_dirs[sym] = str(d.relative_to(tracker))
        return build_compare(syms, quotes, mults, screens, mult_errors, research_dirs)

    # ---- history (chart-able bars) ----
    def history(
        self, symbol: str, *, start: str, end: str | None = None, timeframe: str = "1Day"
    ) -> list[Bar]:
        return self._provider().get_bars(symbol, start=start, end=end, timeframe=timeframe)

    # ---- correlate (the "is this a real diversifier?" surface) ----
    def correlate(
        self,
        symbols: list[str],
        *,
        days: int,
        factor: list[str] | None = None,
        top_divergence: int = 8,
    ) -> CorrelationReport:
        """Gather daily closes for each symbol over the window, then the pure correlation math.

        Missing/empty symbols are dropped (noted), so one un-fetchable ticker doesn't sink the run."""
        from datetime import timedelta

        from harness.finance.correlate import build_correlation

        start = (date.today() - timedelta(days=days)).isoformat()
        closes: dict[str, dict[str, float]] = {}
        missing: list[str] = []
        for sym in symbols:
            try:
                bars = self.history(sym, start=start)
            except Exception:  # noqa: BLE001 — a single bad fetch shouldn't sink the matrix
                bars = []
            if not bars:
                missing.append(sym)
                continue
            closes[sym] = {b.t[:10]: b.c for b in bars}
        fac = [s for s in (factor or []) if s in closes] or None
        rep = build_correlation(closes, factor=fac, top_divergence=top_divergence)
        rep.days = days
        if missing:
            rep.notes.append("no bars for: " + ", ".join(missing))
        return rep

    # ---- positions: the full asset table — live quotes + last-known NAV + static balances ----
    def positions(self) -> PortfolioSnapshot:
        seed = self.reader.read_portfolio()
        quotable = [h for h in seed.holdings if h.quotable]
        quotes = {q.symbol: q for q in self.quote([h.symbol for h in quotable])} if quotable else {}

        snap = PortfolioSnapshot()
        for h in seed.holdings:
            pos = Position(
                symbol=h.symbol,
                name=h.name,
                account=h.account,
                asset_type=h.asset_type,
                shares=h.shares,
                avg_cost=h.avg_cost,
                cost_basis=h.cost_basis,
                quotable=h.quotable,
                valuation=h.valuation,
                as_of=h.as_of,
            )
            q = quotes.get(h.symbol)
            if h.valuation == "live" and q is not None and q.available and q.price is not None:
                pos.price = q.price
                pos.market_value = q.price * h.shares
                pos.unrealized_gl = pos.market_value - h.cost_basis
                if h.cost_basis:
                    pos.unrealized_gl_pct = pos.unrealized_gl / h.cost_basis * 100.0
                pos.day_change_pct = q.day_change_pct
                if q.day_change is not None:
                    pos.day_gl = q.day_change * h.shares
                snap.live_value += pos.market_value
                snap.quoted_cost_basis += h.cost_basis
                snap.quoted_unrealized_gl += pos.unrealized_gl
                if pos.day_gl is not None:
                    snap.quoted_day_gl += pos.day_gl
            elif h.valuation == "last_known" and h.last_price is not None:
                pos.price = h.last_price
                pos.market_value = h.last_price * h.shares
                pos.unrealized_gl = pos.market_value - h.cost_basis
                if h.cost_basis:
                    pos.unrealized_gl_pct = pos.unrealized_gl / h.cost_basis * 100.0
                pos.note = f"last-known NAV ({h.as_of or 'undated'}); `finance fund-proxy` for direction"
                snap.last_known_value += pos.market_value
            elif h.valuation == "static" and h.balance is not None:
                pos.market_value = h.balance
                pos.note = f"static balance ({h.as_of or 'undated'})"
                snap.static_value += pos.market_value
            else:
                # a live holding whose quote failed (feed gap) — surface it, don't fabricate a value
                pos.note = q.note if q is not None else "no valuation available"
            snap.positions.append(pos)

        snap.quoted_market_value = snap.live_value  # back-compat alias (live-quoted only)
        snap.net_worth = snap.live_value + snap.last_known_value + snap.static_value
        snap.notes.append(
            "Net worth = live quotes + last-known NAV + static balances. Last-known / static values "
            "are manually synced (see per-row as-of) — refresh from a current broker screenshot "
            "before high-stakes finance work. Read-only observation, not a recommendation."
        )
        return snap

    # ---- net worth: the full-picture rollup by account/institution ----
    def networth(self) -> NetWorth:
        snap = self.positions()
        groups: dict[str, NetWorthGroup] = {}
        seen_valuations: dict[str, set[str]] = {}
        for p in snap.positions:
            if p.market_value is None:
                continue
            g = groups.get(p.account)
            if g is None:
                g = NetWorthGroup(account=p.account)
                groups[p.account] = g
                seen_valuations[p.account] = set()
            g.value += p.market_value
            seen_valuations[p.account].add(p.valuation)
            # carry the earliest (most-stale) as-of in the group
            if p.as_of and (not g.as_of or p.as_of < g.as_of):
                g.as_of = p.as_of
        for acct, g in groups.items():
            vs = seen_valuations[acct]
            g.valuation = next(iter(vs)) if len(vs) == 1 else "mixed"
        nw = NetWorth(
            groups=sorted(groups.values(), key=lambda x: -x.value),
            total=snap.net_worth,
            live_value=snap.live_value,
            last_known_value=snap.last_known_value,
            static_value=snap.static_value,
        )
        nw.notes.append(
            "Full-picture net worth (brokerage + retirement + cash, across institutions). Static / "
            "last-known balances are manually synced — refresh from a current broker screenshot at "
            "the start of finance work. Excludes anything not in the corpus (e.g. real estate, vehicles)."
        )
        return nw

    # ---- net-worth-portrait viz data, DERIVED from portfolio.yaml ----
    # These replace the hand-authored concentration.json / allocation.json. The data contracts they
    # emit are the SAME shape the viz engine's `treemap`/`pie` renderers consume — so `--write` (the
    # CLI) renders them directly, and they're never stale by construction (a fresh portfolio.yaml sync
    # + one command reproduces them current). The deterministic-core half of the deterministic-core/
    # agent-periphery split: the *narrative* status strings + the strategic *Target* stay judgment
    # (the Target is config-stored in `allocation_target:`); everything here is a pure function of
    # holdings + live quotes.

    def concentration_treemap(self) -> dict[str, object]:
        """LIVE net-worth concentration treemap data (the `treemap` contract), derived from holdings.

        Grouping is the semantic risk taxonomy — but the two isolated-by-design groups are derived,
        NOT hardcoded tickers: the concentrated single-stock = the lotted holding (`seed.concentrated`),
        the single-theme fund = the proxied mutual fund (`seed.proxy.fund`). Everything else falls to
        diversified-brokerage / retirement / cash by asset type. So the risk-isolation that made the
        hand-authored treemap legible stays fully config-derived — never hardcoded tickers."""
        seed = self.reader.read_portfolio()
        snap = self.positions()
        concentrated = seed.concentrated.symbol if seed.concentrated else None
        fund = seed.proxy.fund or None

        def group_of(sym: str, asset_type: str) -> str:
            if sym == concentrated:
                return "concentrated"
            if sym == fund:
                return "fund"
            if asset_type == "retirement":
                return "retirement"
            if asset_type == "cash":
                return "cash"
            return "brokerage"

        labels = {
            "concentrated": f"{concentrated} — concentrated single-stock" if concentrated else "Concentrated",
            "fund": f"{fund} — single-theme fund" if fund else "Single-theme fund",
            "brokerage": "Diversified brokerage",
            "retirement": "Retirement (locked)",
            "cash": "Cash",
        }
        nodes: list[dict[str, object]] = []
        present: list[str] = []
        for p in snap.positions:
            if p.market_value is None:
                continue
            g = group_of(p.symbol, p.asset_type)
            if g not in present:
                present.append(g)
            nodes.append(
                {
                    "label": p.symbol,
                    "value": round(p.market_value, 2),
                    "group": g,
                    "detail": {
                        "shares": round(p.shares, 4),
                        "avg_cost": p.avg_cost,
                        "cost_basis": p.cost_basis,
                        "account": p.account,
                        "valuation": p.valuation,
                    },
                }
            )
        nodes.sort(key=lambda n: -float(n["value"]))  # type: ignore[arg-type]
        # Stable, risk-first group order (the isolated concentrations lead), filtered to those present.
        order = ["concentrated", "fund", "brokerage", "retirement", "cash"]
        groups = [{"key": k, "label": labels[k]} for k in order if k in present]
        return {
            "title": "Net-worth concentration — where the money actually is",
            "subtitle": (
                f"LIVE-derived {date.today().isoformat()} · "
                f"live ${snap.live_value:,.0f} + last-known ${snap.last_known_value:,.0f} + "
                f"static ${snap.static_value:,.0f} · net worth ${snap.net_worth:,.0f}"
            ),
            "groups": groups,
            "nodes": nodes,
        }

    def allocation_pie(self) -> dict[str, object]:
        """Current → Target allocation (the `pie` contract). The CURRENT pie is derived live — the
        liquid pool (every non-retirement holding, each its own slice). The TARGET pie is the ratified
        strategic plan, read from `allocation_target:` in portfolio.yaml (config-stored judgment, synced
        when the *strategy* changes — not per-fill). If no target is configured, only Current renders."""
        seed = self.reader.read_portfolio()
        snap = self.positions()
        liquid = [
            p for p in snap.positions if p.asset_type != "retirement" and p.market_value is not None
        ]
        liquid.sort(key=lambda p: -(p.market_value or 0.0))
        liquid_total = sum(p.market_value or 0.0 for p in liquid)
        current: dict[str, object] = {
            "label": "Current",
            "caption": f"~${liquid_total / 1000:,.0f}k liquid ({date.today().isoformat()}) · derived live",
            "slices": [{"label": p.symbol, "value": round(p.market_value or 0.0, 2)} for p in liquid],
        }
        pies: list[dict[str, object]] = [current]
        if seed.allocation_target and seed.allocation_target.slices:
            pies.append(
                {
                    "label": "Target",
                    "caption": seed.allocation_target.caption,
                    "slices": [
                        {"label": s.label, "value": s.value} for s in seed.allocation_target.slices
                    ],
                }
            )
        return {
            "title": "Allocation — current → target",
            "subtitle": "liquid pool · Current derived live from holdings · Target = ratified strategic plan",
            "pies": pies,
        }

    # ---- mutual-fund EOD-direction proxy (estimate a non-intraday fund from a live proxy basket) ----
    def fund_proxy(self) -> ProxyEstimate:
        basket = self.reader.read_portfolio().proxy
        weighted = bool(basket.weights)
        quotes = self.quote(basket.symbols)
        components: list[ProxyComponent] = []
        avail: list[tuple[float, float]] = []  # (move_pct, weight) — weight=1.0 in equal-weight mode
        missing: list[str] = []
        for q in quotes:
            w = basket.weights.get(q.symbol) if weighted else None
            if q.available and q.day_change_pct is not None:
                components.append(
                    ProxyComponent(
                        symbol=q.symbol,
                        available=True,
                        price=q.price,
                        prev_close=q.prev_close,
                        move_pct=q.day_change_pct,
                        weight=w,
                    )
                )
                avail.append((q.day_change_pct, w if (weighted and w is not None) else 1.0))
            else:
                components.append(ProxyComponent(symbol=q.symbol, available=False, weight=w))
                missing.append(q.symbol)

        weight_sum = sum(w for _, w in avail)
        estimate = sum(m * w for m, w in avail) / weight_sum if avail and weight_sum else None
        coverage = round(sum(basket.weights.values()), 2) if weighted else None
        live_coverage = round(sum(w for _, w in avail), 2) if weighted else None
        notes = [basket.note] if basket.note else []
        if missing:
            notes.append(
                f"Unavailable on the feed (OTC/ADR not covered): {', '.join(missing)}. "
                "Estimate uses the available names only."
            )
        if weighted:
            notes.append(
                f"CAP-WEIGHTED proxy (N-PORT weights): the basket represents "
                f"{coverage}% of the fund ({live_coverage}% quoted live this run). The unmapped "
                "tail is a disclosed blind spot — quiet-day sign flips remain possible when the "
                "tail moves against the basket; NAV truth is the EOD sync, never the proxy."
            )
        else:
            notes.append(
                "Equal-weight directional proxy ONLY — a cap-weighted fund won't match an equal-weight "
                "basket, so treat this as a rough sign-and-magnitude read, not a NAV prediction."
            )
        return ProxyEstimate(
            fund=basket.fund,
            components=components,
            estimate_pct=estimate,
            available_count=len(avail),
            missing=missing,
            notes=notes,
            weighted=weighted,
            coverage_pct=coverage,
            live_coverage_pct=live_coverage,
        )

    # ---- full-book intraday day G/L: exact + proxy-est + flat ----
    def daygl(self) -> DayGL:
        """One glanceable day-G/L number for the WHOLE book. Composition, by valuation basis:
        live-quoted positions exact (cross-brokerage) · non-intraday funds estimated via the
        fund-proxy day% × last-known NAV value (honestly labeled est) · statics flat by definition.
        The est sleeve reconciles at the EOD NAV sync; statics stay honest via the screenshot ritual."""
        snap = self.positions()

        # exact sleeve — live quotes; track per-account split + quote gaps honestly
        accounts: dict[str, float] = {}
        quoted = 0
        gaps: list[str] = []
        live_value = 0.0
        for p in snap.positions:
            if p.valuation != "live":
                continue
            if p.day_gl is not None:
                quoted += 1
                live_value += p.market_value or 0.0
                accounts[p.account] = accounts.get(p.account, 0.0) + p.day_gl
            else:
                gaps.append(p.symbol)

        rows: list[DayGLRow] = [
            DayGLRow(
                label="Quoted sleeve",
                kind="exact",
                value=live_value,
                day_gl=snap.quoted_day_gl,
                day_pct=(
                    snap.quoted_day_gl / (live_value - snap.quoted_day_gl) * 100.0
                    if live_value - snap.quoted_day_gl
                    else None
                ),
                detail=f"{quoted} live positions"
                + (f" · no quote: {', '.join(gaps)} (treated flat)" if gaps else ""),
            )
        ]

        # est sleeve — any last-known fund the proxy covers gets a proxy-estimated day move
        est_total: float | None = None
        est_coverage: float | None = None
        proxy: ProxyEstimate | None = None
        flat_value = snap.static_value
        for p in snap.positions:
            if p.valuation != "last_known" or not p.market_value:
                continue
            if proxy is None:
                try:
                    proxy = self.fund_proxy()
                except ProviderError:
                    proxy = ProxyEstimate(fund="")  # no proxy reachable — funds fall to flat below
            if proxy.fund == p.symbol and proxy.estimate_pct is not None:
                est = p.market_value * proxy.estimate_pct / 100.0
                est_total = (est_total or 0.0) + est
                est_coverage = proxy.live_coverage_pct or proxy.coverage_pct
                rows.append(
                    DayGLRow(
                        label=f"{p.symbol} (proxy est)",
                        kind="est",
                        value=p.market_value,
                        day_gl=est,
                        day_pct=proxy.estimate_pct,
                        detail=(
                            f"cap-weighted basket, {est_coverage or '?'}% of fund quoted"
                            if proxy.weighted
                            else "equal-weight basket (rough sign/magnitude only)"
                        ),
                    )
                )
            else:
                flat_value += p.market_value
                rows.append(
                    DayGLRow(
                        label=f"{p.symbol} (last-known NAV)",
                        kind="flat",
                        value=p.market_value,
                        detail="no proxy estimate — treated flat until the next NAV sync",
                    )
                )

        rows.append(
            DayGLRow(
                label="Statics (retirement + cash)",
                kind="flat",
                value=snap.static_value,
                detail="flat intraday by definition — manually synced balances",
            )
        )

        total = snap.quoted_day_gl + (est_total or 0.0) if (quoted or est_total is not None) else None
        prior = snap.net_worth - total if total is not None else None
        return DayGL(
            total_day_gl=total,
            total_day_pct=(total / prior * 100.0 if total is not None and prior else None),
            exact_day_gl=snap.quoted_day_gl,
            est_day_gl=est_total,
            est_coverage_pct=est_coverage,
            flat_value=flat_value,
            net_worth=snap.net_worth,
            quoted_positions=quoted,
            quote_gaps=gaps,
            accounts=accounts,
            rows=rows,
            notes=[
                "Full-book day G/L: quoted sleeve exact + fund sleeve proxy-ESTIMATED + statics flat. "
                "The est is directional (proxy coverage is partial) — NAV truth arrives at the EOD "
                "sync, and static balances stay honest via the screenshot reconciliation ritual.",
                "Read-only observation, not a recommendation.",
            ],
        )

    # ---- keyless news scan (per-ticker wire + filings rail) ----
    def news(self, symbols: list[str] | None = None, limit: int = 5) -> list[SymbolNews]:
        """Keyless news scan: wire headlines (Yahoo per-ticker RSS) + the EDGAR filings rail.

        Default symbols = the portfolio's stocks + ETFs (mutual funds skipped honestly — thin
        ticker feeds; non-intraday funds use `fund-proxy`). Per-feed failures stay loud on the
        result (never rendered as a false-empty)."""
        if not symbols:
            seed = self.reader.read_portfolio()
            symbols = [h.symbol for h in seed.holdings if h.asset_type in {"stock", "etf"}]
        out: list[SymbolNews] = []
        for sym in symbols:
            sym = sym.upper()
            sn = SymbolNews(symbol=sym)
            try:
                sn.headlines = fetch_yahoo_headlines(sym, limit=limit)
            except ProviderError as e:
                sn.headline_error = str(e)
            cik = self._cik_resolver.cik_for(sym)
            if cik:
                try:
                    sn.filings = fetch_recent_filings(cik, limit=limit)
                except ProviderError as e:
                    sn.filings_note = f"filings fetch failed: {e}"
            else:
                sn.filings_note = "no CIK (ETF/fund — not an SEC filer)"
            out.append(sn)
        return out

    def wire(self, *, source: str | None = None, limit: int = 8) -> WireDigest:
        """Broad-market news wire — the `feeds.yaml` source feeds aggregated NEWEST-FIRST across
        sources (the `wire` verb / `finance_wire` MCP tool). The 'what's the market narrative today?'
        layer for a `market take`.

        Composes the two existing primitives — `_load_feeds()` (the feeds.yaml loader, also feeding
        watch/pulse) + `fetch_rss()` (the generic RSS path, also used by Yahoo per-ticker) — so it
        inherits their already-debugged behavior; the only new logic is aggregate + sort + filter.
        Crucially it does NOT touch the seen-cache (unlike `news`/`watch`, which dedupe), so it
        returns the FULL wire on every call — read the whole market narrative, not just the delta.
        `source` filters to one feed by case-insensitive substring of its name (e.g. 'ft',
        'bloomberg', 'aljazeera'). Per-feed failures degrade to `notes` (one dead feed is never a dead
        run — the standing-agent doctrine the RSS path already enforces). Read-only."""
        from harness.finance.providers.news_provider import fetch_rss

        feeds = _load_feeds()
        if source:
            needle = source.lower()
            feeds = [f for f in feeds if needle in f["name"].lower()]
        relevance = _build_relevance_matcher(self.reader.read_portfolio())  # built once, not per item
        digest = WireDigest()
        for feed in feeds:
            try:
                items = fetch_rss(feed["url"], feed["name"], limit=limit)
                for it in items:  # tag each item with its feed category + any held/watchlist hits
                    it.category = feed.get("category", "markets")
                    it.holdings_hit = relevance(it.title)
                digest.items.extend(items)
                digest.sources_read.append(feed["name"])
            except ProviderError as e:
                digest.notes.append(f"feed {feed['name']} failed: {e}")
        # newest-first. `published` is "YYYY-MM-DD HH:MM" (UTC-ish) → lexical sort == chronological;
        # unparseable/blank dates sort last (treated as "" → oldest), never dropped.
        digest.items.sort(key=lambda it: it.published or "", reverse=True)
        if source and not feeds:
            digest.notes.append(f"no feed name matches source '{source}'")
        return digest

    def research(
        self, symbol: str, months: int = 6, threshold: float = 3.0
    ) -> tuple[ResearchBundle, list[Bar]]:
        """Gather the event-anchored deep-dive bundle. Mechanical; the agent
        synthesizes on top. Google calls self-paced inside the fetcher."""
        from datetime import date, timedelta

        from harness.finance.research import (
            ResearchBundle,
            _month_starts,
            detect_move_days,
            estimate_next_print,
            fetch_google_news,
        )

        sym = symbol.upper()
        end = date.today()
        start = end - timedelta(days=months * 30)
        bars = self.history(sym, start=start.isoformat(), timeframe="1Day")

        entry = self._cik_resolver.lookup(sym)
        cik = entry.cik if entry else None
        title = entry.title if entry else ""
        query = f'"{title.split(",")[0].title()}"' if title else f'"{sym}" stock'

        bundle = ResearchBundle(
            symbol=sym, query=query, start=start.isoformat(), end=end.isoformat()
        )
        if bars:
            bundle.first_close, bundle.last_close = bars[0].c, bars[-1].c
            if bars[0].c:
                bundle.window_pct = (bars[-1].c - bars[0].c) / bars[0].c * 100.0
        bundle.move_days = detect_move_days(bars, threshold=threshold)
        for m in bundle.move_days:
            d = date.fromisoformat(m.day)
            try:
                m.headlines = fetch_google_news(
                    query,
                    (d - timedelta(days=1)).isoformat(),
                    (d + timedelta(days=2)).isoformat(),
                    limit=3,
                    symbol=sym,
                )
            except ProviderError as e:
                bundle.notes.append(f"{m.day} news fetch failed: {e}")
        from harness.finance.research import MonthSlice

        for ms_start in _month_starts(start, end):
            nxt = date(ms_start.year + (ms_start.month == 12), (ms_start.month % 12) + 1, 1)
            sl = MonthSlice(month=ms_start.strftime("%Y-%m"))
            try:
                sl.headlines = fetch_google_news(
                    query, ms_start.isoformat(), min(nxt, end).isoformat(), limit=3, symbol=sym
                )
            except ProviderError as e:
                bundle.notes.append(f"{sl.month} news fetch failed: {e}")
            bundle.months.append(sl)
        if cik:
            try:
                all_filings = fetch_recent_filings(cik, limit=14)
                bundle.filings = [f for f in all_filings if f.filed >= start.isoformat()]
                bundle.next_print_estimate = estimate_next_print(all_filings)
            except ProviderError as e:
                bundle.notes.append(f"filings fetch failed: {e}")
        else:
            bundle.notes.append("no CIK — filings rail + print estimate unavailable")
        try:
            bundle.current = fetch_yahoo_headlines(sym, limit=5)
        except ProviderError as e:
            bundle.notes.append(f"current wire failed: {e}")
        return bundle, bars

    def watch(self, *, mark_seen: bool = True, news_limit: int = 4, ticker_news: bool = True) -> WatchDigest:
        """One-shot standing-watch digest: day moves + band drift + wash-sale
        window + days-to-print + seen-cache-filtered fresh headlines. Read-only; cron-able later.

        `ticker_news` adds a per-HELD-stock Google News search (single-name catalysts beyond Yahoo's
        per-ticker feed); held-scoped to bound the added serial fetches. Set False as the escape
        hatch if a run ever feels slow (the `--no-ticker-news` CLI flag)."""
        from datetime import date as _date

        from harness.finance.watch import (
            SeenCache,
            WatchDigest,
            WatchlistMove,
            check_drift,
            wash_sale_status,
        )

        seed = self.reader.read_portfolio()
        snap = self.positions()
        digest = WatchDigest(as_of=_date.today().isoformat())

        # day moves — live-quoted positions, biggest |day move| first
        live = [p for p in snap.positions if p.valuation == "live" and p.day_change_pct is not None]
        digest.day_moves = sorted(live, key=lambda p: abs(p.day_change_pct or 0), reverse=True)

        # watchlist day reads — non-held symbols the user keeps tabs on. Symbols off
        # the IEX feed (e.g. OTC ADRs) come back available=False — stated honestly; news-covered below.
        if seed.watchlist:
            notes_by_sym = {w.symbol: w.note for w in seed.watchlist}
            try:
                wl_quotes = self.quote([w.symbol for w in seed.watchlist])
            except ProviderError as e:
                digest.notes.append(f"watchlist quotes failed: {e}")
                wl_quotes = []
            moves: list[WatchlistMove] = []
            for q in wl_quotes:
                day_pct: float | None = None
                if q.available and q.price is not None and q.prev_close:
                    day_pct = (q.price - q.prev_close) / q.prev_close * 100.0
                moves.append(
                    WatchlistMove(
                        symbol=q.symbol,
                        note=notes_by_sym.get(q.symbol, ""),
                        available=q.available,
                        price=q.price,
                        day_change_pct=round(day_pct, 2) if day_pct is not None else None,
                    )
                )
            digest.watchlist_moves = sorted(
                moves, key=lambda m: abs(m.day_change_pct or 0), reverse=True
            )

        # band drift — % of the holding's OWN account (bands mean "% of a given brokerage", so a
        # position held at a SEPARATE broker must not dilute that denominator; static balances excluded)
        if seed.rebalance_bands:
            marked = [
                p
                for p in snap.positions
                if p.market_value is not None and p.valuation in {"live", "last_known"}
            ]
            acct_totals: dict[str, float] = {}
            for p in marked:
                acct_totals[p.account] = acct_totals.get(p.account, 0.0) + (p.market_value or 0.0)
            pct = {
                p.symbol: (p.market_value or 0.0) / acct_totals[p.account] * 100.0
                for p in marked
                if acct_totals.get(p.account)
            }
            digest.drift = check_drift(pct, seed.rebalance_bands)
            digest.notes.append(
                "Bands are illustrative until real targets are set in portfolio.yaml; "
                "drift % is of the holding's own brokerage account, not net worth."
            )

        # concentrated-holding wash-sale window — vest dates manual-synced from a vesting calendar
        if seed.vest_dates:
            digest.wash_sale = wash_sale_status(seed.vest_dates)
            # lot-aware enrichment: size the harvestable-loss inventory from the
            # per-lot basis + the live price, so the digest reports "$X harvestable" not just
            # "poisoned/clean". Only when the concentrated holding carries lots.
            from harness.finance.unwind import classify_lots

            conc = next((h for h in seed.holdings if h.lots), None)
            conc_pos = (
                next((p for p in snap.positions if p.symbol == conc.symbol), None) if conc else None
            )
            if conc and conc_pos and conc_pos.price:
                loss_lots = [
                    lt
                    for lt in classify_lots(
                        conc.lots, conc_pos.price, poisoned=digest.wash_sale.today_poisoned,
                        today=_date.today(),
                    )
                    if lt.klass == "loss"
                ]
                digest.wash_sale.harvestable_loss = round(
                    sum(lt.unrealized_gl for lt in loss_lots), 2
                )
                digest.wash_sale.harvestable_shares = round(
                    sum(lt.qty for lt in loss_lots), 4
                )

        # days-to-print per holding — CONFIRMED date first (nasdaq analyst API through the
        # day-TTL cache), honest filing-cadence estimate as the fallback. The label is the
        # contract: "= … (confirmed · nasdaq)" vs "≈ … (est. …)" — a consumer can always tell an
        # announcement from a projection (the month-off filing-cadence estimate error this closes).
        today = _date.today()
        from harness.finance.providers.nasdaq_provider import fetch_earnings_date
        from harness.finance.watch import EarningsDateCache

        earnings_cache = EarningsDateCache.load()
        earnings_dirty = False
        for h in seed.holdings:
            if h.asset_type != "stock":
                continue
            ed = earnings_cache.fresh(h.symbol, today)
            if ed is None:
                try:
                    ed = fetch_earnings_date(h.symbol)
                    earnings_cache.put(ed, today)
                    earnings_dirty = True
                except ProviderError:
                    ed = None  # honest miss (ETF-shaped / unknown) → cadence fallback below
            if ed is not None and ed.confirmed:
                digest.prints.append(
                    PrintCountdown(
                        symbol=h.symbol,
                        estimate=f"= {ed.report_date.isoformat()} (confirmed · nasdaq)",
                        days_out=(ed.report_date - today).days,
                        confirmed=True,
                    )
                )
                continue
            # Fallback: the EDGAR filing-cadence estimate (or nasdaq's own algo date — same
            # class of projection; prefer our auditable-from-facts one and label it est.).
            cik = self._cik_resolver.cik_for(h.symbol)
            if not cik:
                continue
            try:
                from harness.finance.research import estimate_next_print

                est = estimate_next_print(fetch_recent_filings(cik, limit=14))
            except ProviderError as e:
                digest.notes.append(f"{h.symbol} print estimate failed: {e}")
                continue
            if not est:
                continue
            days: int | None = None
            try:
                days = (_date.fromisoformat(est.split()[1]) - today).days
            except (ValueError, IndexError):
                pass
            digest.prints.append(PrintCountdown(symbol=h.symbol, estimate=est, days_out=days))
        if earnings_dirty:
            earnings_cache.save()

        # ratings wire — consensus PT/mix per held stock, TTL-gated (≈one sweep/day against the
        # unofficial API), diffed vs the stored baseline. A material move becomes a synthetic
        # [RATING] NewsItem: it rides the existing fresh-news rendering AND the catalyst→bus path
        # (symbol-tagged info event) for free. First sight seeds the baseline silently.
        from harness.finance.providers.nasdaq_provider import fetch_price_target
        from harness.finance.watch import ConsensusState

        consensus = ConsensusState.load()
        consensus_dirty = False
        for h in seed.holdings:
            if h.asset_type != "stock" or not consensus.stale(h.symbol, today):
                continue
            try:
                pt = fetch_price_target(h.symbol)
            except ProviderError:
                continue  # honest miss — no consensus coverage; never a fabricated baseline
            change = consensus.diff(pt)
            if change:
                digest.fresh_news.append(
                    NewsItem(
                        symbol=h.symbol,
                        title=f"[RATING] {h.symbol}: {change}",
                        # Synthetic per-change key → the seen-cache dedupes it like any headline.
                        url=f"consensus://{h.symbol}/{today.isoformat()}/{pt.mean:.2f}",
                        source="nasdaq-consensus",
                        published=today.isoformat(),
                    )
                )
            consensus.put(pt, today)
            consensus_dirty = True
        if consensus_dirty:
            consensus.save()

        # fresh headlines — the news scan minus everything already seen. Coverage = held
        # stocks/ETFs + the watchlist (Yahoo RSS covers OTC ADRs the quote feed can't).
        news_syms = [h.symbol for h in seed.holdings if h.asset_type in {"stock", "etf"}]
        news_syms += [w.symbol for w in seed.watchlist if w.symbol not in news_syms]
        cache = SeenCache.load()
        for sn in self.news(symbols=news_syms, limit=news_limit):
            if sn.headline_error:
                digest.notes.append(f"{sn.symbol} headlines failed: {sn.headline_error}")
            digest.fresh_news.extend(cache.filter_new(sn.headlines))

        # custom feeds: feeds.yaml wires + thesis topics + per-holding EDGAR 8-K Atom
        from harness.finance.providers.news_provider import (
            fetch_edgar_filing_feed,
            fetch_gnews_ticker,
            fetch_rss,
        )

        for feed in _load_feeds():
            try:
                digest.fresh_news.extend(
                    cache.filter_new(fetch_rss(feed["url"], feed["name"], limit=news_limit))
                )
            except ProviderError as e:
                digest.notes.append(f"feed {feed['name']} failed: {e}")
        for h in seed.holdings:
            if h.asset_type != "stock":
                continue
            cik = self._cik_resolver.cik_for(h.symbol)
            if not cik:
                continue
            try:
                digest.fresh_news.extend(
                    cache.filter_new(fetch_edgar_filing_feed(cik, h.symbol, limit=3))
                )
            except ProviderError as e:
                digest.notes.append(f"EDGAR feed {h.symbol} failed: {e}")

        # per-ticker single-name catalysts — Google News search, HELD stocks only. The
        # breadth layer beyond Yahoo: catches single-name catalysts a per-ticker feed misses — the kind a
        # broker console's news-event marker surfaces that a plain RSS wire can lag. Held-scoped to bound
        # the added serial fetches;
        # `ticker_news=False` is the escape hatch if a run feels slow. One dead query never sinks the run.
        if ticker_news:
            for h in seed.holdings:
                if h.asset_type != "stock":
                    continue
                try:
                    digest.fresh_news.extend(
                        cache.filter_new(fetch_gnews_ticker(h.symbol, limit=news_limit))
                    )
                except ProviderError as e:
                    digest.notes.append(f"gnews {h.symbol} failed: {e}")
        if mark_seen and digest.fresh_news:
            cache.mark(digest.fresh_news)
            cache.save()
        digest.notes.append(
            "Read-only observation, not advice. Wash-sale math covers KNOWN vests only — "
            "confirm the forward schedule with your broker before acting on a harvest window."
        )
        return digest

    def unwind(self, *, symbol: str | None = None, days: int = 120) -> UnwindReport:
        """The concentration-unwind data contract — the deterministic source for the
        unwind sell-planning dashboard. Composes per-lot live gain/loss + harvestability, the vest
        calendar, the wash-sale windows, and the holding's price + bars + support levels into one JSON.
        `symbol` defaults to the concentrated (lotted) holding from config. Read-only."""
        from datetime import date as _date
        from datetime import timedelta

        from harness.finance.levels import support_levels
        from harness.finance.unwind import build_unwind
        from harness.finance.watch import wash_sale_status

        seed = self.reader.read_portfolio()
        # default to the config's concentrated holding (the one with per-lot basis)
        if symbol is None:
            conc = next((h for h in seed.holdings if h.lots), None)
            if conc is None:
                raise ProviderError(
                    "no concentrated holding to unwind — mark a holding with per-lot basis (`lots:`) "
                    "in portfolio.yaml, or pass --symbol."
                )
            symbol = conc.symbol
        sym = symbol.upper()
        holding = next((h for h in seed.holdings if h.symbol == sym), None)
        if holding is None or not holding.lots:
            raise ProviderError(
                f"{sym} has no per-lot basis in portfolio.yaml (`lots:`); the unwind contract needs it."
            )

        quotes = self.quote([sym])
        q = quotes[0] if quotes else None
        if q is None or q.price is None:
            raise ProviderError(f"no live quote for {sym}")
        price = q.price
        day_pct = (
            round((price - q.prev_close) / q.prev_close * 100.0, 2)
            if q.prev_close
            else None
        )

        start = (_date.today() - timedelta(days=days)).isoformat()
        bars = self.history(sym, start=start)
        levels = support_levels(bars) if bars else []

        # %-complete: only when the seed carries the structured sold-ledger.
        # Liquid pool = every non-retirement position's market value (the allocation_pie basis) —
        # one extra positions() pass, acceptable at the dashboard's market10m cadence.
        progress = None
        if seed.unwind_sold:
            from harness.finance.unwind import build_progress

            shares_current = sum(lt.qty for lt in holding.lots)
            liquid_total = None
            try:
                snap = self.positions()
                liquid_total = sum(
                    p.market_value or 0.0
                    for p in snap.positions
                    if p.asset_type != "retirement" and p.market_value is not None
                )
            except ProviderError:
                pass  # progress still computes; pct_of_liquid stays None
            progress = build_progress(
                shares_current=shares_current,
                sold=seed.unwind_sold,
                market_value=shares_current * price,
                liquid_total=liquid_total,
                meta=seed.unwind_meta,
            )

        report: UnwindReport = build_unwind(
            symbol=sym,
            price=price,
            prev_close=q.prev_close,
            day_change_pct=day_pct,
            lots=holding.lots,
            vest_calendar=seed.vest_calendar,
            wash_sale=wash_sale_status(seed.vest_dates),
            support_levels=levels,
            bars=bars,
            today=_date.today(),
            progress=progress,
        )
        return report

    def trap_map(self, *, days: int = 90) -> TrapMap:
        """The trap-map — every symbol with resting GTC orders as a vertical price
        ladder: live price + rungs (with distance-to-fill, pulse's formula) + bars-derived support
        shelves. Pure composition of existing primitives (open_orders ledger · quote · history ·
        support_levels); read-only. One bars call per laddered symbol — fine at the dashboard's
        market10m/manual cadence. An unquotable symbol renders honestly (rungs, no price); a bars
        failure degrades to a note, never a dead map."""
        from datetime import date as _date
        from datetime import datetime, timedelta

        from harness.finance.corpus.reader import OpenOrder
        from harness.finance.levels import support_levels
        from harness.finance.models import SymbolLadder, TrapRung

        seed = self.reader.read_portfolio()
        as_of = datetime.now().isoformat(timespec="seconds")
        if not seed.open_orders:
            return TrapMap(as_of=as_of, notes=["no resting orders — the slate is empty"])

        by_sym: dict[str, list[OpenOrder]] = {}
        for o in seed.open_orders:
            by_sym.setdefault(o.symbol, []).append(o)

        try:
            quotes = {q.symbol: q for q in self.quote(sorted(by_sym))}
        except ProviderError:
            quotes = {}

        start = (_date.today() - timedelta(days=days)).isoformat()
        notes: list[str] = []
        ladders: list[SymbolLadder] = []
        committed = 0.0
        for sym in sorted(by_sym):
            q = quotes.get(sym)
            price = q.price if q else None
            prev = q.prev_close if q and q.prev_close else None
            day_pct = (
                round((price - prev) / prev * 100.0, 2) if price and prev else None
            )
            try:
                bars = self.history(sym, start=start)
            except ProviderError:
                bars = []
                notes.append(f"{sym}: no bars — support shelves omitted")
            levels = support_levels(bars) if bars else []

            rungs: list[TrapRung] = []
            for o in by_sym[sym]:
                dist: float | None = None
                if price:
                    raw = (price - o.limit) / price * 100.0
                    dist = round(raw if o.side == "buy" else -raw, 2)
                value = round(o.qty * o.limit, 2)
                if o.side == "buy":
                    committed += value
                rungs.append(TrapRung(
                    side=o.side, qty=o.qty, limit=o.limit, value=value,
                    distance_pct=dist, expires=o.expires, note=o.note,
                ))

            anchors = [r.limit for r in rungs] + [lv.level for lv in levels]
            if price:
                anchors.append(price)
            if prev:
                anchors.append(prev)
            lo, hi = min(anchors), max(anchors)
            pad = max((hi - lo) * 0.06, hi * 0.01)  # breathe past the extremes
            ladders.append(SymbolLadder(
                symbol=sym, price=price, prev_close=prev, day_change_pct=day_pct,
                rungs=sorted(rungs, key=lambda r: -r.limit), supports=levels,
                lo=round(lo - pad, 2), hi=round(hi + pad, 2),
            ))
            if price is None:
                notes.append(f"{sym}: unquotable on this feed — ladder renders without a live price")

        return TrapMap(
            as_of=as_of, symbols=ladders, committed=round(committed, 2), notes=notes,
        )

    def pulse(self, *, mark_seen: bool = True) -> PulseReport:
        """The scheduled-agent pulse: the watch digest + open-order trap distances +
        DETERMINISTIC flags (thresholds from portfolio.yaml `pulse:`). quiet=True means a cron run
        ends silently; flags mean notify. Detection lives here; the model only narrates."""
        from harness.finance.watch import OpenOrderStatus, PulseFlag, PulseReport

        digest = self.watch(mark_seen=mark_seen)
        seed = self.reader.read_portfolio()
        th = seed.pulse_thresholds
        day_move_pct = th.get("day_move_pct", 5.0)
        trap_pct = th.get("trap_proximity_pct", 1.0)
        print_days = th.get("print_days", 3.0)

        flags: list[PulseFlag] = []
        for pos in digest.day_moves:
            if pos.day_change_pct is not None and abs(pos.day_change_pct) >= day_move_pct:
                flags.append(PulseFlag(
                    kind="day_move", symbol=pos.symbol,
                    message=f"{pos.symbol} {pos.day_change_pct:+.1f}% on the day "
                            f"(threshold ±{day_move_pct:g}%)",
                ))

        orders: list[OpenOrderStatus] = []
        if seed.open_orders:
            try:
                quotes = {q.symbol: q for q in self.quote([o.symbol for o in seed.open_orders])}
            except ProviderError:
                quotes = {}
            for o in seed.open_orders:
                q = quotes.get(o.symbol)
                price = q.price if q else None
                dist: float | None = None
                if price:
                    raw = (price - o.limit) / price * 100.0
                    dist = raw if o.side == "buy" else -raw
                day_pct = round(q.day_change_pct, 2) if q and q.day_change_pct is not None else None
                orders.append(OpenOrderStatus(
                    symbol=o.symbol, side=o.side, qty=o.qty, limit=o.limit,
                    price=price, distance_pct=dist, day_pct=day_pct, expires=o.expires, note=o.note,
                ))
                if dist is not None and dist <= trap_pct:
                    flags.append(PulseFlag(
                        kind="trap_proximity", symbol=o.symbol,
                        message=f"{o.side.upper()} {o.symbol} @ {o.limit:g} is {dist:.1f}% from "
                                f"the live price ({price:.2f}) — fill plausible",
                    ))

        for pc in digest.prints:
            if pc.days_out is not None and pc.days_out <= print_days:
                flags.append(PulseFlag(
                    kind="print_soon", symbol=pc.symbol,
                    message=f"{pc.symbol} print ≈ {pc.days_out}d out ({pc.estimate})",
                ))

        # index-level sense: the tape itself — a broad index move can signal a
        # regime shift the per-holding checks miss
        index_move_pct = th.get("index_move_pct", 1.5)
        indexes: list[Quote] = []
        if seed.index_watch:
            try:
                indexes = self.quote(seed.index_watch)
            except ProviderError:
                indexes = []
            for q in indexes:
                if q.day_change_pct is not None and abs(q.day_change_pct) >= index_move_pct:
                    flags.append(PulseFlag(
                        kind="index_move", symbol=q.symbol,
                        message=f"{q.symbol} {q.day_change_pct:+.1f}% — index-level move "
                                f"(threshold ±{index_move_pct:g}%)",
                    ))

        # macro calendar proximity
        from datetime import date as _date

        macro_days = th.get("macro_days", 2.0)
        today = _date.today()
        for ev in seed.macro_events:
            try:
                d = (_date.fromisoformat(ev.date) - today).days
            except ValueError:
                continue
            if 0 <= d <= macro_days:
                when = "TODAY" if d == 0 else f"in {d}d"
                flags.append(PulseFlag(
                    kind="macro_soon", symbol="MACRO",
                    message=f"{ev.label} {when} ({ev.date})",
                ))

        # fed_decision: on an FOMC day, once the statement is OUT, surface the ACTUAL
        # decision (confirmed, not just "event today"). The released-date guard keeps it silent until
        # the statement drops (pre-2pm the feed's top item is the prior meeting → released != today),
        # and the once-per-day dedup fires it once. Only fetches on an FOMC day (no daily Fed hit);
        # network-guarded — a dead Fed RSS is a skip, never a dead pulse run (standing-agent doctrine).
        def _is_today(iso: str) -> bool:
            try:
                return _date.fromisoformat(iso) == today
            except ValueError:
                return False

        if any("fomc" in ev.label.lower() and _is_today(ev.date) for ev in seed.macro_events):
            try:
                from harness.finance.fed import fetch_fomc_decision

                decision = fetch_fomc_decision()
            except ProviderError:
                decision = None
            if decision and decision.released[:10] == today.isoformat():
                rate = (
                    f"target range {decision.target_rate}"
                    if decision.target_rate
                    else "see statement"
                )
                vote = f", vote {decision.vote}" if decision.vote else ""
                snippet = decision.statement_text[:140].rstrip()
                flags.append(PulseFlag(
                    kind="fed_decision", symbol="MACRO",
                    message=f"FOMC decision is OUT — {rate}{vote}. {snippet}…",
                ))

        # filing_drop (recency-gated): a RECENT fresh EDGAR item for a HELD
        # symbol = the company on record. A results 8-K (item 2.02) is sharpened to print_landed — the
        # print actually dropped (vs print_soon's countdown). The 8-K title carries its item codes.
        # RECENCY GATE: a newly-ADDED holding has no seen-cache baseline, so without this its entire
        # filing back-catalog would false-fire as "fresh". A "drop" is recent by definition — only
        # fire if filed within `filing_days`.
        filing_days = th.get("filing_days", 5.0)

        def _filed_within(published: str) -> bool:
            try:
                age = (today - _date.fromisoformat(published[:10])).days
            except (ValueError, TypeError):
                return True  # unparseable date → fail open (surface it rather than silently drop)
            return 0 <= age <= filing_days

        held = {h.symbol for h in seed.holdings}
        if digest.fresh_news:
            for item in digest.fresh_news:
                if item.source == "sec.gov" and item.symbol in held and _filed_within(item.published):
                    is_results = "2.02" in item.title
                    flags.append(PulseFlag(
                        kind="print_landed" if is_results else "filing_drop", symbol=item.symbol,
                        message=(f"{item.symbol} REPORTED — 8-K item 2.02 (results of operations); "
                                 f"check the thesis ({item.published})"
                                 if is_results else f"{item.symbol}: {item.title} ({item.published})"),
                    ))

        # the concentrated (lotted) holding drives the vest / lot-flip / TLH flags — config-derived,
        # never a hardcoded ticker. None (no lotted holding) → these flags stay silent.
        conc = next((h for h in seed.holdings if h.lots), None)
        conc_sym = conc.symbol if conc else None
        conc_price = (
            next((p.price for p in digest.day_moves
                  if p.symbol == conc_sym and p.price is not None), None)
            if conc_sym else None
        )

        # vest_approaching (watchman extension): a vest within `vest_days` — it spikes
        # concentration, RESETS the wash-poison window ±30d, and triggers the supplemental
        # under-withholding gap. Deterministic from the vest calendar. Price (for the est-$) from
        # the live day-moves; vest est is units × price.
        vest_days = th.get("vest_days", 7.0)
        if conc_sym:
            for v in seed.vest_calendar:
                try:
                    d = (_date.fromisoformat(v.date) - today).days
                except ValueError:
                    continue
                if 0 <= d <= vest_days:
                    est = f" ~${v.units * conc_price:,.0f}" if conc_price else ""
                    when = "TODAY" if d == 0 else f"in {d}d"
                    flags.append(PulseFlag(
                        kind="vest_approaching", symbol=conc_sym,
                        message=f"{conc_sym} vest {when} ({v.date}): {v.units} sh{est} — concentration "
                                f"rises, wash-poison resets ±30d, supplemental under-withholding gap",
                    ))

        # vest RECONCILIATION: the unreconciled-vest guard — the inverse-in-time
        # of vest_approaching. A calendar vest that PASSED with no ledgered lot (grace for posting
        # lag) nags daily until synced; an unscheduled lot flags the calendar. Pure config-vs-config
        # (watch.vest_reconciliation_flags); the broker's delivered lot is the only confirmation.
        if conc_sym and conc is not None:
            from harness.finance.watch import vest_reconciliation_flags

            flags.extend(vest_reconciliation_flags(
                seed.vest_calendar, conc.lots,
                symbol=conc_sym,
                grace_days=int(th.get("vest_reconcile_grace_days", 2)),
                window_days=int(th.get("vest_reconcile_window_days", 45)),
            ))

        # tax_deadline: a dated tax action within `tax_days` (withholding election,
        # 1040-ES, etc.) — real-money + easy-to-forget. Same proximity mechanism as macro_soon.
        tax_days = th.get("tax_days", 7.0)
        for ev in seed.tax_events:
            try:
                d = (_date.fromisoformat(ev.date) - today).days
            except ValueError:
                continue
            if 0 <= d <= tax_days:
                when = "TODAY" if d == 0 else f"in {d}d"
                flags.append(PulseFlag(
                    kind="tax_deadline", symbol="TAX", message=f"{ev.label} {when} ({ev.date})",
                ))

        # lot_flip: a lot within `lot_flip_pct` of its basis is near a gain/loss
        # flip — which changes its sell-eligibility (gains sell anytime; losses are wash-gated TLH).
        flip_pct = th.get("lot_flip_pct", 2.0)
        if conc and conc_price:
            for lot in conc.lots:
                gl_pct = (conc_price - lot.unit_cost) / lot.unit_cost * 100.0
                if abs(gl_pct) <= flip_pct:
                    flags.append(PulseFlag(
                        kind="lot_flip", symbol=conc.symbol,
                        message=f"{conc.symbol} lot {lot.acquired} ({lot.qty:.0f} sh) {gl_pct:+.1f}% vs "
                                f"basis ${lot.unit_cost:.2f} — near gain/loss flip (sell-eligibility)",
                    ))

        # tlh_window: a clean wash-sale window is OPEN and there's loss to harvest.
        # Only fires inside a clean wash-sale window (not while poisoned) — the harvest nudge, by design.
        ws = digest.wash_sale
        if (
            ws
            and not ws.today_poisoned
            and ws.harvestable_loss is not None
            and ws.harvestable_loss < 0
        ):
            until = f" (clean through {ws.next_clean_end})" if ws.next_clean_end else ""
            sym_lbl = conc_sym or "concentrated"
            flags.append(PulseFlag(
                kind="tlh_window", symbol=sym_lbl,
                message=f"{sym_lbl} TLH window OPEN — ${abs(ws.harvestable_loss):,.0f} harvestable "
                        f"across {ws.harvestable_shares:,.0f} sh{until}",
            ))

        return PulseReport(
            as_of=digest.as_of, quiet=not flags, flags=flags, orders=orders,
            indexes=indexes, digest=digest,
        )

    def ratings(self, symbol: str) -> AnalystRatings:
        """Sell-side analyst consensus (Yahoo quoteSummary, keyless cookie+crumb) — price-target
        mean/high/low/median + recommendation + analyst count. INFORMATION not a verdict (the CLI/MCP
        layer carries the sell-side-bias caveat). Read-only; raises ProviderError on miss."""
        from harness.finance.providers.ratings_provider import fetch_yahoo_ratings

        return fetch_yahoo_ratings(symbol.upper())

    def screen(self, symbol: str) -> ScreenResult:
        screen = self.reader.read_portfolio().screen
        excluded, category = screen.check(symbol)
        if excluded:
            return ScreenResult(
                symbol=symbol.upper(),
                status="excluded",
                category=category,
                note=f"Excluded by the values screen ({category}).",
            )
        return ScreenResult(
            symbol=symbol.upper(),
            status="clean",
            note=(
                "Not on the exclude list. (Positive tilts are thematic — "
                f"{', '.join(screen.positive_tilts)} — not a ticker allowlist.)"
            ),
        )


def _load_feeds() -> list[dict[str, str]]:
    """feeds.yaml — resolved pack > tracker-resident > packaged (Settings.feeds_path,
    the portfolio-seed precedence): the packaged file is a generic default wire; a tuned roster is
    corpus data. Absent file = no feeds."""
    import yaml as _yaml

    from harness.finance.config.settings import get_settings

    path = get_settings().feeds_path
    if not path.exists():
        return []
    data = _yaml.safe_load(path.read_text()) or {}
    return [
        {
            "name": str(f.get("name", "feed")),
            "url": str(f["url"]),
            # category drives the News-tab chips; default "markets" for legacy entries.
            "category": str(f.get("category", "markets")),
        }
        for f in data.get("feeds") or []
        if f.get("url")
    ]


# corporate-suffix stripper for the relevance matcher — "Cisco Systems Inc" → "Cisco Systems", so a headline
# saying "Cisco Systems" (never the "Inc" form) still matches. Conservative: only strips legal-form tails,
# never distinctive words ("Energy"/"Services" stay). The common-name gaps (Google vs "Alphabet Inc")
# are closed by per-holding `aliases` in portfolio.yaml.
_NAME_SUFFIX_RE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|holdings?|ltd|limited|plc|group|"
    r"class\s+[ab]|& co|/the|the)\b\.?",
    re.IGNORECASE,
)


def _simplify_name(name: str) -> str:
    n = re.sub(r"\([^)]*\)", " ", name)  # drop parenthetical lane-notes ("Acme Holdings (a lane note)")
    n = _NAME_SUFFIX_RE.sub("", n)
    return re.sub(r"\s+", " ", n).strip(" .,&/")


def _build_relevance_matcher(seed: PortfolioSeed) -> Callable[[str], list[str]]:
    """Compile a per-symbol headline matcher for the News-tab relevance tag. Each held +
    watchlist symbol contributes match terms: its `name` (corporate suffix stripped), any `aliases`, and
    its cashtag (`$SYM`). **Bare tickers are deliberately NOT matched** — that's the noisy path (ALL / KEY
    / GAP are English words); cashtags + names/aliases are the precise, low-false-positive signals.
    Deterministic + corpus-driven (the aliases live in portfolio.yaml, tunable). Matches the TITLE only —
    higher signal than the summary, which over-matches when an article name-drops many companies."""
    entries: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    rows: list[tuple[str, str, list[str]]] = [(h.symbol, h.name, h.aliases) for h in seed.holdings]
    rows += [(w.symbol, w.name, w.aliases) for w in seed.watchlist]
    for sym, name, aliases in rows:
        sym = sym.upper()
        if sym in seen:
            continue
        seen.add(sym)
        terms = {t for t in [_simplify_name(name), *(_simplify_name(a) for a in aliases)] if t}
        parts: list[str] = []
        if terms:  # names/aliases: word-bounded, case-insensitive (longest first so it wins the alternation)
            alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
            parts.append(rf"\b(?:{alts})\b")
        parts.append(rf"\${re.escape(sym)}\b")  # the cashtag — unambiguous
        entries.append((sym, re.compile("|".join(parts), re.IGNORECASE)))

    def match(title: str) -> list[str]:
        return [sym for sym, pat in entries if pat.search(title)]

    return match
