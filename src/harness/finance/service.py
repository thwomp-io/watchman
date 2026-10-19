"""Single orchestration surface. MCP + CLI adapters both call here (parity is structural).

READ-ONLY: every method observes market data + the corpus. Nothing here places, schedules, or
recommends a trade — this is the assistant's eyes, not its hands on the order book.
"""

from __future__ import annotations

import re
from datetime import date, datetime
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
    FilingReadout,
    FomcDecision,
    Fundamentals,
    GatesBoard,
    MarketOverview,
    Multiples,
    NetWorth,
    OptionGauges,
    PortfolioSnapshot,
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

    # ---- filing reader: fetch + read a filing's content from the Archives ----
    def filing(
        self,
        symbol: str,
        *,
        form: str = "8-K",
        cik: str | None = None,
        accession: str | None = None,
        doc: str | None = None,
        list_only: bool = False,
    ) -> FilingReadout:
        """Read a filing's content — the earnings-print reader. Defaults resolve to the newest
        8-K's EX-99 press-release exhibit (where a print's numbers/guidance/KPIs land within
        minutes of the release); every default is overridable for the nonstandard case
        (--accession for an older filing, --doc for a specific exhibit, --list to see the
        directory first). Requires HARNESS_SEC_CONTACT for the Archives fetch (fail-loud)."""
        from harness.finance.filing import (
            fetch_document,
            fetch_document_types,
            list_documents,
            merge_documents,
            normalize_form,
            pick_press_release,
        )
        from harness.finance.providers.news_provider import fetch_filing_refs
        from harness.textutil import html_to_text

        lookup = self.resolve_cik(symbol, cik=cik)
        if not lookup.found or lookup.cik is None:
            raise ProviderError(
                f"could not resolve a CIK for {symbol.upper()} — funds/ETFs/foreign ADRs aren't "
                "US SEC filers (no 8-K to read); pass --cik <number> if you have one."
            )
        notes: list[str] = []
        primary_doc = ""
        filed = ""
        form = normalize_form(form)
        if accession:
            ref_accession = accession
            notes.append("accession passed explicitly — form/filed reflect the request, not a lookup")
        else:
            refs = fetch_filing_refs(lookup.cik, form=form)
            if not refs:
                raise ProviderError(
                    f"no recent {form} filings for {symbol.upper()} (CIK {lookup.cik}) in the "
                    "submissions window — is the print actually out? (`hn finance watch` shows "
                    "confirmed dates)"
                )
            ref = refs[0]
            ref_accession, filed, primary_doc = ref.accession, ref.filed, ref.primary_doc
            form = ref.form
            if len(refs) > 1:
                # Carry the accessions — a date alone can't be re-aimed at.
                older = ", ".join(f"{r.form} {r.filed} = {r.accession}" for r in refs[1:4])
                notes.append(f"older {form.split('/')[0]}s available via --accession: {older}")
        listed = list_documents(lookup.cik, ref_accession)
        doc_types = fetch_document_types(lookup.cik, ref_accession)
        documents, truncated = merge_documents(listed, doc_types)
        if truncated:
            notes.append(
                f"the directory listing looked truncated ({len(listed)} wrapper files) — documents "
                "taken from the filing's -index.htm table"
            )
        readout = FilingReadout(
            symbol=symbol.upper(),
            cik=lookup.cik,
            form=form,
            filed=filed,
            accession=ref_accession,
            documents=documents,
            doc_types=doc_types,
            notes=notes,
        )
        if list_only:
            return readout
        if doc:
            if doc not in documents and not doc.startswith(ref_accession):
                raise ProviderError(f"{doc} is not in this filing's directory — see the documents list")
            chosen, why = doc, "document passed explicitly"
        else:
            chosen, why = pick_press_release(documents, primary_doc, doc_types)
        readout.notes.append(why)
        readout.doc = chosen
        readout.url, raw = fetch_document(lookup.cik, ref_accession, chosen)
        readout.text = html_to_text(raw)
        return readout

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
        assembly ≥ 4 discrete quarters. Read-only surface for valuation research."""
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
        # YTD facts ride along for the cumulative-quarter TTM, so keep a deeper window
        provider = EdgarProvider(recent=max(recent, 16))
        # Splits since the share count's date (the filing can predate a split while the price is
        # post-split). Best effort: a provider without the endpoint or a failed call = no adjustment.
        splits: list[tuple[str, float]] = []
        market = self._provider()
        if hasattr(market, "get_splits"):
            from datetime import date, timedelta

            try:
                splits = market.get_splits(symbol, (date.today() - timedelta(days=400)).isoformat())
            except Exception:  # noqa: BLE001 — an adjustment source, never a failed read
                splits = []
        return compute_multiples(
            provider, symbol.upper(), lookup.cik, quote, entity_name=lookup.title, splits=splits
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
    def held_symbols(self) -> list[str]:
        """Quotable portfolio symbols, largest cost basis first, deduped across lots (no network)."""
        basis: dict[str, float] = {}
        for h in self.reader.read_portfolio().holdings:
            if h.quotable:
                basis[h.symbol] = basis.get(h.symbol, 0.0) + (h.cost_basis or 0.0)
        return sorted(basis, key=lambda s: -basis[s])

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
        # fetch the UNION of matrix symbols + factor members — the basket needs its own bars even
        # when no basket name is a matrix symbol (the old code only fetched matrix
        # symbols, so a disjoint --factor silently produced no factor block at any N)
        factor_only = [f for f in (factor or []) if f not in symbols]
        closes: dict[str, dict[str, float]] = {}
        fac_closes: dict[str, dict[str, float]] = {}
        missing: list[str] = []
        for sym in [*symbols, *factor_only]:
            try:
                bars = self.history(sym, start=start)
            except Exception:  # noqa: BLE001 — a single bad fetch shouldn't sink the matrix
                bars = []
            if not bars:
                missing.append(sym)
                continue
            series = {b.t[:10]: b.c for b in bars}
            if sym in symbols:
                closes[sym] = series
            else:
                fac_closes[sym] = series
        fac = [s for s in (factor or []) if s in closes or s in fac_closes] or None
        rep = build_correlation(
            closes, factor=fac, factor_closes=fac_closes or None, top_divergence=top_divergence
        )
        rep.days = days
        if missing:
            rep.notes.append("no bars for: " + ", ".join(missing))
        if factor and not fac:
            rep.notes.append("factor DROPPED — no bars for any basket member")
        return rep

    # ---- gauges (options positioning: put/call + IV30 vs HV30) ----
    def gauges(self, symbol: str) -> OptionGauges:
        """Options-positioning gauges for one underlying: gather the option-chain snapshots, the
        spot quote, and ~3 months of daily closes, then the pure `build_gauges` math.

        The chain endpoints are Alpaca-specific (option snapshots + the trading-API contracts
        roster), so this requires the real Alpaca provider — the static/demo provider has no
        option chain and fails with a clear message. The OI roster is OPTIONAL by design: if the
        trading API is unreachable, the verb degrades to volume-only put/call and says so."""
        from datetime import timedelta

        from harness.finance.gauges import build_gauges
        from harness.finance.providers.alpaca_provider import AlpacaProvider

        provider = self._provider()
        if not isinstance(provider, AlpacaProvider):
            raise ProviderError(
                "gauges needs the Alpaca options feed — set ALPACA_API_KEY_ID / "
                "ALPACA_API_SECRET_KEY (the static/demo provider has no option chain)"
            )
        sym = symbol.upper()
        snapshots = provider.fetch_option_chain_snapshots(sym)
        quote = provider.get_quotes([sym])[0]
        start = (date.today() - timedelta(days=70)).isoformat()  # ≥31 trading closes for HV30
        closes = [b.c for b in provider.get_bars(sym, start=start)]
        oi: dict[str, int] | None = None
        oi_as_of: str | None = None
        oi_error = ""
        try:
            oi, oi_as_of = provider.fetch_option_open_interest(sym)
        except Exception as e:  # noqa: BLE001 — OI is a bonus layer; its outage never sinks the verb
            oi, oi_error = None, str(e)
        return build_gauges(
            sym,
            snapshots,
            spot=quote.price,
            closes=closes,
            open_interest=oi,
            oi_as_of=oi_as_of,
            oi_error=oi_error,
        )

    # ---- positions: the full asset table — live quotes + last-known NAV + static balances ----
    def positions(self) -> PortfolioSnapshot:
        """Delegates to `harness.finance.book.positions` (documented there)."""
        from harness.finance import book as _book

        return _book.positions(self)

    # ---- net worth: the full-picture rollup by account/institution ----
    def networth(self) -> NetWorth:
        """Delegates to `harness.finance.book.networth` (documented there)."""
        from harness.finance import book as _book

        return _book.networth(self)

    # ---- net-worth-portrait viz data, DERIVED from portfolio.yaml ----
    # These replace the hand-authored concentration.json / allocation.json. The data contracts they
    # emit are the SAME shape the viz engine's `treemap`/`pie` renderers consume — so `--write` (the
    # CLI) renders them directly, and they're never stale by construction (a fresh portfolio.yaml sync
    # + one command reproduces them current). The deterministic-core half of the deterministic-core/
    # agent-periphery split: the *narrative* status strings + the strategic *Target* stay judgment
    # (the Target is config-stored in `allocation_target:`); everything here is a pure function of
    # holdings + live quotes.

    def concentration_treemap(self) -> dict[str, object]:
        """Delegates to `harness.finance.book.concentration_treemap` (documented there)."""
        from harness.finance import book as _book

        return _book.concentration_treemap(self)

    def allocation_pie(self) -> dict[str, object]:
        """Delegates to `harness.finance.book.allocation_pie` (documented there)."""
        from harness.finance import book as _book

        return _book.allocation_pie(self)

    # ---- mutual-fund EOD-direction proxy (estimate a non-intraday fund from a live proxy basket) ----
    def fund_proxy(self, now: datetime | None = None) -> ProxyEstimate:
        """Delegates to `harness.finance.book.fund_proxy` (documented there)."""
        from harness.finance import book as _book

        return _book.fund_proxy(self, now)

    # ---- full-book intraday day G/L: exact + proxy-est + flat ----
    def daygl(self) -> DayGL:
        """Delegates to `harness.finance.book.daygl` (documented there)."""
        from harness.finance import book as _book

        return _book.daygl(self)

    # ---- keyless news scan (per-ticker wire + filings rail) ----
    def news(self, symbols: list[str] | None = None, limit: int = 5) -> list[SymbolNews]:
        """Keyless news scan: wire headlines (Yahoo per-ticker RSS) + the EDGAR filings rail.

        Default symbols = the portfolio's stocks + ETFs (mutual funds skipped honestly — thin
        ticker feeds; non-intraday funds use `fund-proxy`). Per-feed failures stay loud on the
        result (never rendered as a false-empty)."""
        if not symbols:
            seed = self.reader.read_portfolio()
            symbols = [h.symbol for h in seed.holdings if h.asset_type in {"stock", "etf"}]
        from harness.finance.scan import run_parallel

        # One job per symbol, run on the bounded pool: the Yahoo call inside each
        # job is serialized by its host lane, the EDGAR call by its own, so symbols overlap across
        # providers without bursting either. Per-symbol error capture is unchanged — a job never
        # raises ProviderError, it records it on the SymbolNews it returns. CIK resolution stays on
        # this thread (it reads the cached filer map; not worth a thread).
        def _one(sym: str, cik: str | None) -> Callable[[], SymbolNews]:
            def job() -> SymbolNews:
                sn = SymbolNews(symbol=sym)
                try:
                    sn.headlines = fetch_yahoo_headlines(sym, limit=limit)
                except ProviderError as e:
                    sn.headline_error = str(e)
                if cik:
                    try:
                        sn.filings = fetch_recent_filings(cik, limit=limit)
                    except ProviderError as e:
                        sn.filings_note = f"filings fetch failed: {e}"
                else:
                    sn.filings_note = "no CIK (ETF/fund — not an SEC filer)"
                return sn

            return job

        jobs = []
        for raw in symbols:
            sym = raw.upper()
            jobs.append((sym, _one(sym, self._cik_resolver.cik_for(sym))))
        results = run_parallel(jobs)
        # run_parallel only captures ProviderError, which the job itself never lets escape — so
        # every result carries its SymbolNews; the assert documents the invariant.
        out: list[SymbolNews] = []
        for r in results:
            assert r.value is not None
            out.append(r.value)
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
        `source` filters to one feed by case-insensitive substring of its name (e.g. 'ap',
        'bloomberg', 'reuters'). Per-feed failures degrade to `notes` (one dead feed is never a dead
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
        """Delegates to `harness.finance.watch.watch` (documented there)."""
        from harness.finance import watch as _watch

        return _watch.watch(self, mark_seen=mark_seen, news_limit=news_limit, ticker_news=ticker_news)

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
        # one extra positions() pass, acceptable at the 10-minute dashboard refresh.
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

    def gates(self, *, horizon_days: int = 30) -> GatesBoard:
        """Delegates to `harness.finance.gates.gates` (documented there)."""
        from harness.finance import gates as _gates

        return _gates.gates(self, horizon_days=horizon_days)

    def trap_map(self, *, days: int = 90) -> TrapMap:
        """Delegates to `harness.finance.gates.trap_map` (documented there)."""
        from harness.finance import gates as _gates

        return _gates.trap_map(self, days=days)

    def pulse(self, *, mark_seen: bool = True) -> PulseReport:
        """Delegates to `harness.finance.pulse.pulse` (documented there)."""
        from harness.finance import pulse as _pulse

        return _pulse.pulse(self, mark_seen=mark_seen)

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

    from harness.finance.config.settings import get_settings
    from harness.yamlio import load_yaml

    path = get_settings().feeds_path
    if not path.exists():
        return []
    data = load_yaml(path.read_text()) or {}
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
