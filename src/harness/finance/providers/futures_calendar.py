"""Futures contract resolution — which DATED contract a continuous `=F` rail row quotes today.

Why this exists: Yahoo's continuous aliases (`BZ=F`, `CL=F`, `ES=F`…) silently change which
contract month they serve near expiry, and not always on a schedule — `BZ=F` flipped between
the November and December Brent contracts twice in one session, and on a flip its price and its
previous close can come from DIFFERENT months, so a ~$4 backwardation gap prints as a −4% "day".
The continuous
bar series is spliced the same way (the day a WTI month expires, `CL=F`'s history is the old
month's closes followed by the new month's print).

The fix is to never trust the alias: every futures row resolves to an explicit dated contract
(`BZX26.NYM` = Brent November 2026) chosen by a published rule, and its day change is measured
against THAT contract's own previous settle. A consistent series matters more than the "right"
month — the rule is front-month-until-the-switch-date, the chosen month is printed on every row,
and the day the rule changes months is flagged as a roll day instead of passing silently.

Pure and deterministic (a date in, a contract out), so it is unit-tested without a network. Known
simplification: business days are weekdays — exchange holidays are not modeled, so a computed
switch date can land one trading day off around a holiday. That is harmless by construction: the
day % is always same-contract, and the month is named on the row.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

# Exchange month codes, January → December.
MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _is_bday(d: date) -> bool:
    return d.weekday() < 5


def add_bdays(d: date, n: int) -> date:
    """Step `n` weekdays from `d` (negative = backward). `n == 0` returns `d` unchanged."""
    step = 1 if n > 0 else -1
    remaining = abs(n)
    cur = d
    while remaining:
        cur += timedelta(days=step)
        if _is_bday(cur):
            remaining -= 1
    return cur


def last_bday_of_month(year: int, month: int) -> date:
    d = date(year, month, calendar.monthrange(year, month)[1])
    while not _is_bday(d):
        d -= timedelta(days=1)
    return d


def _prev_month(year: int, month: int, back: int = 1) -> tuple[int, int]:
    idx = year * 12 + (month - 1) - back
    return idx // 12, idx % 12 + 1


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # Friday = 4
    return first + timedelta(days=offset + 14)


# ---- reference-day rules: the exchange's last-trade (or first-notice) day for a contract month ----

def _wti_last_trade(year: int, month: int) -> date:
    """NYMEX WTI crude: 3 business days before the 25th of the month BEFORE delivery (4 if the
    25th is not a business day)."""
    py, pm = _prev_month(year, month)
    d25 = date(py, pm, 25)
    return add_bdays(d25, -3 if _is_bday(d25) else -4)


def _brent_last_trade(year: int, month: int) -> date:
    """NYMEX BZ (Brent last-day financial, mirrors ICE Brent): the last business day of the
    SECOND month before delivery."""
    py, pm = _prev_month(year, month, back=2)
    return last_bday_of_month(py, pm)


def _natgas_last_trade(year: int, month: int) -> date:
    """NYMEX NG: 3 business days before the first calendar day of the delivery month."""
    return add_bdays(date(year, month, 1), -3)


def _prior_month_last_bday(year: int, month: int) -> date:
    """RBOB last trade; also the first-notice proxy for the physically-delivered metals + notes:
    the last business day of the month before the contract month."""
    py, pm = _prev_month(year, month)
    return last_bday_of_month(py, pm)


def _index_last_trade(year: int, month: int) -> date:
    """Equity-index futures (E-mini S&P / Nasdaq / Russell, mini Dow): the third Friday of the
    contract month."""
    return third_friday(year, month)


@dataclass(frozen=True)
class FuturesRoot:
    """One continuous rail symbol and the rule that picks its dated contract."""

    root: str  # the exchange root in Yahoo's dated form (BZ for Brent, ES for the E-mini S&P…)
    exchange: str  # Yahoo's dated-symbol suffix (NYM = NYMEX, CBT = CBOT, CMX = COMEX, …)
    months: tuple[int, ...]  # the listed/liquid cycle the rail follows (1-12)
    reference: Callable[[int, int], date]  # the rule's reference day for (year, month)
    switch_bdays: int  # switch to the next contract this many business days BEFORE the reference
    rule: str  # the rule in words — printed in docs + the close reason


# The rail's futures, one rule each. Energy switches ON the expiring month's last trading day
# (thin final session; the benchmark press moves on then too); equity index + the 2-yr note
# switch on the standard quarterly roll (index: the Thursday before the third Friday; notes:
# ahead of first notice); metals follow their liquid cycle and switch ahead of first notice.
_MONTHLY = tuple(range(1, 13))
_QUARTERLY = (3, 6, 9, 12)  # H / M / U / Z

FUTURES_ROOTS: dict[str, FuturesRoot] = {
    "CL=F": FuturesRoot(
        root="CL", exchange="NYM", months=_MONTHLY, reference=_wti_last_trade, switch_bdays=0,
        rule="WTI: front month until its last trading day (3 bdays before the 25th of "
        "the prior month), then the next month"),
    "BZ=F": FuturesRoot(
        root="BZ", exchange="NYM", months=_MONTHLY, reference=_brent_last_trade, switch_bdays=0,
        rule="Brent: front month until its last trading day (last bday of the second "
        "month before delivery), then the next month"),
    "NG=F": FuturesRoot(
        root="NG", exchange="NYM", months=_MONTHLY, reference=_natgas_last_trade, switch_bdays=0,
        rule="Nat gas: front month until its last trading day (3 bdays before the "
        "delivery month), then the next month"),
    "RB=F": FuturesRoot(
        root="RB", exchange="NYM", months=_MONTHLY, reference=_prior_month_last_bday, switch_bdays=0,
        rule="RBOB: front month until its last trading day (last bday of the prior "
        "month), then the next month"),
    "HO=F": FuturesRoot(
        root="HO", exchange="NYM", months=_MONTHLY, reference=_prior_month_last_bday, switch_bdays=0,
        rule="ULSD (heating oil): front month until its last trading day (last bday of the "
        "prior month), then the next month"),
    "ES=F": FuturesRoot(
        root="ES", exchange="CME", months=_QUARTERLY, reference=_index_last_trade, switch_bdays=6,
        rule="E-mini S&P: quarterly (H/M/U/Z), rolls the Thursday before the third-"
        "Friday expiry"),
    "NQ=F": FuturesRoot(
        root="NQ", exchange="CME", months=_QUARTERLY, reference=_index_last_trade, switch_bdays=6,
        rule="E-mini Nasdaq: quarterly, rolls the Thursday before expiry"),
    "YM=F": FuturesRoot(
        root="YM", exchange="CBT", months=_QUARTERLY, reference=_index_last_trade, switch_bdays=6,
        rule="Mini Dow: quarterly, rolls the Thursday before expiry"),
    "RTY=F": FuturesRoot(
        root="RTY", exchange="CME", months=_QUARTERLY, reference=_index_last_trade, switch_bdays=6,
        rule="E-mini Russell: quarterly, rolls the Thursday before expiry"),
    "ZT=F": FuturesRoot(
        root="ZT", exchange="CBT", months=_QUARTERLY, reference=_prior_month_last_bday, switch_bdays=2,
        rule="2-yr note: quarterly, rolls 2 bdays before first notice (last bday of "
        "the prior month)"),
    "GC=F": FuturesRoot(
        root="GC", exchange="CMX", months=(2, 4, 6, 8, 12), reference=_prior_month_last_bday, switch_bdays=2,
        rule="Gold: liquid cycle G/J/M/Q/Z, rolls 2 bdays before first notice"),
    "SI=F": FuturesRoot(
        root="SI", exchange="CMX", months=(3, 5, 7, 9, 12), reference=_prior_month_last_bday, switch_bdays=2,
        rule="Silver: liquid cycle H/K/N/U/Z, rolls 2 bdays before first notice"),
    "HG=F": FuturesRoot(
        root="HG", exchange="CMX", months=(3, 5, 7, 9, 12), reference=_prior_month_last_bday, switch_bdays=2,
        rule="Copper: liquid cycle H/K/N/U/Z, rolls 2 bdays before first notice"),
    "PL=F": FuturesRoot(
        root="PL", exchange="NYM", months=(1, 4, 7, 10), reference=_prior_month_last_bday, switch_bdays=2,
        rule="Platinum: liquid cycle F/J/N/V, rolls 2 bdays before first notice"),
}


@dataclass(frozen=True)
class Contract:
    """A resolved dated contract."""

    symbol: str  # Yahoo's dated symbol, e.g. BZX26.NYM
    year: int
    month: int
    switch_on: date  # the first date the rule quotes the NEXT contract instead

    @property
    def label(self) -> str:
        """'Nov 26' — the month as the rows print it."""
        return f"{MONTH_ABBR[self.month - 1]} {self.year % 100:02d}"


def _contract(spec: FuturesRoot, year: int, month: int) -> Contract:
    sym = f"{spec.root}{MONTH_CODES[month - 1]}{year % 100:02d}.{spec.exchange}"
    switch = add_bdays(spec.reference(year, month), -spec.switch_bdays)
    return Contract(sym, year, month, switch)


def active_contract(continuous: str, on: date) -> Contract | None:
    """The dated contract the rule quotes for `continuous` on date `on`; None = not a mapped
    future (the caller quotes the symbol as-is)."""
    spec = FUTURES_ROOTS.get(continuous)
    if spec is None:
        return None
    # Walk the cycle forward from the current month; the first contract whose switch date is
    # still ahead is the active one. 24 steps covers any cycle with room to spare.
    y, m = on.year, on.month
    for _ in range(24):
        if m in spec.months:
            c = _contract(spec, y, m)
            if on < c.switch_on:
                return c
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None  # unreachable for a sane table; the caller falls back to the alias


def previous_bday(d: date) -> date:
    return add_bdays(d, -1)


def roll_check(continuous: str, on: date) -> tuple[Contract | None, Contract | None]:
    """(today's contract, the contract it rolled FROM if `on` is a roll day else None).

    Stateless: a roll day is a date whose rule answer differs from the previous business
    day's — no state file, identical on every machine and every re-run."""
    today = active_contract(continuous, on)
    prior = active_contract(continuous, previous_bday(on))
    if today is not None and prior is not None and prior.symbol != today.symbol:
        return today, prior
    return today, None
