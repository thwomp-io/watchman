"""Concentration-unwind data contract — the deterministic source behind the unwind dashboard.

Composes everything the concentration-unwind sell-planning surface needs into ONE JSON contract,
feeding two renderers: the static-SVG corpus path and the live visx widget cluster. The lot is the
atom — each lot carries its LIVE gain/loss + classification + wash-sale harvestability, derived from
the current price (never stored). Vests size the timeline + poison the wash windows.

Determinism doctrine: this module computes the facts (gain/loss, harvestability, est-$). The widget
renders + makes them interactive (hover-linking, the what-if price scrubber) — the scrubber just
re-runs `classify_lots` client-side at a hypothetical price, off the same rules. No model in the loop.
Read-only observation surface: it describes tax-lot state, it never recommends or trades.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from harness.finance.corpus.reader import Lot, SoldLot, VestEvent
from harness.finance.models import Bar, SupportLevel
from harness.finance.watch import WashSaleStatus


class LotView(BaseModel):
    """One tax lot with its live, price-derived state — the dashboard's atom."""

    acquired: str
    qty: float
    unit_cost: float
    cost_basis: float
    market_value: float
    unrealized_gl: float
    unrealized_gl_pct: float
    klass: str  # "gain" | "loss" (sign of unrealized_gl at the current price)
    harvestable_now: bool  # loss lot AND not wash-sale-poisoned today
    days_held: int


class VestView(BaseModel):
    """An RSU vest as a timeline marker — units size it, est_value is units × current price."""

    date: str
    units: int
    est_value: float
    days_away: int  # negative = already vested
    future: bool


class PositionRollup(BaseModel):
    shares: float
    cost_basis: float
    avg_cost: float
    market_value: float
    unrealized_gl: float
    unrealized_gl_pct: float


class TlhSummary(BaseModel):
    """The harvest-vs-anytime split at the current price — the sell-planning headline."""

    harvestable_loss: float  # sum of unrealized losses across underwater lots (negative)
    harvestable_shares: float
    gain_lot_value: float  # market value of above-water lots (concentration ammo, sellable anytime)
    gain_lot_shares: float
    loss_lot_value: float = 0.0  # market value of underwater lots (wash-gated until a clean window)
    poisoned_today: bool = False
    clean_window_start: str | None = None
    clean_window_end: str | None = None


class UnwindProgress(BaseModel):
    """The %-complete read — how much of the position has been unwound.

    pct_unwound = shares_sold / shares_ever_held × 100, ever-held = current + sold-to-date.
    THE DENOMINATOR GROWS at each vest (new lots raise `current`) — deliberately: a vest means
    more pie appeared, so the % dips honestly instead of overstating progress through a peak vest
    year. The pct_of_liquid companion (vs plan-start/target anchors) is the risk-remaining read."""

    pct_unwound: float
    shares_sold: float
    shares_current: float
    shares_ever: float
    pct_of_liquid: float | None = None  # live weight in the liquid (non-retirement) pool
    pct_of_liquid_at_start: float | None = None  # config anchor: plan-start weight
    target_pct_liquid: float | None = None  # config anchor: end-state weight


def build_progress(
    *,
    shares_current: float,
    sold: list[SoldLot],
    market_value: float | None = None,
    liquid_total: float | None = None,
    meta: dict[str, float] | None = None,
) -> UnwindProgress | None:
    """Pure %-complete math off the sold-ledger. No ledger → None (packs without `unwind:`)."""
    if not sold:
        return None
    shares_sold = round(sum(s.qty for s in sold), 4)
    shares_ever = round(shares_current + shares_sold, 4)
    pct = round(shares_sold / shares_ever * 100.0, 1) if shares_ever else 0.0
    pct_liquid = (
        round(market_value / liquid_total * 100.0, 1)
        if market_value is not None and liquid_total
        else None
    )
    meta = meta or {}
    return UnwindProgress(
        pct_unwound=pct,
        shares_sold=shares_sold,
        shares_current=round(shares_current, 4),
        shares_ever=shares_ever,
        pct_of_liquid=pct_liquid,
        pct_of_liquid_at_start=meta.get("plan_start_pct_liquid"),
        target_pct_liquid=meta.get("target_pct_liquid"),
    )


class UnwindReport(BaseModel):
    """The full concentration-unwind contract — one source, two renderers (static SVG + live visx widget)."""

    as_of: str
    symbol: str
    price: float
    prev_close: float | None = None
    day_change_pct: float | None = None
    position: PositionRollup
    tlh: TlhSummary
    lots: list[LotView] = Field(default_factory=list)
    vests: list[VestView] = Field(default_factory=list)
    wash_sale: WashSaleStatus
    support_levels: list[SupportLevel] = Field(default_factory=list)
    bars: list[Bar] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Viz-ready sub-shapes — the dashboard widgets `value_path` into these; each matches an existing
    # bus-app component's contract (so they auto-sniff) except vest_timeline (the one new component).
    price_chart: dict[str, Any] = Field(default_factory=dict)  # LineChart `line` (closes + basis)
    lot_split: dict[str, Any] = Field(default_factory=dict)  # Treemap (lots sized by $, gain/loss)
    tlh_split: dict[str, Any] = Field(default_factory=dict)  # Donut `pies` (sellable vs wash-gated)
    vest_timeline: dict[str, Any] = Field(default_factory=dict)  # VestTimeline (markers + wash bands)
    # %-complete: None when the seed has no `unwind:` sold-ledger (sample packs)
    progress: UnwindProgress | None = None


