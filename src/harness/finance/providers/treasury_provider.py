"""US Treasury daily par yield curve — the primary-source curve read (keyless, EOD).

Why a second rates source beside the Yahoo rail: Yahoo's crumb-free chart meta carries LIVE
levels for the 13-week bill and the 5/10/30-year (`^IRX`/`^FVX`/`^TNX`/`^TYX`) but has NO
reliable 2-year index — `2YY=F` resolves to whatever contract last printed (a stale expiry showed
3.96 while the cash 2-year sat at 4.35). The 2-year is the policy-expectations tenor — the one a
hawkish-Fed day moves first — so the curve read needs it from a source that publishes the number
itself. Treasury's Interest-Rate Statistics feed does: every business day's par yield curve, all
tenors, one XML call per month, no key, no crumb. It is EOD by construction (the day's curve posts
after the close), which is exactly the honest frame for LEVELS and SPREADS; intraday DIRECTION on
the 2-year rides the Yahoo rail's 2-year note futures (`ZT=F`, price — a falling price is a rising
yield).

Facts, never verdicts (the market/global doctrine): levels per tenor, one-day changes in basis
points, and the three spreads a curve regime is read from — 2s10s (the recession/steepening
tell), 3m10s (the near-inversion tell), 5s30s (the fiscal/term-premium tell). Interpreting the shape is
the agent's take.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from harness._http import get_with_retry
from harness.errors import ProviderError

_FEED_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}

# The feed's column name → our tenor key. Every column is parsed; the surfaced subset is `TENORS`.
_COLUMNS: dict[str, str] = {
    "BC_1MONTH": "1m", "BC_2MONTH": "2m", "BC_3MONTH": "3m", "BC_4MONTH": "4m", "BC_6MONTH": "6m",
    "BC_1YEAR": "1y", "BC_2YEAR": "2y", "BC_3YEAR": "3y", "BC_5YEAR": "5y", "BC_7YEAR": "7y",
    "BC_10YEAR": "10y", "BC_20YEAR": "20y", "BC_30YEAR": "30y",
}
# The tenors a regime read actually uses, in curve order (the table widget's row order).
TENORS: list[tuple[str, str]] = [
    ("3m", "3-month bill"),
    ("2y", "2-year"),
    ("5y", "5-year"),
    ("10y", "10-year"),
    ("30y", "30-year"),
]
# Spreads in bp: (name, long tenor, short tenor). Positive = normal (long above short).
SPREADS: list[tuple[str, str, str]] = [
    ("2s10s", "10y", "2y"),
    ("3m10s", "10y", "3m"),
    ("5s30s", "30y", "5y"),
]


@dataclass
class CurveDay:
    """One business day's par yield curve — tenor key → yield in percent."""

    day: date
    rates: dict[str, float]


class CurveRow(BaseModel):
    """A table-widget row: one surfaced tenor with its level and one-day change."""

    tenor: str
    label: str
    yield_pct: float
    chg_bp: float | None = None


class YieldCurve(BaseModel):
    """The curve read: levels + one-day changes + the three regime spreads, all from one EOD source.

    `as_of` is the curve's own business date (NOT the fetch time) — during a session the levels are
    the prior close by design; the `note` says so whenever the curve date is behind today."""

    as_of: str
    prior_date: str | None = None
    levels: dict[str, float] = Field(default_factory=dict)  # tenor → %
    changes_bp: dict[str, float] = Field(default_factory=dict)  # tenor → bp vs prior business day
    spreads_bp: dict[str, float] = Field(default_factory=dict)  # 2s10s / 3m10s / 5s30s, in bp
    spread_changes_bp: dict[str, float] = Field(default_factory=dict)
    rows: list[CurveRow] = Field(default_factory=list)  # the surfaced tenors, curve order
    source: str = "home.treasury.gov daily par yield curve"
    note: str = ""


def _month_param(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}"


def _parse_feed(xml_text: str) -> list[CurveDay]:
    """Parse one month's Atom feed into CurveDays (ascending by date). Missing/blank tenors are
    skipped per-day rather than failing the month — the feed occasionally omits a column."""
    root = ET.fromstring(xml_text)
    days: list[CurveDay] = []
    for entry in root.findall("a:entry", _NS):
        props = entry.find("a:content/m:properties", _NS)
        if props is None:
            continue
        raw_date = props.findtext("d:NEW_DATE", default="", namespaces=_NS)
        if not raw_date:
            continue
        try:
            day = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue
        rates: dict[str, float] = {}
        for col, key in _COLUMNS.items():
            txt = props.findtext(f"d:{col}", default="", namespaces=_NS)
            if not txt:
                continue
            try:
                rates[key] = float(txt)
            except ValueError:
                continue
        if rates:
            days.append(CurveDay(day=day, rates=rates))
    days.sort(key=lambda c: c.day)
    return days


