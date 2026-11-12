"""Today's release numbers from the agencies that publish them, and the market-holiday rows.

The calendar's rows (name, time, consensus, previous) come from the schedule source. Once a release's
time has passed, the figure is read from its agency (providers/agency_provider.py) and written onto
the matching row with the agency named beside it; a figure the agency has published that the
schedule has no row for is added as a row of its own, and one the agency did not publish reads
"not published". Until the agency has the new period, the schedule source's own actual stands.

How a figure finds its row: by the row's name, and where two rows share a name (the schedule calls
both the monthly and the 12-month change "PPI") by the row's `previous`, which the agency's prior
period reproduces. A row that cannot be told apart is left alone.

What is fetched, and how often: only sources with a row due today, at most once every five minutes
while the new period is awaited, never again once it has posted. BLS allows 25 queries a day, so its
series go in one query and the day's count is kept in the cache and capped. A source that errors is
left alone for half an hour.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from harness.errors import ProviderError
from harness.finance.providers import agency_provider as ap

if TYPE_CHECKING:
    from harness.finance.calendar import CalEvent

NOT_PUBLISHED = "not published"
_TTL_AWAIT, _TTL_ERROR, TTL_HOLIDAYS = 300, 1800, 7 * 86400
_PACE = 1.0  # seconds between agency requests in one pass
_BLS_DAILY_CAP = 20  # of the 25 the API allows; the rest is headroom for a by-hand check
_NEAR, _CLEAR = 0.35, 0.2  # a row's previous must be this near a figure's, and this much nearer than the next


@dataclass
class Reading:
    name: str  # the row's name when the figure becomes a row of its own ("CPI y/y")
    pattern: str  # matched against a schedule row's lower-cased name
    kind: str  # mm | yy | other
    actual: str | None  # None: the agency published no figure
    previous: float | None = None
    headline: bool = False  # becomes its own row when no schedule row takes it


# ————— BLS: levels to the changes the calendar shows —————

_BLS_SERIES: dict[str, dict[str, str]] = {
    "CPI": {
        "sa": "CUSR0000SA0",
        "core_sa": "CUSR0000SA0L1E",
        "nsa": "CUUR0000SA0",
        "core_nsa": "CUUR0000SA0L1E",
    },
    "PPI": {"sa": "WPSFD4", "core_sa": "WPSFD49104", "nsa": "WPUFD4", "core_nsa": "WPUFD49104"},
    "Payrolls": {"jobs": "CES0000000001"},
    "Unemployment": {"rate": "LNS14000000"},
    "Wages": {"ahe": "CES0500000003"},
}
BLS_FAMILIES = frozenset(_BLS_SERIES)


def _at(points: list[ap.Point], year: int, month: int, back: int = 0) -> float | None:
    """The value `back` months before (year, month); None when absent or not published."""
    idx = year * 12 + (month - 1) - back
    return next((p.value for p in points if p.year * 12 + p.month - 1 == idx), None)


def _pct(points: list[ap.Point], year: int, month: int, span: int, back: int = 0) -> float | None:
    now, then = _at(points, year, month, back), _at(points, year, month, back + span)
    return None if now is None or not then else (now / then - 1) * 100


def _fmt_pct(v: float | None, places: int = 1) -> str | None:
    return None if v is None else f"{v:.{places}f}%"


def _has(points: list[ap.Point], year: int, month: int) -> bool:
    return any(p.year == year and p.month == month for p in points)


def bls_readings(family: str, data: dict[str, list[ap.Point]], year: int, month: int) -> list[Reading] | None:
    """Pure: the family's figures for the reference month, or None when the agency has no row for
    that month yet (the release has not posted)."""
    ids = _BLS_SERIES[family]
    series = {role: data.get(sid) or [] for role, sid in ids.items()}
    if not all(_has(points, year, month) for points in series.values()):
        return None

    def change(role: str, span: int, name: str, pattern: str, kind: str, headline: bool = True) -> Reading:
        return Reading(
            name,
            pattern,
            kind,
            _fmt_pct(_pct(series[role], year, month, span)),
            _pct(series[role], year, month, span, back=1),
            headline,
        )

    if family in ("CPI", "PPI"):
        low = family.lower()
        out = [
            change("sa", 1, f"{family} m/m", rf"^{low}$", "mm"),
            change("core_sa", 1, f"Core {family} m/m", rf"^core {low}$", "mm"),
            change("nsa", 12, f"{family} y/y", rf"^{low}$", "yy"),
            change("core_nsa", 12, f"Core {family} y/y", rf"^core {low}$", "yy"),
        ]
        if family == "CPI":

            def level(role: str, pattern: str) -> Reading:
                v = _at(series[role], year, month)
                return Reading(
                    "",
                    pattern,
                    "other",
                    None if v is None else f"{v:.2f}",
                    _at(series[role], year, month, back=1),
                )

            nsa_mm = _pct(series["nsa"], year, month, 1)
            out += [
                level("sa", r"^cpi index, s\.a"),
                level("nsa", r"^cpi index, n\.s\.a"),
                level("core_sa", r"^core cpi index"),
                Reading(
                    "",
                    r"^cpi, n\.s\.a",
                    "mm",
                    _fmt_pct(nsa_mm, 2),
                    _pct(series["nsa"], year, month, 1, back=1),
                ),
            ]
        return out
    if family == "Payrolls":
        jobs = series["jobs"]
        now, prior, before = (_at(jobs, year, month, b) for b in (0, 1, 2))
        added = None if now is None or prior is None else f"{now - prior:.0f}K"
        return [
            Reading(
                "Nonfarm payrolls",
                r"^nonfarm payrolls$",
                "other",
                added,
                None if prior is None or before is None else prior - before,
                True,
            )
        ]
    if family == "Unemployment":
        rate = _at(series["rate"], year, month)
        return [
            Reading(
                "Unemployment rate",
                r"^unemployment rate$",
                "other",
                _fmt_pct(rate),
                _at(series["rate"], year, month, back=1),
                True,
            )
        ]
    return [
        change("ahe", 1, "Average hourly earnings m/m", r"^average hourly earnings", "mm"),
        change("ahe", 12, "Average hourly earnings y/y", r"^average hourly earnings", "yy"),
    ]


def gdp_readings(r: ap.GdpRelease) -> list[Reading]:
    return [
        Reading(f"GDP {r.period}", r"^gdp$|^gdp growth rate", "other", _fmt_pct(r.rate), r.previous, True)
    ]


def pce_readings(r: ap.PceRelease) -> list[Reading]:
    return [
        Reading("PCE price index m/m", r"^pce price index$", "mm", _fmt_pct(r.mm), r.prev_mm, True),
        Reading(
            "Core PCE price index m/m",
            r"^core pce price index$",
            "mm",
            _fmt_pct(r.core_mm),
            r.prev_core_mm,
            True,
        ),
        Reading("PCE price index y/y", r"^pce price index$", "yy", _fmt_pct(r.yy), None, True),
        Reading("Core PCE price index y/y", r"^core pce price index$", "yy", _fmt_pct(r.core_yy), None, True),
    ]


def retail_readings(r: ap.RetailRelease) -> list[Reading]:
    return [
        Reading("Retail sales m/m", r"^retail sales$", "mm", _fmt_pct(r.mm), r.prev_mm, True),
        Reading("Retail sales y/y", r"^retail sales$", "yy", _fmt_pct(r.yy), None, True),
    ]


# ————— a figure finds its row —————


def _number(s: str | None) -> float | None:
    m = re.search(r"-?[\d.]+", (s or "").replace(",", ""))
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


def _pick(row: CalEvent, readings: list[Reading]) -> Reading | None:
    """The one reading this row shows, or None when it cannot be told."""
    low = row.name.lower()
    cands = [r for r in readings if re.search(r.pattern, low)]
    if re.search(r"yoy|y/y", low):
        cands = [r for r in cands if r.kind == "yy"]
    elif re.search(r"mom|m/m", low):
        cands = [r for r in cands if r.kind == "mm"]
    if len(cands) <= 1:
        return cands[0] if cands else None
    prev = _number(row.previous)
    if prev is None:
        return None
    known = sorted((abs(r.previous - prev), i) for i, r in enumerate(cands) if r.previous is not None)
    if known and known[0][0] <= _NEAR and (len(known) == 1 or known[1][0] - known[0][0] > _CLEAR):
        return cands[known[0][1]]
    # the row is none of the figures whose previous is known; if exactly one figure's is not, it is that one
    unknown = [r for r in cands if r.previous is None]
    if len(unknown) == 1 and all(d > _NEAR for d, _ in known):
        return unknown[0]
    return None


def _fmt_previous(r: Reading) -> str | None:
    """An added row's previous, in the unit its actual is shown in ("3.7%", "60K")."""
    if r.previous is None:
        return None
    if r.kind in ("mm", "yy") or (r.actual or "").endswith("%"):
        return f"{r.previous:.1f}%"
    return f"{r.previous:.0f}K" if (r.actual or "").endswith("K") else f"{r.previous:g}"


