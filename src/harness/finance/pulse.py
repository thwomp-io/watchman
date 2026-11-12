"""The pulse: `hn finance pulse`, the pass a schedule runs through the market day.

It takes the watch digest, adds the resting orders and the index tape, and runs a fixed list of
detectors over them. Each detector is independent and returns the flags it found; `pulse` runs them
in the order the flags are published. A run with no flags is quiet, and a quiet run notifies no one.

Every flag is deterministic. Thresholds come from `pulse:` in portfolio.yaml (`Thresholds` holds the
defaults), detection lives here, and a model only narrates the result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol, Self

from pydantic import BaseModel, Field

from harness.finance import calendar as macro_calendar
from harness.finance import fed
from harness.finance.models import Quote
from harness.finance.providers.base import ProviderError
from harness.finance.watch import WatchDigest

if TYPE_CHECKING:
    from harness.finance.calendar import Calendar
    from harness.finance.corpus.reader import Holding, Lot, PortfolioSeed, VestEvent
    from harness.finance.service import FinanceService


class PulseFlag(BaseModel):
    """One thing worth a person's attention. `kind` names the detector's finding (day_move,
    trap_proximity, print_soon, …) and, with `symbol`, keys the once-a-day dedup on the bus."""

    kind: str
    symbol: str
    message: str
    # Which of several same-kind, same-symbol flags this is (two releases on one day are both
    # macro_soon/MACRO). It joins the dedup key, so each is published; empty when kind and symbol
    # already identify the flag.
    topic: str = ""


class OpenOrderStatus(BaseModel):
    """A resting order against the live price."""

    symbol: str
    side: str
    qty: float
    limit: float
    price: float | None = None
    # How far the price is from the limit, as a % of the price, signed so that smaller is always
    # closer to filling: a buy fills as the price falls to the limit, a sell as it rises to it.
    distance_pct: float | None = None
    day_pct: float | None = None  # the underlying's day move
    expires: str = ""
    note: str = ""


class PulseReport(BaseModel):
    """What a scheduled run reads: `quiet` means end silently, flags mean notify."""

    as_of: str
    quiet: bool = True
    flags: list[PulseFlag] = Field(default_factory=list)
    orders: list[OpenOrderStatus] = Field(default_factory=list)
    indexes: list[Quote] = Field(default_factory=list)
    digest: WatchDigest | None = None


@dataclass(frozen=True)
class Thresholds:
    """When each detector fires. Percentages are absolute moves; `*_days` are look-ahead windows,
    except the vest-reconcile pair, which look back."""

    day_move_pct: float = 5.0
    trap_proximity_pct: float = 1.0
    print_days: float = 3.0
    index_move_pct: float = 1.5
    macro_days: float = 2.0
    filing_days: float = 5.0
    vest_days: float = 7.0
    vest_reconcile_grace_days: int = 2
    vest_reconcile_window_days: int = 45
    tax_days: float = 7.0
    lot_flip_pct: float = 2.0

    @classmethod
    def from_config(cls, config: dict[str, float]) -> Self:
        """The defaults, overridden by whichever keys the portfolio's `pulse:` block sets."""
        set_by_user: dict[str, Any] = {
            name: type(getattr(cls, name))(config[name])  # the YAML may say 2 where a float is meant
            for name in cls.__dataclass_fields__
            if name in config
        }
        return cls(**set_by_user)


class _Dated(Protocol):
    @property
    def date(self) -> str: ...