def fetch_curve_days(today: date | None = None, *, months: int = 2) -> list[CurveDay]:
    """The last `months` months of daily curves (ascending). Two months by default so a
    first-business-day-of-the-month read still has a prior day to diff against."""
    today = today or date.today()
    out: list[CurveDay] = []
    month_first = today.replace(day=1)
    last_err: Exception | None = None
    for _ in range(months):
        try:
            resp = get_with_retry(_FEED_URL, params={
                "data": "daily_treasury_yield_curve",
                "field_tdr_date_value_month": _month_param(month_first),
            }, retries=2)
            out.extend(_parse_feed(resp.text))
        except Exception as err:  # noqa: BLE001 — a missing month must not brick the read
            last_err = err
        # step to the previous month (the day before the 1st is always in it)
        month_first = (month_first - timedelta(days=1)).replace(day=1)
    if not out:
        raise ProviderError(f"treasury curve: feed unavailable ({last_err})")
    dedup = {c.day: c for c in out}
    return sorted(dedup.values(), key=lambda c: c.day)


def build_curve(days: list[CurveDay], today: date | None = None) -> YieldCurve:
    """Pure: the latest day's levels + diffs vs the prior business day + the regime spreads."""
    if not days:
        raise ProviderError("treasury curve: no daily curves parsed")
    latest = days[-1]
    prior = days[-2] if len(days) >= 2 else None
    levels = {k: latest.rates[k] for k, _ in TENORS if k in latest.rates}
    changes: dict[str, float] = {}
    if prior is not None:
        for k in levels:
            if k in prior.rates:
                changes[k] = round((levels[k] - prior.rates[k]) * 100.0, 1)
    spreads: dict[str, float] = {}
    spread_chg: dict[str, float] = {}
    for name, long_t, short_t in SPREADS:
        if long_t in latest.rates and short_t in latest.rates:
            spreads[name] = round((latest.rates[long_t] - latest.rates[short_t]) * 100.0, 1)
            if prior is not None and long_t in prior.rates and short_t in prior.rates:
                prev = (prior.rates[long_t] - prior.rates[short_t]) * 100.0
                spread_chg[name] = round(spreads[name] - prev, 1)
    rows = [
        CurveRow(tenor=k, label=label, yield_pct=levels[k], chg_bp=changes.get(k))
        for k, label in TENORS
        if k in levels
    ]
    note = ""
    today = today or date.today()
    if latest.day < today:
        note = f"EOD curve as of {latest.day.isoformat()} (Treasury posts after the close)"
    return YieldCurve(
        as_of=latest.day.isoformat(),
        prior_date=prior.day.isoformat() if prior else None,
        levels=levels,
        changes_bp=changes,
        spreads_bp=spreads,
        spread_changes_bp=spread_chg,
        rows=rows,
        note=note,
    )


def fetch_yield_curve(today: date | None = None) -> YieldCurve:
    """The one-call read used by `hn finance global`: fetch → build."""
    return build_curve(fetch_curve_days(today), today)


def curve_scalars(curve: YieldCurve) -> dict[str, float]:
    """Tile-ready scalars (value_path `scalars.<key>`): the 2-year LEVEL + its day change in bp, the
    EOD 10-year for reference, and the three spreads in bp. Names carry `_eod` where the live Yahoo
    rail already owns the plain key (`us10y`) so a tile never silently swaps sources."""
    out: dict[str, float] = {}
    if "2y" in curve.levels:
        out["us2y"] = curve.levels["2y"]
    if "2y" in curve.changes_bp:
        out["us2y_chg_bp"] = curve.changes_bp["2y"]
    if "10y" in curve.levels:
        out["us10y_eod"] = curve.levels["10y"]
    for name in ("2s10s", "3m10s", "5s30s"):
        if name in curve.spreads_bp:
            out[f"spread_{name}_bp"] = curve.spreads_bp[name]
    return out