def fill(events: list[CalEvent], family: str, day: str, readings: list[Reading], source: str) -> None:
    """Write each figure onto its row for (family, day); add a row for a headline figure no row took."""
    from harness.finance.calendar import CalEvent

    rows = [e for e in events if e.date == day and e.family == family and e.source != source]
    left = list(readings)
    for row in rows:
        got = _pick(row, left)
        if got is None:
            continue
        left.remove(got)
        row.actual, row.actual_source = got.actual or NOT_PUBLISHED, source
    when = next((e.time_et for e in rows if e.time_et), None)
    for r in left:
        if r.headline:
            events.append(
                CalEvent(
                    date=day,
                    name=r.name,
                    source=source,
                    tier="high",
                    family=family,
                    time_et=when,
                    actual=r.actual or NOT_PUBLISHED,
                    actual_source=source,
                    previous=_fmt_previous(r),
                )
            )


# ————— fetch what is due —————

_FAMILY_SOURCE = {**dict.fromkeys(BLS_FAMILIES, "bls"), "GDP": "bea", "PCE": "bea", "Retail sales": "census"}
FAMILIES = frozenset(_FAMILY_SOURCE)


def _due(events: list[CalEvent], today: date, now_et: str) -> list[str]:
    """Families with a schedule row today whose time has passed, in first-seen order."""
    out: list[str] = []
    for e in events:
        if (
            e.date == today.isoformat()
            and e.family in FAMILIES
            and e.family not in out
            and e.time_et
            and e.time_et <= now_et
        ):
            out.append(e.family)
    return out