def pulse(svc: FinanceService, *, mark_seen: bool = True) -> PulseReport:
    digest = svc.watch(mark_seen=mark_seen)
    seed = svc.reader.read_portfolio()
    th = Thresholds.from_config(seed.pulse_thresholds)
    today = date.today()

    orders = open_orders(svc, seed)
    indexes = _quotes_or_empty(svc, seed.index_watch)
    calendar = _calendar_or_none(today)
    # The holding that vests on a schedule is the one carrying per-lot basis.
    vesting = next((h for h in seed.holdings if h.lots), None)
    vesting_price = _day_price(digest, vesting.symbol) if vesting else None

    flags = [
        *_day_moves(digest, th.day_move_pct),
        *_traps(orders, th.trap_proximity_pct),
        *_prints_soon(digest, th.print_days),
        *_index_moves(indexes, th.index_move_pct),
        *_ledger_macro(seed, th.macro_days, today),
        *_calendar_macro(calendar, seed, th.macro_days, today),
        *_fed_decision(calendar, seed, today),
        *_filings(digest, seed, th.filing_days, today),
    ]
    if vesting:
        flags += _vests(seed, vesting, vesting_price, th.vest_days, today)
        flags += vest_reconciliation_flags(
            seed.vest_calendar, vesting.lots,
            symbol=vesting.symbol,
            grace_days=th.vest_reconcile_grace_days,
            window_days=th.vest_reconcile_window_days,
        )
    flags += _tax_deadlines(seed, th.tax_days, today)
    if vesting and vesting_price:
        flags += _lot_flips(vesting, vesting_price, th.lot_flip_pct)
    flags += _tlh_window(digest, vesting.symbol if vesting else None)

    return PulseReport(
        as_of=digest.as_of, quiet=not flags, flags=flags, orders=orders,
        indexes=indexes, digest=digest,
    )


# --- inputs ----------------------------------------------------------------------------------------


def _quotes_or_empty(svc: FinanceService, symbols: list[str]) -> list[Quote]:
    """Quotes for `symbols`; a provider failure is an empty list, never a dead pulse run."""
    if not symbols:
        return []
    try:
        return svc.quote(symbols)
    except ProviderError:
        return []


def open_orders(svc: FinanceService, seed: PortfolioSeed) -> list[OpenOrderStatus]:
    """Each resting order in the ledger with its live price and how far that is from the limit.
    Costs one quote request; an order whose symbol has no quote keeps its row, unpriced."""
    quotes = {q.symbol: q for q in _quotes_or_empty(svc, [o.symbol for o in seed.open_orders])}
    out = []
    for o in seed.open_orders:
        q = quotes.get(o.symbol)
        price = q.price if q else None
        dist: float | None = None
        if price:
            raw = (price - o.limit) / price * 100.0
            dist = raw if o.side == "buy" else -raw
        day_pct = round(q.day_change_pct, 2) if q and q.day_change_pct is not None else None
        out.append(OpenOrderStatus(
            symbol=o.symbol, side=o.side, qty=o.qty, limit=o.limit,
            price=price, distance_pct=dist, day_pct=day_pct, expires=o.expires, note=o.note,
        ))
    return out


def _calendar_or_none(today: date) -> Calendar | None:
    """The release calendar for the next two weeks, or None when its sources are down.

    Two weeks, not just the macro look-ahead: this fetch is what keeps the calendar's cache warm for
    the gates board, which reads it offline.
    """
    try:
        return macro_calendar.build_calendar(14, today=today)
    except Exception:  # noqa: BLE001 — one dead source must not end a scheduled run
        return None


def _day_price(digest: WatchDigest, symbol: str) -> float | None:
    return next((p.price for p in digest.day_moves if p.symbol == symbol and p.price is not None), None)


def _due_within[D: _Dated](items: Iterable[D], days: float, today: date) -> Iterator[tuple[D, int]]:
    """(item, days until it) for each dated item from today through `days` out; an unparseable date
    is skipped."""
    for item in items:
        try:
            d = (date.fromisoformat(item.date) - today).days
        except ValueError:
            continue
        if 0 <= d <= days:
            yield item, d


def _is_on(iso: str, day: date) -> bool:
    try:
        return date.fromisoformat(iso) == day
    except ValueError:
        return False


def _when(days: int) -> str:
    return "TODAY" if days == 0 else f"in {days}d"


# --- detectors -------------------------------------------------------------------------------------


def _day_moves(digest: WatchDigest, threshold: float) -> list[PulseFlag]:
    return [
        PulseFlag(
            kind="day_move", symbol=p.symbol,
            message=f"{p.symbol} {p.day_change_pct:+.1f}% on the day (threshold ±{threshold:g}%)",
        )
        for p in digest.day_moves
        if p.day_change_pct is not None and abs(p.day_change_pct) >= threshold
    ]