def classify_lots(lots: list[Lot], price: float, *, poisoned: bool, today: date) -> list[LotView]:
    """Each lot's live gain/loss + harvestability at ``price``. Pure — the what-if scrubber's core.

    A lot is harvestable_now when it's underwater AND today isn't wash-poisoned. (The
    harvest-bigger-than-replacement path can still harvest inside a poisoned window — see the TLH
    plan; this flag is the conservative, calendar-clean reading the dashboard signals on.)
    """
    out: list[LotView] = []
    for lot in lots:
        market_value = round(lot.qty * price, 2)
        cost_basis = lot.cost_basis
        gl = round(market_value - cost_basis, 2)
        gl_pct = round(gl / cost_basis * 100.0, 2) if cost_basis else 0.0
        is_loss = gl < 0
        out.append(
            LotView(
                acquired=lot.acquired,
                qty=lot.qty,
                unit_cost=lot.unit_cost,
                cost_basis=cost_basis,
                market_value=market_value,
                unrealized_gl=gl,
                unrealized_gl_pct=gl_pct,
                klass="loss" if is_loss else "gain",
                harvestable_now=is_loss and not poisoned,
                days_held=(today - date.fromisoformat(lot.acquired)).days,
            )
        )
    return out


def _vests(vest_calendar: list[VestEvent], price: float, today: date) -> list[VestView]:
    out: list[VestView] = []
    for v in vest_calendar:
        days_away = (date.fromisoformat(v.date) - today).days
        out.append(
            VestView(
                date=v.date,
                units=v.units,
                est_value=round(v.units * price, 2),
                days_away=days_away,
                future=days_away >= 0,
            )
        )
    return out


def _rollup(lots: list[LotView]) -> PositionRollup:
    shares = round(sum(lt.qty for lt in lots), 4)
    cost_basis = round(sum(lt.cost_basis for lt in lots), 2)
    market_value = round(sum(lt.market_value for lt in lots), 2)
    gl = round(market_value - cost_basis, 2)
    return PositionRollup(
        shares=shares,
        cost_basis=cost_basis,
        avg_cost=round(cost_basis / shares, 4) if shares else 0.0,
        market_value=market_value,
        unrealized_gl=gl,
        unrealized_gl_pct=round(gl / cost_basis * 100.0, 2) if cost_basis else 0.0,
    )


_WASH_DAYS = 30


def _merge_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Union overlapping/adjacent (lo, hi) date ranges — the poison bands of clustered vests."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for lo, hi in ordered[1:]:
        if lo <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _price_chart(symbol: str, bars: list[Bar], avg_cost: float, costs: list[float]) -> dict[str, Any]:
    """LineChart `line` contract: closes + a clean basis band (avg / low / high) as reference lines.
    Per-lot basis lines are a Tier-2 (cockpit) feature — kept to 3 lines here to stay readable."""
    levels = [{"label": f"avg basis ${avg_cost:,.2f}", "y": round(avg_cost, 2)}]
    if costs:
        levels += [
            {"label": f"low ${min(costs):,.2f}", "y": round(min(costs), 2)},
            {"label": f"high ${max(costs):,.2f}", "y": round(max(costs), 2)},
        ]
    return {
        "title": f"{symbol} — price vs cost-basis band",
        "subtitle": f"{len(bars)}d daily closes · dashed = lot-basis band (avg/low/high)",
        "yPrefix": "$",
        "series": [{"label": symbol, "points": [{"x": b.t[:10], "y": b.c} for b in bars]}],
        "levels": levels,
    }


def _lot_split(symbol: str, lot_views: list[LotView], price: float) -> dict[str, Any]:
    """Treemap contract: lots sized by market value, grouped gain/loss (sellable-now vs wash-gated)."""
    return {
        "title": f"{symbol} lots — sized by value, colored gain/loss",
        "subtitle": f"@ ${price:,.2f} · gain = sellable anytime · loss = TLH inventory (wash-gated)",
        "groups": [
            {"key": "gain", "label": "Gain — sellable anytime"},
            {"key": "loss", "label": "Loss — TLH inventory"},
        ],
        "nodes": [
            {
                "label": lt.acquired,
                "value": lt.market_value,
                "group": lt.klass,
                "detail": {
                    "qty": lt.qty,
                    "basis": lt.unit_cost,
                    "gl": lt.unrealized_gl,
                    "gl_pct": lt.unrealized_gl_pct,
                    "harvestable_now": lt.harvestable_now,
                },
            }
            for lt in lot_views
        ],
    }