def _fetch_one(client: httpx.Client, family: str, today: date) -> list[Reading] | None:
    """The family's figures if its agency's current release is today's, else None."""
    if family == "GDP":
        gdp = ap.fetch_gdp(client)
        return gdp_readings(gdp) if gdp.released == today else None
    if family == "PCE":
        released, path = ap.fetch_pce_released(client)
        return pce_readings(ap.fetch_pce(client, released, path)) if released == today else None
    retail = ap.fetch_retail(client)
    return retail_readings(retail) if retail.released == today else None


def apply_actuals(
    events: list[CalEvent],
    cache: dict[str, Any],
    *,
    today: date,
    now_et: str,
    now: float,
    client: httpx.Client,
    offline: bool,
    notes: list[str],
) -> bool:
    """Fill today's due rows from the agencies. Returns True when the cache changed."""
    day = today.isoformat()
    store: dict[str, Any] = cache.setdefault("actuals", {})
    dirty = False
    for stale in [k for k in store if not k.startswith(day)]:
        del store[stale]
        dirty = True
    due = _due(events, today, now_et)

    def wanted(family: str) -> bool:
        entry = store.get(f"{day}:{family}")
        if offline or (entry and entry.get("posted")):
            return False
        ttl = _TTL_ERROR if entry and entry.get("error") else _TTL_AWAIT
        return not entry or now - float(entry.get("fetched", 0)) >= ttl

    def record(family: str, got: Callable[[], list[Reading] | None]) -> None:
        nonlocal dirty
        entry: dict[str, Any] = {"fetched": now, "posted": False}
        try:
            readings = got()
            if readings is not None:
                entry.update(posted=True, readings=[asdict(r) for r in readings])
        except ProviderError as e:
            entry["error"] = str(e)
            notes.append(f"{family} actual: {e}")
        store[f"{day}:{family}"] = entry
        dirty = True

    bls = [f for f in due if f in BLS_FAMILIES and wanted(f)]
    if bls:
        budget = cache.get("bls_queries") or {}
        used = int(budget.get("n", 0)) if budget.get("day") == day else 0
        if used >= _BLS_DAILY_CAP:
            notes.append(
                f"BLS actuals: the day's query budget ({_BLS_DAILY_CAP}) is spent; the schedule's own stand"
            )
        else:
            cache["bls_queries"] = {"day": day, "n": used + 1}
            ref = today.replace(day=1) - timedelta(days=1)  # a monthly release reports the prior month
            series = [sid for f in bls for sid in _BLS_SERIES[f].values()]
            try:
                data: dict[str, list[ap.Point]] | ProviderError = ap.fetch_bls(
                    client, series, ref.year - 1, ref.year
                )
            except ProviderError as e:
                data = e
            for f in bls:

                def got(f: str = f) -> list[Reading] | None:
                    if isinstance(data, ProviderError):
                        raise data
                    return bls_readings(f, data, ref.year, ref.month)

                record(f, got)
    for f in due:
        if f not in BLS_FAMILIES and wanted(f):
            if dirty:
                time.sleep(_PACE)

            def one(f: str = f) -> list[Reading] | None:
                return _fetch_one(client, f, today)

            record(f, one)
    for f in due:
        entry = store.get(f"{day}:{f}") or {}
        if entry.get("posted"):
            fill(events, f, day, [Reading(**r) for r in entry["readings"]], _FAMILY_SOURCE[f])
    return dirty