def _traps(orders: list[OpenOrderStatus], threshold: float) -> list[PulseFlag]:
    return [
        PulseFlag(
            kind="trap_proximity", symbol=o.symbol,
            message=f"{o.side.upper()} {o.symbol} @ {o.limit:g} is {o.distance_pct:.1f}% from "
                    f"the live price ({o.price:.2f}) — fill plausible",
        )
        for o in orders
        if o.distance_pct is not None and o.price is not None and o.distance_pct <= threshold
    ]


def _prints_soon(digest: WatchDigest, days: float) -> list[PulseFlag]:
    return [
        PulseFlag(
            kind="print_soon", symbol=p.symbol, message=f"{p.symbol} print ≈ {p.days_out}d out ({p.estimate})"
        )
        for p in digest.prints
        if p.days_out is not None and p.days_out <= days
    ]


def _index_moves(indexes: list[Quote], threshold: float) -> list[PulseFlag]:
    """The tape itself: a broad index move can signal a regime shift the per-holding checks miss."""
    return [
        PulseFlag(
            kind="index_move", symbol=q.symbol,
            message=f"{q.symbol} {q.day_change_pct:+.1f}% — index-level move (threshold ±{threshold:g}%)",
        )
        for q in indexes
        if q.day_change_pct is not None and abs(q.day_change_pct) >= threshold
    ]


def _ledger_macro(seed: PortfolioSeed, days: float, today: date) -> list[PulseFlag]:
    """Macro events the user put in the ledger by hand."""
    return [
        PulseFlag(
            kind="macro_soon", symbol="MACRO", topic=f"{ev.label} {ev.date}",
            message=f"{ev.label} {_when(d)} ({ev.date})",
        )
        for ev, d in _due_within(seed.macro_events, days, today)
    ]


def _calendar_macro(
    calendar: Calendar | None, seed: PortfolioSeed, days: float, today: date
) -> list[PulseFlag]:
    """High-tier releases from the calendar: macro_soon for one coming up that the ledger doesn't
    already carry, and macro_print, with the number, for one that is out today.

    Fed speakers are one flag per day, naming each with their time. A heavy speaking week otherwise
    puts a flag per speech in front of the releases; the Chair keeps a flag of their own.
    """
    if calendar is None:
        return []
    flags: list[PulseFlag] = []
    ledger = [(ev.date, ev.label) for ev in seed.macro_events]
    seen_families: set[tuple[str, str | None]] = set()
    speakers: dict[str, list[str]] = {}  # date → "Name HH:MM", in calendar order
    for ce in calendar.events:
        if (
            ce.tier != "high"
            or macro_calendar.covered_by_ledger(ce, ledger)
            or macro_calendar.dedup_key(ce) in seen_families
        ):
            continue
        seen_families.add(macro_calendar.dedup_key(ce))
        d = (date.fromisoformat(ce.date) - today).days
        if not (0 <= d <= days) or ce.actual:
            continue
        if ce.family == "Fed speaker":
            if ce.date not in speakers:
                # the day's flag takes the place of its first speaker, so the order stays by date
                flags.append(PulseFlag(
                    kind="macro_soon", symbol="MACRO", topic=f"Fed speakers {ce.date}", message=ce.date
                ))
            speakers.setdefault(ce.date, []).append(_speaker(ce.name, ce.time_et))
        else:
            flags.append(PulseFlag(
                kind="macro_soon", symbol="MACRO", topic=f"{ce.family} {ce.date}",
                message=f"{ce.label} {_when(d)} ({ce.date})",
            ))
    for flag in flags:
        if flag.message in speakers:
            day = flag.message
            d = (date.fromisoformat(day) - today).days
            flag.message = f"Fed speakers {_when(d)} ({day}): {', '.join(speakers[day])} ET"
    for ce in macro_calendar.todays_prints(calendar, today):
        flags.append(PulseFlag(kind="macro_print", symbol="MACRO", topic=ce.name, message=ce.print_line()))
    return flags


_SPEAKER = re.compile(
    r"^(?:fed|fomc member)\s+(?:vice chair(?: for supervision)?\s+)?(?P<who>.+?)\s+speaks\b", re.IGNORECASE
)


def _speaker(event_name: str, time_et: str | None) -> str:
    """'FOMC Member Williams Speaks' at 05:15 → 'Williams 05:15'. A name in a form the pattern
    doesn't know is kept whole rather than guessed at."""
    m = _SPEAKER.match(event_name)
    who = m.group("who") if m else event_name
    return f"{who} {time_et}" if time_et else who


