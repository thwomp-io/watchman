"""Read the finance corpus.

Reads the machine-readable seed (`config/portfolio.yaml`) for the structured figures — holdings,
the fund-proxy basket, the values screen. The prose in `narratives/finance.md` holds the
*rationale*; this YAML holds the *figures*; the two are kept in sync by hand.
We never parse the prose for numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.finance.config.settings import get_settings
from harness.yamlio import load_yaml


@dataclass
class Lot:
    """One tax lot of a holding (RSU vest/lapse or a purchase). Stable inputs ONLY —
    gain/loss + wash-sale eligibility are derived LIVE from the current price, never stored.
    Maintained on vest (adds a lot) / sale (removes-reduces); synced by hand."""

    acquired: str  # YYYY-MM-DD (acquisition / RSU-lapse date)
    qty: float
    unit_cost: float

    @property
    def cost_basis(self) -> float:
        return round(self.qty * self.unit_cost, 2)


@dataclass
class Holding:
    symbol: str
    name: str
    asset_type: str  # stock | etf | mutual_fund | retirement | cash
    account: str = "brokerage"  # which brokerage/institution holds it — the grouping key
    shares: float = 0.0
    avg_cost: float = 0.0
    cost_basis: float = 0.0
    last_price: float | None = None  # last-known per-share price for non-quotable holdings (mutual funds)
    balance: float | None = None  # static account value (retirement / cash) — no shares/market quote
    as_of: str = ""  # last-synced date for last_price / balance (staleness marker)
    lots: list[Lot] = field(default_factory=list)  # per-lot tax basis (concentration unwind / TLH); optional
    aliases: list[str] = field(default_factory=list)  # extra headline-match terms for the News-tab
    #   relevance tagger beyond `name` — e.g. the common name ("Google" vs "Alphabet Inc").

    @property
    def quotable(self) -> bool:
        # Only exchange-traded instruments are live-quotable via Alpaca (stocks / ETFs).
        return self.asset_type in {"stock", "etf"}

    @property
    def valuation(self) -> str:
        """How this holding is valued: 'live' (quote), 'last_known' (stale NAV), 'static' (balance)."""
        if self.quotable:
            return "live"
        if self.balance is not None:
            return "static"
        return "last_known"


@dataclass
class ProxyBasket:
    fund: str
    symbols: list[str]
    note: str = ""
    # symbol -> % of the FUND (N-PORT weight). Populated by the `basket:` config form;
    # empty for the legacy `symbols:` form, which stays equal-weight.
    weights: dict[str, float] = field(default_factory=dict)
    # symbol -> quote lane (the OTC longtail): "alpaca" (default — exchange-listed,
    # IEX feed) or "yahoo" (OTC ADRs the IEX feed can't see — quoted via the global verb's v8
    # client). Only non-default lanes are stored; absent symbol = alpaca.
    sources: dict[str, str] = field(default_factory=dict)
    # the fund's cash sweep as a flat component (% of fund, 0% day-move by definition) — dampens
    # the estimate the way the fund's own cash does (the roster's cash-sweep lines).
    cash_weight: float = 0.0
    # The unmapped tail, filled by exposure: the fund's full roster (a reference yaml, resolved against this
    # portfolio file's directory) and one ETF per exposure. Empty = no fills; the tail re-spreads as before.
    holdings_ref: Path | None = None
    fills: list[ProxyFill] = field(default_factory=list)


@dataclass
class ProxyFill:
    """One exposure's stand-in for the roster names the basket can't quote: `countries` are the roster's
    country codes that map to it; a roster row's own `exposure:` field overrides its country."""

    symbol: str
    exposure: str
    countries: list[str] = field(default_factory=list)


@dataclass
class ValuesScreen:
    # category -> excluded tickers
    exclude: dict[str, list[str]] = field(default_factory=dict)
    positive_tilts: list[str] = field(default_factory=list)

    def check(self, symbol: str) -> tuple[bool, str | None]:
        """(excluded?, category). Case-insensitive ticker match."""
        sym = symbol.upper()
        for category, tickers in self.exclude.items():
            if sym in {t.upper() for t in tickers}:
                return True, category
        return False, None


@dataclass
class WatchItem:
    """A non-held symbol the user keeps tabs on — graduated research candidates.

    Mirrors a brokerage watchlist; `note` carries the one-line why (lane / screen status)."""

    symbol: str
    note: str = ""
    name: str = ""  # display/company name — the News-tab relevance tagger's headline-match term
    aliases: list[str] = field(default_factory=list)  # extra match terms


@dataclass
class MacroEvent:
    """A calendar-known macro event (FOMC/CPI/jobs — the static-reference layer)."""

    date: str  # YYYY-MM-DD
    label: str


@dataclass
class VestEvent:
    """An equity/RSU vest event (manual-synced from a vesting calendar) for a concentrated holding.
    Units drive the vest-timeline marker size + the est-$ at the current price; dates drive the
    wash-sale math."""

    date: str  # YYYY-MM-DD
    units: int = 0