def _tlh_split(tlh: TlhSummary, price: float) -> dict[str, Any]:
    """Donut `pies` contract: the position's market value split by sellability."""
    return {
        "title": "Sellability split",
        "subtitle": f"position market value @ ${price:,.2f}",
        "pies": [
            {
                "label": "By sellability",
                "slices": [
                    {"label": "Gain — anytime", "value": tlh.gain_lot_value},
                    {"label": "Loss — wash-gated", "value": tlh.loss_lot_value},
                ],
            }
        ],
    }


def _vest_timeline(symbol: str, vest_calendar: list[VestEvent], price: float, today: date) -> dict[str, Any]:
    """NEW VestTimeline contract: $-sized vest markers + wash-poison/clean window bands + today.

    Windows are computed deterministically here (±30d poison around each vest, merged; clean = the
    gaps) so the component is a dumb renderer — the determinism doctrine end to end."""
    ordered = sorted(vest_calendar, key=lambda v: v.date)
    vest_dates = [date.fromisoformat(v.date) for v in ordered]
    if vest_dates:
        start = min(today - timedelta(days=20), vest_dates[0] - timedelta(days=35))
        end = vest_dates[-1] + timedelta(days=35)
    else:
        start, end = today - timedelta(days=20), today + timedelta(days=60)

    wash = timedelta(days=_WASH_DAYS)
    poison = _merge_ranges([(d - wash, d + wash) for d in vest_dates])
    windows: list[dict[str, Any]] = []
    cursor = start
    for lo, hi in poison:
        if lo > cursor:
            windows.append(
                {"start": cursor.isoformat(), "end": (lo - timedelta(days=1)).isoformat(), "kind": "clean"}
            )
        windows.append(
            {"start": max(lo, start).isoformat(), "end": min(hi, end).isoformat(), "kind": "poison"}
        )
        cursor = max(cursor, hi + timedelta(days=1))
    if cursor <= end:
        windows.append({"start": cursor.isoformat(), "end": end.isoformat(), "kind": "clean"})

    return {
        "title": f"{symbol} vest calendar — sell-planning timeline",
        "subtitle": "vests poison loss-sales ±30d (amber); green = clean harvest window",
        "today": today.isoformat(),
        "domain": [start.isoformat(), end.isoformat()],
        "vests": [
            {
                "date": v.date,
                "units": v.units,
                "value": round(v.units * price, 2),
                "future": date.fromisoformat(v.date) >= today,
            }
            for v in ordered
        ],
        "windows": sorted(windows, key=lambda w: w["start"]),
    }


def build_unwind(
    *,
    symbol: str,
    price: float,
    prev_close: float | None,
    day_change_pct: float | None,
    lots: list[Lot],
    vest_calendar: list[VestEvent],
    wash_sale: WashSaleStatus,
    support_levels: list[SupportLevel],
    bars: list[Bar],
    today: date,
    progress: UnwindProgress | None = None,
) -> UnwindReport:
    """Assemble the full contract from injected inputs (pure — no I/O; the CLI fetches price/bars)."""
    lot_views = classify_lots(lots, price, poisoned=wash_sale.today_poisoned, today=today)
    loss_lots = [lt for lt in lot_views if lt.klass == "loss"]
    gain_lots = [lt for lt in lot_views if lt.klass == "gain"]

    tlh = TlhSummary(
        harvestable_loss=round(sum(lt.unrealized_gl for lt in loss_lots), 2),
        harvestable_shares=round(sum(lt.qty for lt in loss_lots), 4),
        gain_lot_value=round(sum(lt.market_value for lt in gain_lots), 2),
        gain_lot_shares=round(sum(lt.qty for lt in gain_lots), 4),
        loss_lot_value=round(sum(lt.market_value for lt in loss_lots), 2),
        poisoned_today=wash_sale.today_poisoned,
        clean_window_start=wash_sale.next_clean_start,
        clean_window_end=wash_sale.next_clean_end,
    )

    rollup = _rollup(lot_views)
    costs = [lt.unit_cost for lt in lot_views]

    notes = [
        "Read-only observation — lot-level tax state, not advice. Lot inputs are manually synced "
        "(from a broker vesting & delivery feed); gain/loss + harvestability derive from the live price.",
        f"At ${price:,.2f}: {len(gain_lots)} gain lot(s) sellable anytime (gains never wash) · "
        f"{len(loss_lots)} loss lot(s) are the TLH inventory (wash-gated).",
    ]

    return UnwindReport(
        as_of=today.isoformat(),
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        day_change_pct=day_change_pct,
        position=rollup,
        tlh=tlh,
        lots=lot_views,
        vests=_vests(vest_calendar, price, today),
        wash_sale=wash_sale,
        support_levels=support_levels,
        bars=bars,
        notes=notes,
        price_chart=_price_chart(symbol, bars, rollup.avg_cost, costs),
        lot_split=_lot_split(symbol, lot_views, price),
        tlh_split=_tlh_split(tlh, price),
        vest_timeline=_vest_timeline(symbol, vest_calendar, price, today),
        progress=progress,
    )