# ————— market holidays —————


def fetch_holidays(client: httpx.Client, notes: list[str]) -> list[dict[str, Any]] | None:
    """Both schedules as plain rows; a schedule that fails is a note and its market is left out.
    None when neither answered."""
    days: list[ap.MarketDay] = []
    answered = 0
    for label, fetch in (("bond-market holidays", ap.fetch_sifma), ("equity holidays", ap.fetch_nyse)):
        try:
            if answered:
                time.sleep(_PACE)
            days += fetch(client)
            answered += 1
        except ProviderError as e:
            notes.append(f"{label}: {e}")
    return [asdict(d) for d in days] if answered else None


def holiday_events(rows: list[dict[str, Any]], start: date, end: date) -> list[CalEvent]:
    """Pure: one row per day a market is closed or closes early, naming only the markets whose
    schedule says so (a market with no entry that day is open on its usual hours)."""
    from harness.finance.calendar import CalEvent

    markets = {r["market"] for r in rows}
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if start.isoformat() <= r["day"] <= end.isoformat():
            by_day.setdefault(r["day"], {})[r["market"]] = r
    label = {"bonds": "bond market", "equities": "equities"}
    out = []
    for day, found in sorted(by_day.items()):
        holiday = next((r["name"] for r in found.values() if r["name"]), "")
        closed = [m for m in ("bonds", "equities") if found.get(m, {}).get("kind") == "closed"]
        early = [m for m in ("bonds", "equities") if found.get(m, {}).get("kind") == "early"]
        open_ = [m for m in ("bonds", "equities") if m in markets and m not in found]
        bits = []
        if len(closed) == 2:
            bits.append("Markets closed")
        elif closed:
            bits.append(f"{label[closed[0]].capitalize()} closed")
        bits += [
            f"{label[m]} close{'s' if m == 'bonds' else ''} early {found[m]['close_et']} ET" for m in early
        ]
        bits += [f"{label[m]} open" for m in open_]
        name = "; ".join(bits)
        name = name[0].upper() + name[1:] + (f" ({holiday})" if holiday else "")
        out.append(
            CalEvent(
                date=day,
                name=name,
                source="+".join(
                    {"bonds": "sifma", "equities": "nyse"}[m] for m in ("bonds", "equities") if m in found
                ),
                tier="high",
                family="Market holiday",
            )
        )
    return out
