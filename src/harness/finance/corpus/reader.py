"""Read the finance corpus.

Reads the machine-readable seed (`config/portfolio.yaml`) for the structured figures — holdings,
the fund-proxy basket, the values screen. Honors the hybrid-config principle (the toolkit's
weights.yaml ↔ preferences.md): the prose in `narratives/finance.md` is the canonical *rationale*;
this YAML is the canonical *figures*, kept in manual sync. We never parse the prose for numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harness.finance.config.settings import get_settings


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
        data: dict[str, Any] = yaml.safe_load(self._path.read_text()) or {}

        holdings = [
            Holding(
                symbol=str(h["symbol"]),
                name=str(h.get("name", "")),
                asset_type=str(h.get("type", "")),
                account=str(h.get("account", "brokerage")),
                shares=float(h.get("shares", 0.0)),
                avg_cost=float(h.get("avg_cost", 0.0)),
                cost_basis=float(h.get("cost_basis", 0.0)),
                last_price=float(h["last_price"]) if h.get("last_price") is not None else None,
                balance=float(h["balance"]) if h.get("balance") is not None else None,
                as_of=str(h.get("as_of", "")),
                lots=[
                    Lot(
                        acquired=str(lt["acquired"]),
                        qty=float(lt["qty"]),
                        unit_cost=float(lt["unit_cost"]),
                    )
                    for lt in h.get("lots", []) or []
                ],
                aliases=[str(a) for a in h.get("aliases", []) or []],
            )
            for h in data.get("holdings", [])
        ]

        # a mutual fund that doesn't price intraday → estimate its EOD direction from a basket of
        # live proxies (config key `fund_proxy:`; absent → no proxy, the verb reports nothing to do).
        proxy_raw = data.get("fund_proxy", {}) or {}
        proxy = ProxyBasket(
            fund=str(proxy_raw.get("fund", "")),
            symbols=[str(s) for s in proxy_raw.get("symbols", [])],
            note=str(proxy_raw.get("note", "")).strip(),
        )

        screen_raw = data.get("values_screen", {}) or {}
        screen = ValuesScreen(
            exclude={
                str(cat): [str(t) for t in tickers]
                for cat, tickers in (screen_raw.get("exclude", {}) or {}).items()
            },
            positive_tilts=[str(t) for t in screen_raw.get("positive_tilts", [])],
        )

        alloc_raw = data.get("allocation_target") or {}
        allocation_target = (
            AllocationTarget(
                caption=str(alloc_raw.get("caption", "")).strip(),
                slices=[
                    AllocSlice(label=str(s["label"]), value=float(s["value"]))
                    for s in alloc_raw.get("slices", []) or []
                ],
            )
            if alloc_raw
            else None
        )

        bands = {
            str(sym): {str(k): float(v) for k, v in (band or {}).items()}
            for sym, band in (data.get("rebalance_bands", {}) or {}).items()
        }
        vest_calendar = [
            VestEvent(date=str(v["date"]), units=int(v.get("units", 0)))
            for v in data.get("vest_calendar", []) or []
        ]
        vests = [v.date for v in vest_calendar]  # backward-compat: dates-only for wash_sale_status
        watchlist = [
            WatchItem(
                symbol=str(w["symbol"]).upper(),
                note=str(w.get("note", "")),
                name=str(w.get("name", "")),
                aliases=[str(a) for a in w.get("aliases", []) or []],
            )
            for w in data.get("watchlist", []) or []
        ]

        open_orders = [
            OpenOrder(
                symbol=str(o["symbol"]),
                side=str(o.get("side", "buy")),
                qty=float(o.get("qty", 0.0)),
                limit=float(o["limit"]),
                placed=str(o.get("placed", "")),
                expires=str(o.get("expires", "")),
                note=str(o.get("note", "")),
            )
            for o in data.get("open_orders") or []
        ]
        pulse_thresholds = {
            str(k): float(v) for k, v in (data.get("pulse") or {}).items()
        }

        index_watch = [str(x) for x in data.get("index_watch") or []]
        macro_events = [
            MacroEvent(date=str(m["date"]), label=str(m.get("label", "")))
            for m in data.get("macro_events") or []
        ]
        tax_events = [
            MacroEvent(date=str(m["date"]), label=str(m.get("label", "")))
            for m in data.get("tax_events") or []
        ]

        return PortfolioSeed(
            holdings=holdings,
            proxy=proxy,
            screen=screen,
            allocation_target=allocation_target,
            rebalance_bands=bands,
            vest_dates=vests,
            vest_calendar=vest_calendar,
            watchlist=watchlist,
            open_orders=open_orders,
            pulse_thresholds=pulse_thresholds,
            index_watch=index_watch,
            macro_events=macro_events,
            tax_events=tax_events,
        )
