"""The pulse pass — `hn finance pulse`: orders, traps, index and macro proximity, filings, the
watchman flags. A function of the service (it reads quotes, the corpus and the calendar through it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:

    from harness.finance.watch import PulseReport

from harness.finance.models import (
    Quote,
)
from harness.finance.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.finance.service import FinanceService

def pulse(svc: FinanceService,  *, mark_seen: bool = True) -> PulseReport:
    """The scheduled-agent pulse: the watch digest + open-order trap distances +
    DETERMINISTIC flags (thresholds from portfolio.yaml `pulse:`). quiet=True means a cron run
    ends silently; flags mean notify. Detection lives here; the model only narrates."""
    from harness.finance.watch import OpenOrderStatus, PulseFlag, PulseReport

    digest = svc.watch(mark_seen=mark_seen)
    seed = svc.reader.read_portfolio()
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
            quotes = {q.symbol: q for q in svc.quote([o.symbol for o in seed.open_orders])}
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
            indexes = svc.quote(seed.index_watch)
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

    # the release calendar: high-tier rows join macro_soon beside the ledger,
    # and a release whose number is OUT today becomes a macro_print flag carrying it. Online
    # but TTL-cached (today 30 min, future 12 h); a dead source is a skip, never a dead pulse.
    from harness.finance.calendar import build_calendar, covered_by_ledger, dedup_key, todays_prints

    try:
        # the full two-week window (not just macro_days): pulse is what keeps the cache warm
        # for the gates board, which reads it offline. Future days are 12h-TTL — ~2 fetches/day.
        cal = build_calendar(14, today=today)
    except Exception:  # noqa: BLE001 — the standing-agent doctrine: one bad source ≠ a dead run
        cal = None
    cal_fomc_today = False
    if cal is not None:
        ledger = [(ev.date, ev.label) for ev in seed.macro_events]
        seen_fam: set[tuple[str, str | None]] = set()
        for ce in cal.events:
            if ce.tier != "high" or covered_by_ledger(ce, ledger) or dedup_key(ce) in seen_fam:
                continue
            seen_fam.add(dedup_key(ce))
            d = (_date.fromisoformat(ce.date) - today).days
            if 0 <= d <= macro_days and not ce.actual:
                when = "TODAY" if d == 0 else f"in {d}d"
                flags.append(PulseFlag(
                    kind="macro_soon", symbol="MACRO", message=f"{ce.label} {when} ({ce.date})",
                ))
        for ce in todays_prints(cal, today):
            flags.append(PulseFlag(kind="macro_print", symbol="MACRO", message=ce.print_line()))
        cal_fomc_today = any(
            ce.family == "FOMC" and ce.date == today.isoformat() for ce in cal.events
        )

    if cal_fomc_today or any(
        "fomc" in ev.label.lower() and _is_today(ev.date) for ev in seed.macro_events
    ):
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

    # vest_approaching: a vest within `vest_days` — it spikes
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
            # skip empty/zero-cost rows: a qty-0 DISPOSITION TOMBSTONE (a vest delivered then
            # fully sold — ledgered so vest reconciliation can match the calendar) or a zero-cost
            # lot (gifted/promo shares) has no flip semantics — guard the division so a zero
            # basis can't crash pulse.
            if not lot.qty or lot.unit_cost <= 0:
                continue
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