def _is_fomc_day(calendar: Calendar | None, seed: PortfolioSeed, today: date) -> bool:
    """Today has an FOMC decision, by the calendar or by a ledger entry naming one."""
    on_calendar = calendar is not None and any(
        ce.family == "FOMC" and ce.date == today.isoformat() for ce in calendar.events
    )
    in_ledger = any("fomc" in ev.label.lower() and _is_on(ev.date, today) for ev in seed.macro_events)
    return on_calendar or in_ledger


def _fed_decision(calendar: Calendar | None, seed: PortfolioSeed, today: date) -> list[PulseFlag]:
    """On an FOMC day, once the statement is out, the actual decision.

    The Fed is only asked on an FOMC day. Before the statement drops, the feed's newest item is the
    prior meeting, so its release date isn't today and nothing fires; the bus's once-per-day dedup
    keeps the flag to one. A dead feed is a skip.
    """
    if not _is_fomc_day(calendar, seed, today):
        return []
    try:
        decision = fed.fetch_fomc_decision()
    except ProviderError:
        return []
    if not decision or decision.released[:10] != today.isoformat():
        return []
    rate = f"target range {decision.target_rate}" if decision.target_rate else "see statement"
    vote = f", vote {decision.vote}" if decision.vote else ""
    snippet = decision.statement_text[:140].rstrip()
    return [PulseFlag(
        kind="fed_decision", symbol="MACRO", message=f"FOMC decision is OUT — {rate}{vote}. {snippet}…",
    )]


def _filings(digest: WatchDigest, seed: PortfolioSeed, days: float, today: date) -> list[PulseFlag]:
    """A recent EDGAR filing for a held symbol. A results 8-K (item 2.02, named in the title) is
    sharpened to print_landed: the print actually dropped.

    Only filings from the last `days` count. A newly added holding has no seen-cache baseline, so
    without the gate its whole filing history would fire as fresh.
    """

    def recent(published: str) -> bool:
        try:
            age = (today - date.fromisoformat(published[:10])).days
        except (ValueError, TypeError):
            return True  # an unparseable date fails open: surface it rather than drop it silently
        return 0 <= age <= days

    held = {h.symbol for h in seed.holdings}
    flags = []
    for item in digest.fresh_news or []:
        if item.source != "sec.gov" or item.symbol not in held or not recent(item.published):
            continue
        if "2.02" in item.title:
            flags.append(PulseFlag(
                kind="print_landed", symbol=item.symbol,
                message=f"{item.symbol} REPORTED — 8-K item 2.02 (results of operations); "
                        f"check the thesis ({item.published})",
            ))
        else:
            flags.append(PulseFlag(
                kind="filing_drop", symbol=item.symbol, topic=item.url or item.title,
                message=f"{item.symbol}: {item.title} ({item.published})",
            ))
    return flags


def _vests(
    seed: PortfolioSeed, vesting: Holding, price: float | None, days: float, today: date
) -> list[PulseFlag]:
    """A vest within `days`. A vest raises concentration, resets the ±30-day wash-sale window, and
    opens a supplemental under-withholding gap."""
    sym = vesting.symbol
    flags = []
    for v, d in _due_within(seed.vest_calendar, days, today):
        est = f" ~${v.units * price:,.0f}" if price else ""
        flags.append(PulseFlag(
            kind="vest_approaching", symbol=sym, topic=v.date,
            message=f"{sym} vest {_when(d)} ({v.date}): {v.units} sh{est} — concentration "
                    f"rises, wash-poison resets ±30d, supplemental under-withholding gap",
        ))
    return flags


