"""Macro release calendar sources — three primary, keyless feeds (the `calendar` verb's I/O).

- **Nasdaq economic events** (`api.nasdaq.com/api/calendar/economicevents?date=`) — every US
  release for a day with `actual` / `consensus` / `previous` once printed; published about two
  weeks ahead. The same host (and the same endpoint-required browser UA) as the earnings-date
  provider. The `gmt` field is Eastern time in practice (MBA 07:00, EIA crude 10:30).
- **The Fed's FOMC calendar page** (`federalreserve.gov/monetarypolicy/fomccalendars.htm`) — every
  meeting of the year (month + day range, `*` = a Summary of Economic Projections meeting). Covers
  what Nasdaq's two-week window cannot.
- **TreasuryDirect upcoming auctions** (`treasurydirect.gov/TA_WS/securities/upcoming`) — the
  announced coupon auctions (Notes / Bonds / TIPS) with the offering amount and closing time.

Every fetch is per-source resilient: the calendar verb turns a dead source into a note.
BLS (CPI, payrolls) is NOT a source: its site refuses tool User-Agents (an Akamai wall); Nasdaq
carries those releases with the numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.providers.nasdaq_provider import _HDRS as _NASDAQ_HDRS

_NASDAQ_ECON = "https://api.nasdaq.com/api/calendar/economicevents"
_FED_CAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_TD_UPCOMING = "https://www.treasurydirect.gov/TA_WS/securities/upcoming"

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], start=1)}


@dataclass
class EconRow:
    day: str  # ISO date
    time_et: str | None
    name: str
    actual: str | None
    consensus: str | None
    previous: str | None


@dataclass
class FomcMeeting:
    decision_date: str  # ISO — the statement day (the meeting's last day)
    first_day: str
    sep: bool  # a Summary of Economic Projections meeting (the dot plot)


@dataclass
class Auction:
    day: str
    term: str  # "5-Year"
    kind: str  # Note | Bond | TIPS
    amount_bn: float | None
    close_et: str | None
    reopening: bool


def _clean(v: object) -> str | None:
    s = re.sub(r"&nbsp;|\s+", " ", str(v or "")).strip()
    return s or None


def fetch_econ_day(day: date, client: httpx.Client) -> list[EconRow]:
    """One day's US economic releases from Nasdaq (actual/consensus/previous when present).

    ⚠ The endpoint's `date=D` returns the releases of D−1 (verified live: `date=…-22` served a
    Monday's Chicago Fed index + bill auctions, `date=…-23` the Tuesday's Redbook + Richmond Fed
    with actuals). So day X is requested as X+1 and its rows are labeled X — without this every
    release lands a day late (EIA crude on a Thursday, durable goods on a Saturday)."""
    try:
        resp = get_with_retry(_NASDAQ_ECON, client=client, headers=_NASDAQ_HDRS,
                              params={"date": (day + timedelta(days=1)).isoformat()}, retries=2)
        rows = (resp.json().get("data") or {}).get("rows") or []
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"nasdaq economic calendar {day}: {e}") from e
    out = []
    for r in rows:
        if r.get("country") != "United States" or not _clean(r.get("eventName")):
            continue
        out.append(EconRow(
            day=day.isoformat(), time_et=_clean(r.get("gmt")), name=str(_clean(r.get("eventName"))),
            actual=_clean(r.get("actual")), consensus=_clean(r.get("consensus")),
            previous=_clean(r.get("previous")),
        ))
    return out


def parse_fomc_page(html: str) -> list[FomcMeeting]:
    """Pure: every meeting on the Fed calendar page (all year sections)."""
    out: list[FomcMeeting] = []
    years = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", html)]
    for i, (start, year) in enumerate(years):
        end = years[i + 1][0] if i + 1 < len(years) else len(html)
        seg = html[start:end]
        months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>', seg)
        dates = re.findall(r'fomc-meeting__date[^>]*>([^<]+)</div>', seg)
        for mon, rng in zip(months, dates, strict=False):
            sep = "*" in rng
            rng = rng.replace("*", "").strip()
            mnames = [m.strip() for m in mon.split("/")]
            try:
                d1s, d2s = (rng.split("-") + [rng])[:2]
                m1 = _MONTHS[mnames[0]]
                m2 = _MONTHS[mnames[-1]]
                first = date(year, m1, int(d1s))
                last = date(year, m2, int(d2s))
            except (KeyError, ValueError):
                continue  # "(notation vote)"-style rows and unparseable entries are skipped
            out.append(FomcMeeting(last.isoformat(), first.isoformat(), sep))
    return out


def fetch_fomc_meetings(client: httpx.Client) -> list[FomcMeeting]:
    try:
        return parse_fomc_page(get_with_retry(_FED_CAL, client=client, retries=2).text)
    except httpx.HTTPError as e:
        raise ProviderError(f"fed FOMC calendar: {e}") from e


def _close_et(v: object) -> str | None:
    """'01:00 PM' → '13:00' — the same 24-hour ET form the Nasdaq rows carry, so a day sorts right."""
    s = _clean(v)
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AaPp])[Mm]", s or "")
    if not m:
        return s
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return f"{hour:02d}:{m.group(2)}"


def fetch_auctions(client: httpx.Client) -> list[Auction]:
    """Announced coupon auctions (Notes/Bonds/TIPS); bills + floating-rate notes skipped."""
    try:
        rows = get_with_retry(_TD_UPCOMING, client=client, params={"format": "json"}, retries=2).json()
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"treasurydirect auctions: {e}") from e
    out = []
    for r in rows or []:
        kind = str(r.get("securityType") or "")
        if kind not in ("Note", "Bond", "TIPS"):
            continue
        if str(r.get("floatingRate") or "").lower() == "yes":
            continue
        tips_keys = ("tips", "type", "inflationIndexSecurity")
        if any(str(r.get(k) or "").lower() in ("yes", "tips") for k in tips_keys):
            kind = "TIPS"
        amt = r.get("offeringAmount")
        try:
            amount = round(float(amt) / 1e9, 1) if amt not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        out.append(Auction(
            day=str(r.get("auctionDate") or "")[:10], term=str(r.get("securityTerm") or ""),
            kind=kind, amount_bn=amount, close_et=_close_et(r.get("closingTimeCompetitive")),
            reopening=str(r.get("reopening") or "").lower() == "yes",
        ))
    return [a for a in out if a.day]