@dataclass
class SoldLot:
    """One executed unwind sale (the structured sold-ledger) — maintained at each
    fill, replacing the comment-only history. Drives the unwind-progress %-complete
    (sold / ever-held); the denominator grows as vests add shares — the honest read."""

    date: str  # YYYY-MM-DD
    qty: float
    px: float | None = None  # execution price (provenance; not used in the % math)


@dataclass
class OpenOrder:
    """A resting order (the GTC ledger) — maintained at place/cancel/fill.
    Pulse computes distance-to-limit; the harness NEVER places/cancels orders (read-only)."""

    symbol: str
    side: str  # buy | sell
    qty: float
    limit: float
    placed: str = ""
    expires: str = ""
    note: str = ""


@dataclass
class AllocSlice:
    """One slice of the strategic TARGET allocation (the ratified end-state plan). JUDGMENT, not
    derivable — so it's config-stored (synced when the *strategy* changes, which is rare), the
    deterministic-config sibling of `rebalance_bands`. The *Current* allocation, by contrast, is
    derived live from `holdings` (see FinanceService.allocation_pie)."""

    label: str
    value: float


@dataclass
class AllocationTarget:
    caption: str = ""
    slices: list[AllocSlice] = field(default_factory=list)


@dataclass
class PortfolioSeed:
    holdings: list[Holding]
    proxy: ProxyBasket
    screen: ValuesScreen
    # strategic TARGET allocation (config-stored judgment; the Current pie is derived from holdings)
    allocation_target: AllocationTarget | None = None
    # standing-watch inputs: scaffold drift bands (% of the brokerage account;
    # real targets are user-set) + vest dates (manual-sync w/ a vesting calendar)
    rebalance_bands: dict[str, dict[str, float]] = field(default_factory=dict)
    vest_dates: list[str] = field(default_factory=list)  # dates only (the existing wash-sale input)
    vest_calendar: list[VestEvent] = field(default_factory=list)  # dates + units ($-sizing, timeline)
    # watchlist: non-held symbols watched for day moves + news deltas
    watchlist: list[WatchItem] = field(default_factory=list)
    # the GTC ledger + pulse flag thresholds
    open_orders: list[OpenOrder] = field(default_factory=list)
    pulse_thresholds: dict[str, float] = field(default_factory=dict)
    # index-level sense + the macro calendar
    index_watch: list[str] = field(default_factory=list)
    macro_events: list[MacroEvent] = field(default_factory=list)
    # dated tax actions: withholding election, 1040-ES, etc. — pulse tax_deadline
    tax_events: list[MacroEvent] = field(default_factory=list)
    # the unwind sold-ledger + plan anchors — inputs to the %-complete widget
    unwind_sold: list[SoldLot] = field(default_factory=list)
    unwind_meta: dict[str, float] = field(default_factory=dict)  # plan_start_pct_liquid / target_pct_liquid

    @property
    def concentrated(self) -> Holding | None:
        """The concentrated single-stock holding to plan an unwind for — the one carrying per-lot
        basis (`lots:`). None when the portfolio has no lotted holding (the unwind / vest / wash-sale /
        TLH surfaces stay inactive). Config-driven, so it's whatever holding the user marks with lots,
        never a hardcoded ticker."""
        return next((h for h in self.holdings if h.lots), None)


class CorpusReader:
    def __init__(self, portfolio_path: Path | None = None) -> None:
        # Explicit arg wins (tests inject a fixture); otherwise the pack-aware default — the active
        # weight pack's finance/portfolio.yaml if WEIGHTS_PACK is loaded, else the packaged seed.
        self._path = portfolio_path or get_settings().portfolio_path

    def read_portfolio(self) -> PortfolioSeed:
        data: dict[str, Any] = load_yaml(self._path.read_text()) or {}
        vest_calendar = [
            VestEvent(date=str(v["date"]), units=int(v.get("units", 0)))
            for v in data.get("vest_calendar") or []
        ]
        unwind = data.get("unwind") or {}
        return PortfolioSeed(
            holdings=[_holding(h) for h in data.get("holdings", [])],
            proxy=_proxy_basket(data.get("fund_proxy") or {}, self._path.parent),
            screen=_values_screen(data.get("values_screen") or {}),
            allocation_target=_allocation_target(data.get("allocation_target") or {}),
            rebalance_bands={
                str(sym): {str(k): float(v) for k, v in (band or {}).items()}
                for sym, band in (data.get("rebalance_bands") or {}).items()
            },
            vest_dates=[v.date for v in vest_calendar],
            vest_calendar=vest_calendar,
            watchlist=[
                WatchItem(
                    symbol=str(w["symbol"]).upper(),
                    note=str(w.get("note", "")),
                    name=str(w.get("name", "")),
                    aliases=[str(a) for a in w.get("aliases") or []],
                )
                for w in data.get("watchlist") or []
            ],
            open_orders=[_open_order(o) for o in data.get("open_orders") or []],
            pulse_thresholds={str(k): float(v) for k, v in (data.get("pulse") or {}).items()},
            index_watch=[str(x) for x in data.get("index_watch") or []],
            macro_events=_dated_events(data.get("macro_events")),
            tax_events=_dated_events(data.get("tax_events")),
            unwind_sold=[
                SoldLot(
                    date=str(s["date"]),
                    qty=float(s["qty"]),
                    px=float(s["px"]) if s.get("px") is not None else None,
                )
                for s in unwind.get("sold") or []
            ],
            unwind_meta={
                str(k): float(v)
                for k, v in unwind.items()
                if k in ("plan_start_pct_liquid", "target_pct_liquid") and v is not None
            },
        )


