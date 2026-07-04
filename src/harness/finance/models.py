"""Shared data shapes (pydantic) used across the provider, corpus, service, and adapters."""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class Quote(BaseModel):
    """A point-in-time read for one symbol, derived from an Alpaca snapshot.

    `available=False` means the provider returned nothing usable (e.g. an OTC ticker not on the
    IEX feed, or a mutual fund) — surfaced explicitly, never silently dropped.
    """

    symbol: str
    available: bool = True
    price: float | None = None  # latest trade price
    prev_close: float | None = None  # previous daily close
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: int | None = None
    as_of: str | None = None  # latest-trade timestamp (ISO)
    feed: str = "iex"
    note: str = ""

    # computed_field → these SERIALIZE (model_dump/JSON), so the dashboard's Indexes table can show the
    # day move (the `columns` widget selects it; otherwise it's a property buried past the column cap).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def day_change(self) -> float | None:
        if self.price is None or self.prev_close is None:
            return None
        return round(self.price - self.prev_close, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def day_change_pct(self) -> float | None:
        if self.price is None or self.prev_close in (None, 0):
            return None
        assert self.prev_close is not None
        return round((self.price - self.prev_close) / self.prev_close * 100.0, 2)


class Bar(BaseModel):
    """A single OHLCV bar — the chart-able unit."""

    t: str  # bar timestamp (ISO date/datetime)
    o: float
    h: float
    low: float = Field(alias="l")
    c: float
    v: int

    model_config = {"populate_by_name": True}


class History(BaseModel):
    symbol: str
    timeframe: str
    bars: list[Bar] = Field(default_factory=list)


class SupportLevel(BaseModel):
    """A clustered swing-low level (see `harness.finance.levels`) — an observation of past
    price behavior, never a prediction."""

    level: float
    touches: int
    last_touch: str  # ISO date of the most recent swing low in the cluster
    distance_pct: float  # signed % from last close (negative = below price)


class Position(BaseModel):
    """A held position: corpus figures (shares/cost) joined to a live quote where quotable."""

    symbol: str
    name: str = ""
    account: str = ""  # which brokerage holds it (broker-a | broker-b | ...)
    asset_type: str = ""  # stock | etf | mutual_fund | cash
    shares: float = 0.0
    avg_cost: float = 0.0
    cost_basis: float = 0.0
    quotable: bool = True
    valuation: str = "live"  # live | last_known | static
    as_of: str = ""  # staleness marker for last_known / static valuations
    price: float | None = None
    market_value: float | None = None
    unrealized_gl: float | None = None
    unrealized_gl_pct: float | None = None
    day_change_pct: float | None = None
    day_gl: float | None = None
    note: str = ""


class PortfolioSnapshot(BaseModel):
    """Read-only portfolio view: live-quoted where possible, last-known / static otherwise. Never a
    recommendation — an observation surface for the sounding-board."""

    positions: list[Position] = Field(default_factory=list)
    quoted_market_value: float = 0.0  # sum of live-quoted positions only
    quoted_cost_basis: float = 0.0
    quoted_unrealized_gl: float = 0.0
    quoted_day_gl: float = 0.0
    # full-picture totals (live + last-known + static):
    live_value: float = 0.0
    last_known_value: float = 0.0
    static_value: float = 0.0
    net_worth: float = 0.0
    notes: list[str] = Field(default_factory=list)


class NetWorthGroup(BaseModel):
    """One account/institution's contribution to net worth."""

    account: str
    value: float = 0.0
    valuation: str = ""  # live | last_known | static | mixed
    as_of: str = ""


class NetWorth(BaseModel):
    """The full-picture net-worth rollup — every tracked account in one read.

    Aggregates everything in the corpus: live-quoted brokerage + last-known mutual-fund NAV +
    static retirement/cash balances. Honest about staleness (per-group `as_of` + valuation basis).
    """

    groups: list[NetWorthGroup] = Field(default_factory=list)
    total: float = 0.0
    live_value: float = 0.0
    last_known_value: float = 0.0
    static_value: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ProxyComponent(BaseModel):
    symbol: str
    available: bool = True
    price: float | None = None
    prev_close: float | None = None
    move_pct: float | None = None


class ProxyEstimate(BaseModel):
    """A rough directional EOD estimate for a non-intraday-priced mutual fund from live proxies."""

    fund: str
    components: list[ProxyComponent] = Field(default_factory=list)
    estimate_pct: float | None = None  # equal-weight mean of available components
    available_count: int = 0
    missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ScreenResult(BaseModel):
    """Values-screen verdict for a symbol — corpus-driven, no network call."""

    symbol: str
    status: str  # "excluded" | "clean"
    category: str | None = None  # which exclude bucket, if excluded
    note: str = ""


class FundamentalFact(BaseModel):
    """One reported XBRL data point for a concept, from an SEC 10-Q/10-K filing."""

    value: float
    unit: str = "USD"
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # Q1 | Q2 | Q3 | FY (issuer-reported)
    period_type: str = ""  # quarter | annual | instant (balance-sheet point-in-time)
    start: str | None = None
    end: str | None = None
    form: str = ""  # 10-Q | 10-K
    filed: str | None = None  # SEC filing date (latest filed wins on restatement)


class ConceptSeries(BaseModel):
    """A reported financial concept (e.g. Revenue) + its recent facts, newest-first.

    `tag` is the us-gaap XBRL tag that actually resolved (varies by issuer — e.g. CRM reports revenue
    under RevenueFromContractWithCustomerExcludingAssessedTax, not Revenues). Empty `facts` + a `note`
    means the concept wasn't reported under any tag we tried — surfaced, never fabricated."""

    label: str
    tag: str = ""
    facts: list[FundamentalFact] = Field(default_factory=list)
    note: str = ""


class CikLookup(BaseModel):
    """Ticker → SEC CIK resolution result (the first hop before any EDGAR data call)."""

    symbol: str
    cik: str | None = None
    title: str = ""
    found: bool = False
    source: str = ""  # "map" (bundled company_tickers.json) | "override" (--cik)


class Fundamentals(BaseModel):
    """Reported (GAAP/XBRL) financials for a holding, from SEC EDGAR — read-only.

    Reported figures LAG real-time: the newest point is the most recent 10-Q/10-K, not a live number.
    The sounding-board surfaces these; the user judges (e.g. 'did the quarterly subscription-revenue
    print confirm the thesis?')."""

    symbol: str
    cik: str | None = None
    entity_name: str = ""
    concepts: list[ConceptSeries] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MultiplesComponent(BaseModel):
    """One underlying figure feeding a valuation multiple — surfaced so the math is auditable.

    `period` is the TTM-assembly basis: "4Q" (summed 4 most-recent discrete quarters), "annual"
    (most-recent 10-K fallback when <4 quarters exist), or "instant" (newest balance-sheet point).
    `tag` is the XBRL tag that actually resolved (varies by issuer). A `note` carries any honesty
    caveat (e.g. a quarter gap in the TTM window). Missing data = value None + a note, never a guess.
    """

    label: str
    value: float | None = None
    tag: str = ""
    period: str = ""  # 4Q | annual | instant | live
    note: str = ""


class Multiples(BaseModel):
    """Valuation multiples for a symbol, from SEC EDGAR (keyless) + a live Alpaca price — read-only.

    GAAP-honest: every multiple's numerator/denominator is surfaced as a `MultiplesComponent` so the
    arithmetic can be audited. The multiples themselves are `float | str` because the honest answer is
    sometimes a STRING, not a number:
    - "N/M" (not meaningful) when the denominator is ≤ 0 (unprofitable — EBITDA or net income
      negative); a huge/negative ratio would mislead, so we say N/M.
    - "unavailable" when a required component wasn't reported under any XBRL tag we tried (the missing
      piece is named in `notes`); we never fabricate a number.

    Reported figures LAG real-time (newest = most recent 10-Q/10-K); only the price is live.
    """

    symbol: str
    cik: str | None = None
    entity_name: str = ""
    # live price + derived market cap / EV
    price: float | None = None
    price_as_of: str | None = None
    market_cap: float | None = None
    enterprise_value: float | None = None
    # the auditable component breakdown (shares, debt, cash, op income, D&A, EBITDA, NI, revenue)
    components: list[MultiplesComponent] = Field(default_factory=list)
    # the multiples — float, or "N/M" / "unavailable" (honesty strings), never a misleading number
    ev_ebitda: float | str | None = None
    pe: float | str | None = None
    ps: float | str | None = None
    # PEG (price/earnings-to-growth). Populated on the FMP path (vendor-computed); the keyless EDGAR
    # path leaves it None (no forward-growth input). "N/M"/"unavailable" honesty strings carry through.
    peg: float | str | None = None
    # Where the multiples came from: "edgar" (auditable-from-XBRL, keyless — the default) or "fmp"
    # (Financial Modeling Prep PRE-COMPUTED TTM ratios — sidesteps the Q4-in-10-K assembly trap).
    source: str = "edgar"
    notes: list[str] = Field(default_factory=list)


class CompareRow(BaseModel):
    """One symbol's side-by-side line in a `compare` report — valuation + live price + screen status.

    Composed (not re-derived) from the existing surfaces: `multiples` (the valuation triplet + market
    cap, EDGAR + live price), `quote` (price + day move), and `screen` (the values verdict). The
    multiples stay `float | str | None` so the honesty strings carry through ("N/M" unprofitable,
    "unavailable" un-tagged) — never a fabricated number. A per-row `note` flags caveats (a failed
    EDGAR resolve, or the mega-cap P/S sanity-guard for the TTM mis-tag)."""

    symbol: str
    entity_name: str = ""
    price: float | None = None
    day_change_pct: float | None = None
    market_cap: float | None = None
    ps: float | str | None = None
    pe: float | str | None = None
    ev_ebitda: float | str | None = None
    screen: str = ""  # "clean" | "excluded" | "" (unknown)
    screen_category: str | None = None
    # tracker-relative research dir (finance/research/.../SYM), existence-checked — "" if none yet.
    # The deep-link anchor: a compare card wikilinks the symbol to its profile; the bus-app nav Ref
    # resolves dir→newest doc at click time (same contract as the bench-map ref.dir).
    research_dir: str = ""
    note: str = ""


class CompareReport(BaseModel):
    """Side-by-side comparison of a selected pick-set (the deterministic half of the Compare tab).

    Symbols-as-rows so ONE JSON contract serves both consumers: the CLI side-by-side table AND the
    dashboard readout-table (which renders a list of row objects). The agent-written narrative
    (the *why* / the verdict) lives in a separate doc-series the dashboard browses — no model in the
    render loop. READ-ONLY."""

    rows: list[CompareRow] = Field(default_factory=list)
    as_of: str | None = None
    notes: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    """One wire headline (Yahoo per-ticker RSS, or a feeds.yaml broad-wire source)."""

    symbol: str
    title: str
    url: str = ""
    source: str = ""  # link domain — Yahoo's feed doesn't carry a clean per-item source field
    published: str = ""  # "YYYY-MM-DD HH:MM" UTC-ish, or the raw pubDate if unparseable
    summary: str = ""  # RSS body: <content:encoded> else <description>, raw as-published (may be
    #                    HTML — capture generously, consumer curates at render); "" when the feed is
    #                    headline-only (most market feeds). Powers the News-tab reader pane.
    # — the following two are populated by `wire` only (broad-market feeds), for the News-tab v2 chips +
    #   relevance filter; they stay empty on the per-ticker `news` path.
    category: str = ""  # the feed's bucket — "markets" | "geopolitics" | "thesis" (from feeds.yaml)
    holdings_hit: list[str] = Field(default_factory=list)  # held/watchlist symbols named in the title
    #                    (deterministic match vs name/aliases/cashtag) — the "is the wire talking about
    #                    MY book?" signal that drives the reader's relevance badge + "my book" filter.


class Filing(BaseModel):
    """One recent SEC filing (data.sec.gov submissions) — the primary-source events rail."""

    form: str  # 8-K = material event; 10-Q/10-K = the real prints
    filed: str  # YYYY-MM-DD
    url: str = ""


class SymbolNews(BaseModel):
    """News scan result for one symbol — partial failures stay loud, never false-empty."""

    symbol: str
    headlines: list[NewsItem] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    headline_error: str | None = None
    filings_note: str | None = None  # "no CIK (fund/ETF)" honesty, or a fetch error


class WireDigest(BaseModel):
    """Broad-market news wire — the `feeds.yaml` source feeds (MarketWatch / CNBC / FT / AP /
    Bloomberg + Al Jazeera geopolitics + thesis-topic searches) aggregated NEWEST-FIRST
    across sources. The `wire` verb's contract.

    Deliberately distinct from the other two news surfaces: `news` is per-ticker (Yahoo RSS keyed on
    a symbol — 'what hit AAPL?'), and `watch`/`pulse` fold the same broad feeds into their
    fresh-headlines rail but DEDUPE them through the seen-cache (the standing-watch delta). `wire` is
    NEVER seen-filtered — it returns the full wire on every run — so a `market take` reads the whole
    market narrative each time instead of only what's new-since-last-watch. Per-feed failures land in
    `notes` (graceful degradation; one dead feed is never a dead run), never as a silent empty."""

    items: list[NewsItem] = Field(default_factory=list)  # newest-first across all sources
    sources_read: list[str] = Field(default_factory=list)  # feed names fetched OK (transparency)
    notes: list[str] = Field(default_factory=list)  # per-feed failures + filter misses


class PrintCountdown(BaseModel):
    """Days-to-print estimate for one filer (10-Q/10-K cadence; honest, approximate)."""

    symbol: str
    estimate: str  # "≈ YYYY-MM-DD (est. from 10-Q/10-K filing cadence)"
    days_out: int | None = None  # negative = estimate already passed (cadence drifted)


class AnalystRatings(BaseModel):
    """Sell-side analyst consensus (Yahoo quoteSummary `financialData`). INFORMATION, not a verdict —
    sell-side targets skew bullish, herd, and lag price (~1/3 hit at 12mo). Read the *consensus* +
    the *range* + the analyst count; never bet on a single target."""

    symbol: str
    current_price: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    target_median: float | None = None
    recommendation_key: str = ""  # strong_buy | buy | hold | sell | strong_sell
    recommendation_mean: float | None = None  # 1.0 = strong buy ... 5.0 = strong sell
    num_analysts: int | None = None
    upside_pct: float | None = None  # (target_mean - current_price) / current_price * 100
    source: str = "yahoo"


class MarketQuote(BaseModel):
    """One instrument in the bird's-eye market basket — a Quote flattened with its display label and
    group (indices | sectors | semis | megacap). `day_change`/`day_change_pct` are materialized (not
    computed properties) so the dashboard plucks them directly from the JSON."""

    symbol: str
    label: str = ""
    group: str = ""
    available: bool = True
    price: float | None = None
    prev_close: float | None = None
    day_change: float | None = None
    day_change_pct: float | None = None
    note: str = ""


class MarketMover(BaseModel):
    """A leader/laggard entry — the biggest movers across sectors/semis/mega-caps."""

    symbol: str
    label: str = ""
    group: str = ""
    day_change_pct: float | None = None


class MarketBreadth(BaseModel):
    """Deterministic breadth FACTS — never a risk-on/off verdict (that judgment is the agent's
    narrative, written into finance/market/take.md). `equal_weight_minus_cap_pct` (RSP − SPY) is the
    headline tell: positive = the average stock beats the mega-cap-weighted index (broadening);
    negative = a narrow, mega-cap-led tape."""

    sectors_advancing: int = 0
    sectors_declining: int = 0
    spy_pct: float | None = None  # SPY% — cap-weighted S&P (the "broad/headline" tape)
    rsp_pct: float | None = None  # RSP% — equal-weighted S&P (the "average" stock)
    equal_weight_minus_cap_pct: float | None = None  # RSP% − SPY%
    megacap_avg_pct: float | None = None
    megacap_spread_pct: float | None = None  # max − min across the Mag7 (dispersion)
    semis_avg_pct: float | None = None


class MarketOverview(BaseModel):
    """Bird's-eye, point-in-time market read: indices + breadth + sector rotation + semis + mega-cap
    dispersion. A pure deterministic gather (one Alpaca snapshots call) + computed facts — the
    interpretive 'take' is a separate agent artifact (finance/market/take.md), so the dashboard never
    depends on a live model call."""

    indices: list[MarketQuote] = Field(default_factory=list)
    sectors: list[MarketQuote] = Field(default_factory=list)
    semis: list[MarketQuote] = Field(default_factory=list)
    megacap: list[MarketQuote] = Field(default_factory=list)
    leaders: list[MarketMover] = Field(default_factory=list)
    laggards: list[MarketMover] = Field(default_factory=list)
    breadth: MarketBreadth = Field(default_factory=MarketBreadth)
    as_of: str | None = None
    notes: list[str] = Field(default_factory=list)


class FomcDecision(BaseModel):
    """The latest FOMC decision, from federalreserve.gov (keyless, Fed-direct). The STATEMENT —
    decision + policy language + target-rate range + vote. The SEP/dot-plot is a separate item we
    LINK but don't parse (PDF/HTML projection table, no clean API). The hawkish/dovish read is the
    agent's job; this surfaces the primary-source facts so the market take is CONFIRMED, not inferred."""

    title: str = ""
    released: str = ""  # pubDate (UTC)
    statement_url: str = ""
    statement_text: str = ""
    target_rate: str = ""  # e.g. "3-1/2 to 3-3/4 percent" (parsed from the statement)
    vote: str = ""  # e.g. "12-0"
    sep_url: str = ""  # economic-projections / dot-plot link, if a SEP meeting
    notes: list[str] = Field(default_factory=list)


class DivergenceDay(BaseModel):
    """A day the focal name's return diverged most from the factor basket — the 'what moved on its
    own' evidence. Sign-coded gap (focal − factor): positive = focal up while the factor lagged."""

    date: str
    focal: str
    focal_ret_pct: float
    factor_ret_pct: float  # equal-weight factor return that day
    gap_pct: float  # focal − factor (the independence on the day)
    members: dict[str, float] = Field(default_factory=dict)  # each factor member's return %, that day


class CorrelationReport(BaseModel):
    """Daily-return correlation across holdings — the 'is this name a real diversifier?' hard-data
    surface. Deterministic FACTS (Pearson correlations / beta / dispersion); the interpretation is the
    agent's narrative, never computed here (the market.build_overview doctrine)."""

    symbols: list[str] = Field(default_factory=list)  # the first is the focal name
    days: int = 0  # the calendar lookback requested
    n_obs: int = 0  # aligned trading days (return observations) actually used
    start: str = ""
    end: str = ""
    matrix: list[list[float]] = Field(default_factory=list)  # symbols × symbols Pearson correlation
    vol_annual: dict[str, float] = Field(default_factory=dict)  # per-symbol annualized vol % (×√252)
    factor: list[str] | None = None  # the equal-weight factor basket (e.g. an AI-names basket), if requested
    factor_corr: dict[str, float] | None = None  # sym → correlation to the factor
    factor_beta: dict[str, float] | None = None  # sym → beta to the factor (cov/var)
    divergence_days: list[DivergenceDay] | None = None  # focal vs factor, biggest-gap days
    notes: list[str] = Field(default_factory=list)