def vest_reconciliation_flags(
    vest_calendar: Sequence[VestEvent],
    lots: Sequence[Lot],
    *,
    symbol: str,
    today: date | None = None,
    grace_days: int = 2,
    window_days: int = 45,
    match_days: int = 5,
) -> list[PulseFlag]:
    """The vest calendar checked against the ledgered lots, in both directions.

    A vest can be delivered at the broker without the portfolio's lots ever being updated: brokers
    often expose no API, the ledger is synced by hand, and `vest_approaching` fires before the event,
    so nothing looks afterwards. Both checks compare config with config; nothing is fetched.

    - vest_unreconciled: a calendar vest older than `grace_days` (posting lag) with no lot acquired
      within `match_days` after it. It repeats daily until the lot is ledgered or the vest ages
      past `window_days`.
    - unscheduled_lot: the inverse, a recent lot with no calendar vest just before it. The calendar
      is missing a row.
    """
    today = today or date.today()

    def _d(s: str) -> date | None:
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    lot_dates = [(d, lot) for lot in lots if (d := _d(lot.acquired)) is not None]
    flags: list[PulseFlag] = []

    for v in vest_calendar:
        vd = _d(v.date)
        if vd is None:
            continue
        age = (today - vd).days
        if age <= grace_days or age > window_days:
            continue
        matched = any(0 <= (ld - vd).days <= match_days for ld, _ in lot_dates)
        if not matched:
            flags.append(PulseFlag(
                kind="vest_unreconciled", symbol=symbol, topic=v.date,
                message=f"{symbol} vest {v.date} ({v.units}u) has NO ledgered lot {age}d later — "
                        "verify on the broker's vesting/stock-plan screen and sync the "
                        "portfolio lots (a vest is confirmed by the DELIVERED LOT, not the calendar)",
            ))

    vest_dates = [vd for v in vest_calendar if (vd := _d(v.date)) is not None]
    for ld, lot in lot_dates:
        age = (today - ld).days
        if age < 0 or age > window_days:
            continue
        matched = any(0 <= (ld - vd).days <= match_days for vd in vest_dates)
        if not matched:
            flags.append(PulseFlag(
                kind="unscheduled_lot", symbol=symbol, topic=lot.acquired,
                message=f"{symbol} lot acquired {lot.acquired} ({lot.qty:g} sh) matches NO calendar "
                        "vest — reconcile the vest calendar (the unreconciled-vest guard's inverse)",
            ))
    return flags


def _tax_deadlines(seed: PortfolioSeed, days: float, today: date) -> list[PulseFlag]:
    """A dated tax action (a withholding election, a 1040-ES payment): real money, easy to forget."""
    return [
        PulseFlag(
            kind="tax_deadline", symbol="TAX", topic=f"{ev.label} {ev.date}",
            message=f"{ev.label} {_when(d)} ({ev.date})",
        )
        for ev, d in _due_within(seed.tax_events, days, today)
    ]


def _lot_flips(vesting: Holding, price: float, threshold: float) -> list[PulseFlag]:
    """A lot within `threshold`% of its basis is near a gain/loss flip, which changes whether it can
    be sold: a gain sells any time, a loss is a wash-gated harvest.

    A qty-0 row (a vest delivered and fully sold, kept so reconciliation can match the calendar) and
    a zero-cost lot have no flip to speak of, and the zero basis would divide by zero.
    """
    sym = vesting.symbol
    flags = []
    for lot in vesting.lots:
        if not lot.qty or lot.unit_cost <= 0:
            continue
        gl_pct = (price - lot.unit_cost) / lot.unit_cost * 100.0
        if abs(gl_pct) <= threshold:
            flags.append(PulseFlag(
                kind="lot_flip", symbol=sym, topic=lot.acquired,
                message=f"{sym} lot {lot.acquired} ({lot.qty:.0f} sh) {gl_pct:+.1f}% vs "
                        f"basis ${lot.unit_cost:.2f} — near gain/loss flip (sell-eligibility)",
            ))
    return flags


def _tlh_window(digest: WatchDigest, symbol: str | None) -> list[PulseFlag]:
    """A clean wash-sale window is open and there is a loss to harvest. Silent while the window is
    poisoned: this is the harvest nudge, not a status line."""
    ws = digest.wash_sale
    if not ws or ws.today_poisoned or ws.harvestable_loss is None or ws.harvestable_loss >= 0:
        return []
    until = f" (clean through {ws.next_clean_end})" if ws.next_clean_end else ""
    label = symbol or "concentrated"
    return [PulseFlag(
        kind="tlh_window", symbol=label,
        message=f"{label} TLH window OPEN — ${abs(ws.harvestable_loss):,.0f} harvestable "
                f"across {ws.harvestable_shares:,.0f} sh{until}",
    )]
