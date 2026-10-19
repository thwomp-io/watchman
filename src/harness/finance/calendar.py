"""The macro release calendar — `hn finance calendar` and the rows gates + pulse merge in.

One dated schedule from primary sources (see providers/calendar_provider.py): the economic
releases (with the numbers once printed), FOMC decisions (+ the minutes, estimated three weeks on
until the release date is published), and coupon auctions. Tiered: `high` = the releases that move
the tape (CPI, payrolls, PCE, GDP, the Fed…); everything else is `normal` and shows with `--all`.

Machine-sourced rows never touch the hand-kept `macro_events` ledger: the gates board and pulse
MERGE them, skipping a calendar row when a ledger row on the same date already names the same
release (the ledger's wording wins — it carries the operator's context).

Cached under the harness cache dir: a past day's rows are final; today refreshes every 30 minutes
(actuals land through the day); future days every 12 hours; the Fed page weekly; auctions every
6 hours. `--refresh` ignores the cache.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from harness.errors import ProviderError
from harness.paths import cache_file

CACHE = "macro-calendar.json"
_TTL_TODAY, _TTL_FUTURE, _TTL_FED, _TTL_TD = 1800, 12 * 3600, 7 * 86400, 6 * 3600
_PACE = 0.3  # seconds between Nasdaq day calls

# The releases that move the tape. Matched case-insensitively against the source's event name;
# order matters only for the short label (the first match names the row's family).
HIGH_TIER: list[tuple[str, str]] = [
    (r"^core cpi|^cpi\b", "CPI"),
    (r"^core ppi|^ppi\b", "PPI"),
    (r"nonfarm payrolls", "Payrolls"),
    (r"^unemployment rate", "Unemployment"),
    (r"average hourly earnings", "Wages"),
    (r"^core pce|pce price index", "PCE"),
    (r"^gdp\b", "GDP"),
    (r"^retail sales|^core retail sales", "Retail sales"),
    (r"^ism (manufacturing|non-manufacturing|services) pmi", "ISM"),
    # S&P Global's flash PMIs print ~9:45 ET mid-month and can move rates on their own
    (r"^s&p global (manufacturing|services|composite) pmi", "S&P PMI"),
    (r"^jolts job openings", "JOLTS"),
    (r"^initial jobless claims", "Jobless claims"),
    (r"fed interest rate decision|fomc statement|fomc economic projections", "FOMC"),
    (r"fomc meeting minutes", "FOMC minutes"),
    (r"fed chair .* speaks", "Fed Chair"),
    # any scheduled Fed Board / regional-president remarks (they move rate odds); not "Fed's
    # Balance Sheet" or the regional Fed surveys
    (r"^(fed|fomc member)\b.*\bspeaks\b|^fed vice chair\b.*\bspeaks\b", "Fed speaker"),
    (r"michigan consumer sentiment", "UMich sentiment"),
    (r"^cb consumer confidence", "Consumer confidence"),
    (r"^crude oil inventories", "EIA crude"),
    (r"^durable goods orders", "Durable goods"),
]


@dataclass
class CalEvent:
    date: str  # ISO
    name: str
    source: str  # nasdaq | fed | treasury
    tier: str  # high | normal
    family: str | None = None  # the HIGH_TIER family ("CPI"…) — the dedup key vs the ledger
    time_et: str | None = None
    actual: str | None = None
    consensus: str | None = None
    previous: str | None = None
    confirmed: bool = True  # False = an estimate (FOMC minutes before the Fed publishes the date)
    # a data release past its time (+10 min) with no actual yet: it PRINTED, the source hasn't posted
    # the number (Nasdaq can lag hours on licensed releases) — read the wire
    awaiting_actual: bool = False

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.time_et:
            bits.append(f"{self.time_et} ET")
        if self.actual:
            bits.append(f"actual {self.actual}")
        elif self.awaiting_actual:
            bits.append("printed, actual not posted yet")
        if self.consensus:
            bits.append(f"cons {self.consensus}")
        if self.previous and not self.actual:
            bits.append(f"prev {self.previous}")
        return " · ".join(bits) + ("" if self.confirmed else " (est)")

    def print_line(self) -> str:
        """The released-number line pulse carries: 'CPI m/m 0.4% (cons 0.3%, prev 0.2%)'."""
        ctx = ", ".join(x for x in (f"cons {self.consensus}" if self.consensus else "",
                                     f"prev {self.previous}" if self.previous else "") if x)
        return f"{self.name} {self.actual}" + (f" ({ctx})" if ctx else "")


@dataclass
class Calendar:
    start: str
    days: int
    events: list[CalEvent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def classify(name: str) -> tuple[str, str | None]:
    low = name.lower()
    for pattern, family in HIGH_TIER:
        if re.search(pattern, low):
            return "high", family
    return "normal", None


# ————— cache —————


def _load() -> dict[str, Any]:
    try:
        data = json.loads(cache_file(CACHE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    p = cache_file(CACHE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)


def _fresh(entry: dict[str, Any] | None, ttl: float, now: float) -> bool:
    return bool(entry) and now - float(entry.get("fetched", 0)) < ttl  # type: ignore[union-attr]


# ————— assembly —————


def build_calendar(
    days: int = 14, *, today: date | None = None, refresh: bool = False, offline: bool = False,
    now_et: str | None = None,
) -> Calendar:
    """The next `days` days (today included). `offline` = cache only, never fetch (the fast
    consumers: the gates widget, pulse between refreshes)."""
    from harness.finance.providers import calendar_provider as cp

    today = today or date.today()
    now = time.time()
    cache = {} if refresh else _load()
    cache.setdefault("days", {})
    cal = Calendar(start=today.isoformat(), days=days)
    dirty = False
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        # economic releases, day by day (Nasdaq publishes ~2 weeks ahead; an empty far day is normal)
        for i in range(days):
            d = today + timedelta(days=i)
            key = d.isoformat()
            entry = cache["days"].get(key)
            ttl = _TTL_TODAY if d == today else _TTL_FUTURE
            if not offline and not _fresh(entry, ttl, now):
                try:
                    if i:
                        time.sleep(_PACE)
                    rows = [asdict(r) for r in cp.fetch_econ_day(d, client)]
                    entry = cache["days"][key] = {"fetched": now, "rows": rows}
                    dirty = True
                except ProviderError as e:
                    cal.notes.append(str(e))
            for r in (entry or {}).get("rows", []):
                tier, fam = classify(r["name"])
                cal.events.append(CalEvent(
                    date=r["day"], name=r["name"], source="nasdaq", tier=tier, family=fam,
                    time_et=r.get("time_et"), actual=r.get("actual"), consensus=r.get("consensus"),
                    previous=r.get("previous"),
                ))
        # FOMC decisions from the Fed's own page (beyond Nasdaq's window) + estimated minutes
        fed = cache.get("fed")
        if not offline and not _fresh(fed, _TTL_FED, now):
            try:
                fed = cache["fed"] = {"fetched": now,
                                      "meetings": [asdict(m) for m in cp.fetch_fomc_meetings(client)]}
                dirty = True
            except ProviderError as e:
                cal.notes.append(str(e))
        end = today + timedelta(days=days - 1)
        have = {(e.date, e.family) for e in cal.events if e.family}
        for m in (fed or {}).get("meetings", []):
            dec = date.fromisoformat(m["decision_date"])
            if today <= dec <= end and (m["decision_date"], "FOMC") not in have:
                cal.events.append(CalEvent(
                    date=m["decision_date"], source="fed", tier="high", family="FOMC",
                    name="FOMC decision" + (" + projections (SEP)" if m.get("sep") else ""),
                    time_et="14:00",
                ))
            minutes = dec + timedelta(days=21)
            if today <= minutes <= end and not any(
                e.family == "FOMC minutes" and abs((date.fromisoformat(e.date) - minutes).days) <= 3
                for e in cal.events
            ):
                cal.events.append(CalEvent(
                    date=minutes.isoformat(), source="fed", tier="high", family="FOMC minutes",
                    name=f"FOMC minutes ({dec:%b %d} mtg)", time_et="14:00", confirmed=False,
                ))
        # coupon auctions
        td = cache.get("treasury")
        if not offline and not _fresh(td, _TTL_TD, now):
            try:
                td = cache["treasury"] = {"fetched": now,
                                          "auctions": [asdict(a) for a in cp.fetch_auctions(client)]}
                dirty = True
            except ProviderError as e:
                cal.notes.append(str(e))
        for a in (td or {}).get("auctions", []):
            if today.isoformat() <= a["day"] <= end.isoformat():
                amt = f" ${a['amount_bn']:g}B" if a.get("amount_bn") else ""
                reopen = " (reopening)" if a.get("reopening") else ""
                cal.events.append(CalEvent(
                    date=a["day"], source="treasury", tier="normal", family=None,
                    name=f"{a['term']} {a['kind']} auction{amt}{reopen}", time_et=a.get("close_et"),
                ))
    if dirty:
        _save(cache)
    mark_awaiting(cal.events, today, now_et or datetime.now(_ET).strftime("%H:%M"))
    cal.events.sort(key=lambda e: (e.date, e.time_et or "99:99", e.name))
    return cal


_ET = ZoneInfo("America/New_York")
_GRACE_MIN = 10


def mark_awaiting(events: list[CalEvent], today: date, now_et: str) -> None:
    """Flag today's data releases that are past their time with no actual (pure; `now_et` HH:MM).

    A data release carries a previous or consensus value; speeches, auctions and FOMC rows never
    get an 'actual', so they are never flagged."""
    h, m = (int(x) for x in now_et.split(":"))
    now_min = h * 60 + m
    for e in events:
        if (e.date != today.isoformat() or e.source != "nasdaq" or e.actual or not e.time_et
                or not (e.previous or e.consensus)):
            continue
        try:
            eh, em = (int(x) for x in e.time_et.split(":"))
        except ValueError:
            continue
        e.awaiting_actual = now_min >= eh * 60 + em + _GRACE_MIN


# ————— merge helpers for gates + pulse —————

_FAMILY_WORDS = {
    "CPI": ("cpi",), "PPI": ("ppi",), "Payrolls": ("payroll", "nfp", "jobs report"),
    "Unemployment": ("unemployment", "jobs report"), "Wages": ("hourly earnings", "jobs report"),
    "PCE": ("pce",), "GDP": ("gdp",), "Retail sales": ("retail sales",), "ISM": ("ism",),
    "JOLTS": ("jolts",), "Jobless claims": ("jobless", "claims"), "FOMC": ("fomc", "fed decision"),
    "FOMC minutes": ("minutes",), "Fed Chair": ("powell", "fed chair", "warsh"),
    "UMich sentiment": ("sentiment", "michigan"), "Consumer confidence": ("consumer confidence",),
    "EIA crude": ("eia", "crude inventor"), "Durable goods": ("durable",),
    # specific on purpose: S&P's final manufacturing PMI prints the same morning as ISM's, so a
    # ledger "ISM PMI" row must not hide the S&P rows
    "S&P PMI": ("s&p global", "s&p pmi", "flash pmi"),
    "Fed speaker": ("speaks", "speech", "remarks", "testimony"),
}


def dedup_key(ev: CalEvent) -> tuple[str, str | None]:
    """One row per release family per day (CPI m/m + y/y = one bar) — but every Fed speaker is
    their own event (Williams and Schmid on the same morning are two reads, not one)."""
    return (ev.date, ev.name if ev.family == "Fed speaker" else ev.family)


def covered_by_ledger(ev: CalEvent, ledger: list[tuple[str, str]]) -> bool:
    """True when a hand-kept ledger row (date, label) on the same date already names this release."""
    words = _FAMILY_WORDS.get(ev.family or "", ())
    return any(d == ev.date and any(w in label.lower() for w in words) for d, label in ledger)


def todays_prints(cal: Calendar, today: date) -> list[CalEvent]:
    """High-tier releases today whose number is out — pulse's macro_print payload."""
    return [e for e in cal.events if e.date == today.isoformat() and e.tier == "high" and e.actual]
