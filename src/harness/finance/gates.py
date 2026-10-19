"""The plan's calendar gates and the trap map — `hn finance gates` / `hn finance trap-map`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    pass

from harness.finance.models import (
    GateItem,
    GatesBoard,
    TrapMap,
)
from harness.finance.providers.base import ProviderError
from harness.finance.providers.news_provider import fetch_recent_filings

if TYPE_CHECKING:
    from harness.finance.service import FinanceService

def gates(svc: FinanceService,  *, horizon_days: int = 30) -> GatesBoard:
    """The plan-gates board — upcoming held-name prints + macro events inside
    the horizon, structured + imminent-first. The FAST calendar twin of pulse's
    print_soon/macro_soon prose flags: no quotes, no wire — the print dates ride the day-TTL
    earnings cache (a cold symbol falls back to the filing-cadence estimate, labeled), the
    macro rows read the seed's macro_events verbatim. Built for the Plans tab's
    countdown bars, where a widget refresh must not cost a full pulse run."""
    from datetime import date as _date

    from harness.finance.providers.nasdaq_provider import fetch_earnings_date
    from harness.finance.watch import EarningsDateCache

    seed = svc.reader.read_portfolio()
    today = _date.today()
    board = GatesBoard(
        as_of=datetime.now().isoformat(timespec="seconds"),
        horizon_days=horizon_days,
    )

    # held-name prints — confirmed (nasdaq, day-TTL cache) first, cadence estimate fallback
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
                ed = None
        if ed is not None:
            days = (ed.report_date - today).days
            if 0 <= days <= horizon_days:
                board.gates.append(GateItem(
                    kind="print", symbol=h.symbol,
                    label=f"{h.symbol} print",
                    date=ed.report_date.isoformat(), days=days,
                    confirmed=ed.confirmed,
                ))
            continue
        # cadence fallback (honest estimate; unresolvable symbols skip silently — ETF-shaped)
        cik = svc._cik_resolver.cik_for(h.symbol)
        if not cik:
            continue
        try:
            from harness.finance.research import estimate_next_print

            est = estimate_next_print(fetch_recent_filings(cik, limit=14))
        except ProviderError as e:
            board.notes.append(f"{h.symbol} print estimate failed: {e}")
            continue
        if not est:
            continue
        try:
            est_date = _date.fromisoformat(est.split()[1])
        except (ValueError, IndexError):
            continue
        days = (est_date - today).days
        if 0 <= days <= horizon_days:
            board.gates.append(GateItem(
                kind="print", symbol=h.symbol,
                label=f"{h.symbol} print (est)",
                date=est_date.isoformat(), days=days, confirmed=False,
            ))
    if earnings_dirty:
        earnings_cache.save()

    # macro events — the seed's calendar verbatim, inside the horizon
    for ev in seed.macro_events:
        try:
            d = (_date.fromisoformat(ev.date) - today).days
        except ValueError:
            continue
        if 0 <= d <= horizon_days:
            board.gates.append(GateItem(
                kind="macro", symbol=None, label=ev.label,
                date=ev.date, days=d, confirmed=True,
            ))

    # machine-sourced release calendar — high-tier rows from the CACHE only
    # (a widget refresh never fetches; `hn finance calendar` / pulse keep the cache warm),
    # skipped where the hand-kept ledger already names the same release on the same date.
    from harness.finance.calendar import build_calendar, covered_by_ledger, dedup_key

    ledger = [(ev.date, ev.label) for ev in seed.macro_events]
    cal = build_calendar(min(horizon_days + 1, 45), today=today, offline=True)
    seen_cal: set[tuple[str, str | None]] = set()
    for ce in cal.events:
        if ce.tier != "high" or covered_by_ledger(ce, ledger) or dedup_key(ce) in seen_cal:
            continue
        seen_cal.add(dedup_key(ce))  # one bar per release family per day (m/m + y/y)
        d = (_date.fromisoformat(ce.date) - today).days
        if 0 <= d <= horizon_days:
            board.gates.append(GateItem(
                kind="macro", symbol=None, label=ce.label, date=ce.date, days=d,
                confirmed=ce.confirmed,
            ))

    # vests — the concentrated holding's forward RSU calendar. A vest is a
    # calendar gate like a print: shares land, the wash window re-arms, sell-at-vest fires.
    from harness.finance.unwind import vest_gates

    conc = seed.concentrated
    board.gates.extend(vest_gates(
        seed.vest_calendar, conc.symbol if conc else None, today, horizon_days
    ))

    board.gates.sort(key=lambda g: (g.days if g.days is not None else 10_000, g.kind))
    return board


def trap_map(svc: FinanceService,  *, days: int = 90) -> TrapMap:
    """The trap-map — every symbol with resting GTC orders as a vertical price
    ladder: live price + rungs (with distance-to-fill, pulse's formula) + bars-derived support
    shelves. Pure composition of existing primitives (open_orders ledger · quote · history ·
    support_levels); read-only. One bars call per laddered symbol — fine at the 10-minute
    dashboard refresh (or a manual run). An unquotable symbol renders honestly (rungs, no price); a bars
    failure degrades to a note, never a dead map."""
    from datetime import date as _date
    from datetime import timedelta

    from harness.finance.corpus.reader import OpenOrder
    from harness.finance.levels import support_levels
    from harness.finance.models import SymbolLadder, TrapRung

    seed = svc.reader.read_portfolio()
    as_of = datetime.now().isoformat(timespec="seconds")
    if not seed.open_orders:
        return TrapMap(as_of=as_of, notes=["no resting orders — the slate is empty"])

    by_sym: dict[str, list[OpenOrder]] = {}
    for o in seed.open_orders:
        by_sym.setdefault(o.symbol, []).append(o)

    try:
        quotes = {q.symbol: q for q in svc.quote(sorted(by_sym))}
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
            bars = svc.history(sym, start=start)
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