# ---- one function per section of portfolio.yaml --------------------------------------------------
# Each takes the section's raw YAML and returns its typed form. A missing optional key takes the
# dataclass's default; a missing required one (a holding's symbol, an order's limit) is a KeyError,
# which is the right failure for a ledger that is edited by hand.


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _holding(raw: dict[str, Any]) -> Holding:
    return Holding(
        symbol=str(raw["symbol"]),
        name=str(raw.get("name", "")),
        asset_type=str(raw.get("type", "")),
        account=str(raw.get("account", "brokerage")),
        shares=float(raw.get("shares", 0.0)),
        avg_cost=float(raw.get("avg_cost", 0.0)),
        cost_basis=float(raw.get("cost_basis", 0.0)),
        last_price=_optional_float(raw.get("last_price")),
        balance=_optional_float(raw.get("balance")),
        as_of=str(raw.get("as_of", "")),
        lots=[
            Lot(acquired=str(lot["acquired"]), qty=float(lot["qty"]), unit_cost=float(lot["unit_cost"]))
            for lot in raw.get("lots") or []
        ],
        aliases=[str(a) for a in raw.get("aliases") or []],
    )


def _proxy_basket(raw: dict[str, Any], base_dir: Path) -> ProxyBasket:
    """The basket that stands in for a fund with no intraday price (`fund_proxy:`).

    Two forms: `basket:` rows of {symbol, weight, source?}, where the weights are each name's share
    of the fund, or the older `symbols:` list, which is equal-weight. `basket:` wins when both are
    present. `holdings_ref` is resolved against the portfolio file's own directory.
    """
    rows = raw.get("basket") or []
    if rows:
        symbols = [str(r.get("symbol", "")) for r in rows if r.get("symbol")]
        weights = {
            str(r["symbol"]): float(r["weight"])
            for r in rows
            if r.get("symbol") and r.get("weight") is not None
        }
        sources = {
            str(r["symbol"]): str(r["source"]).lower() for r in rows if r.get("symbol") and r.get("source")
        }
    else:
        symbols = [str(s) for s in raw.get("symbols", [])]
        weights, sources = {}, {}
    return ProxyBasket(
        fund=str(raw.get("fund", "")),
        symbols=symbols,
        note=str(raw.get("note", "")).strip(),
        weights=weights,
        sources=sources,
        cash_weight=float(raw.get("cash_weight", 0.0) or 0.0),
        holdings_ref=(base_dir / str(raw["holdings_ref"])).resolve() if raw.get("holdings_ref") else None,
        fills=[
            ProxyFill(
                symbol=str(f["symbol"]).upper(),
                exposure=str(f.get("exposure") or f["symbol"]),
                countries=[str(c).upper() for c in f.get("countries") or []],
            )
            for f in raw.get("fills") or []
            if f.get("symbol")
        ],
    )


def _values_screen(raw: dict[str, Any]) -> ValuesScreen:
    return ValuesScreen(
        exclude={
            str(category): [str(t) for t in tickers]
            for category, tickers in (raw.get("exclude") or {}).items()
        },
        positive_tilts=[str(t) for t in raw.get("positive_tilts", [])],
    )


def _allocation_target(raw: dict[str, Any]) -> AllocationTarget | None:
    if not raw:
        return None
    return AllocationTarget(
        caption=str(raw.get("caption", "")).strip(),
        slices=[AllocSlice(label=str(s["label"]), value=float(s["value"])) for s in raw.get("slices") or []],
    )


def _open_order(raw: dict[str, Any]) -> OpenOrder:
    return OpenOrder(
        symbol=str(raw["symbol"]),
        side=str(raw.get("side", "buy")),
        qty=float(raw.get("qty", 0.0)),
        limit=float(raw["limit"]),
        placed=str(raw.get("placed", "")),
        expires=str(raw.get("expires", "")),
        note=str(raw.get("note", "")),
    )


def _dated_events(raw: list[dict[str, Any]] | None) -> list[MacroEvent]:
    return [MacroEvent(date=str(e["date"]), label=str(e.get("label", ""))) for e in raw or []]
