"""A visit's schedule as one day-by-day contract — the Visits tab's interactive timeline.

A visit folder's `schedule.json` (agent-authored, also read by the static schedule /
schedule-bank / weather-strip renders) carries the window (`start`/`end`, `dayStart`/`dayEnd`/
`eveningStart`), the planned `items[]`, pinned `markers[]` (games, shows, holidays), and an
`availability` block saying who is around on which dates. This module merges those per date with
the live forecast and the visit's bank (`travel.bank`), so the console draws one column per day
without re-deriving anything:

- availability resolves per date: an explicit `dates[date]` entry wins, else `weekend` /
  `weekday` defaults.
- weather is the home locale's daily forecast, clamped to the forecast horizon; a day is flagged
  `wet` on the same thresholds the conditions watch uses (duration or accumulation, never the
  probability), so the two surfaces agree on what a rain day is.
- each planned item links to a bank entry when it names one (an explicit `bank: <id>` key, else a
  word-overlap match on the labels), so clicking it opens the same detail card as the bank tile.

Read-only: nothing here edits the schedule; moving an item is an edit to the JSON.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from harness.travel.bank import BankItem, VisitBank
from harness.travel.models import DONE_STATUSES, DailyWeather, Trip, is_live
from harness.travel.ranking.weights import ConditionsThresholds

# Words that carry no identity when matching an item label to a bank label.
_STOP = {"a", "an", "and", "at", "by", "for", "in", "near", "of", "on", "the", "to", "with", "w"}
LINK_MIN_OVERLAP = 0.6  # share of the smaller label's words that must match


class Marker(BaseModel):
    date: str
    time: str | None
    label: str
    cat: str | None


class MarkerCat(BaseModel):
    key: str
    label: str
    color: str | None


class AvailabilityGroup(BaseModel):
    key: str
    label: str
    color: str | None


class ScheduleItem(BaseModel):
    date: str
    start: str | None  # "HH:MM"
    end: str | None
    label: str
    lane: str | None
    note: str | None
    penciled: bool  # the label carries the ✎ "penciled suggestion" mark
    bank_id: str | None  # the linked bank entry, when the item names one
    bank_match: Literal["explicit", "label"] | None


class DayWeather(BaseModel):
    condition: str
    high: float | None
    low: float | None
    precip_prob: int | None
    precip_sum: float | None
    precip_hours: float | None
    wet: bool  # crosses the wet-day thresholds (duration or accumulation)


class ScheduleDay(BaseModel):
    date: str
    weekday: str  # "Sat"
    availability: list[str]  # group keys present that day
    items: list[ScheduleItem]
    markers: list[Marker]
    weather: DayWeather | None  # None past the forecast horizon or when the forecast failed


class VisitSchedule(BaseModel):
    """`hn travel schedule --json`: the resolved visit, a column per date, and its bank."""

    visit: str  # "" when no live visit carries a schedule
    title: str
    subtitle: str
    doc: str
    start: date | None
    end: date | None
    day_start: str
    day_end: str
    evening_start: str | None
    availability_groups: list[AvailabilityGroup]
    marker_label: str
    marker_cats: list[MarkerCat]
    days: list[ScheduleDay]
    weather_place: str
    weather_note: str  # why some days have no weather (horizon, failure) — "" when all do
    precip_unit: str
    temp_unit: str
    bank: VisitBank


def _s(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _hhmm(v: Any) -> str | None:
    s = _s(v)
    return s if s and re.fullmatch(r"\d{1,2}:\d{2}", s.strip()) else None


def words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", label.lower()) if w not in _STOP}


def link_bank(label: str, explicit: str | None, bank: list[BankItem]) -> tuple[str | None, str | None]:
    """(bank_id, how) for a planned item. An explicit id that exists wins; otherwise the bank
    entry whose words overlap the item's most, if the overlap clears LINK_MIN_OVERLAP of the
    smaller label (ties → the first in bank order). A one-word label never auto-links."""
    ids = {b.id for b in bank}
    if explicit and explicit in ids:
        return explicit, "explicit"
    mine = words(label)
    best: tuple[float, str | None] = (0.0, None)
    for b in bank:
        theirs = words(b.label)
        small = min(len(mine), len(theirs))
        if small < 2:
            continue
        score = len(mine & theirs) / small
        if score > best[0]:
            best = (score, b.id)
    if best[1] and best[0] >= LINK_MIN_OVERLAP:
        return best[1], "label"
    return None, None


def resolve_availability(av: Any, d: date) -> list[str]:
    if not isinstance(av, dict):
        return []
    raw_dates = av.get("dates")
    dates: dict[str, Any] = raw_dates if isinstance(raw_dates, dict) else {}
    entry = dates.get(d.isoformat())
    if entry is None:
        entry = av.get("weekend" if d.weekday() >= 5 else "weekday")
    if not isinstance(entry, list):
        return []
    return [str(e["group"]) for e in entry if isinstance(e, dict) and e.get("group")]


def day_weather(d: DailyWeather, th: ConditionsThresholds) -> DayWeather:
    wet = (d.precip_hours is not None and d.precip_hours >= th.wet_day_hours) or (
        d.precip_sum is not None and d.precip_sum >= th.wet_day_sum_in
    )
    return DayWeather(
        condition=d.condition, high=d.temp_max, low=d.temp_min, precip_prob=d.precip_prob,
        precip_sum=d.precip_sum, precip_hours=d.precip_hours, wet=wet,
    )


def read_schedule(folder: Path) -> dict[str, Any] | None:
    p = folder / "schedule.json"
    try:
        data = json.loads(p.read_text()) if p.is_file() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def has_schedule(folder: Path) -> bool:
    s = read_schedule(folder)
    return bool(s and _s(s.get("start")) and _s(s.get("end")))


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def build_schedule(
    trip: Trip,
    vault_root: Path,
    bank: VisitBank,
    *,
    forecast: dict[str, DailyWeather] | None = None,
    weather_place: str = "",
    weather_note: str = "",
    thresholds: ConditionsThresholds | None = None,
    precip_unit: str = "inch",
    temp_unit: str = "°F",
) -> VisitSchedule:
    """Merge one visit's schedule.json with a forecast (date → DailyWeather) and its bank."""
    folder = (vault_root / trip.doc).parent
    raw = read_schedule(folder) or {}
    th = thresholds or ConditionsThresholds()
    start = _date(raw.get("start")) or trip.start
    end = _date(raw.get("end")) or trip.end or start
    av = raw.get("availability")
    groups = [
        AvailabilityGroup(
            key=str(g["key"]), label=_s(g.get("label")) or str(g["key"]), color=_s(g.get("color"))
        )
        for g in (av.get("groups") if isinstance(av, dict) else None) or []
        if isinstance(g, dict) and g.get("key")
    ]
    mc = raw.get("markerCats")
    cats_raw: dict[str, Any] = mc if isinstance(mc, dict) else {}
    cats = [
        MarkerCat(key=str(k), label=_s(v.get("label")) or str(k), color=_s(v.get("color")))
        for k, v in cats_raw.items() if isinstance(v, dict)
    ]
    outings = [b for b in bank.items if b.kind == "outing"]
    items_by_day: dict[str, list[ScheduleItem]] = {}
    for it in raw.get("items") or []:
        if not isinstance(it, dict) or not _s(it.get("date")) or not _s(it.get("label")):
            continue
        label = str(it["label"])
        bank_id, how = link_bank(label, _s(it.get("bank")), bank.items if _s(it.get("bank")) else outings)
        items_by_day.setdefault(str(it["date"]), []).append(ScheduleItem(
            date=str(it["date"]), start=_hhmm(it.get("start")), end=_hhmm(it.get("end")), label=label,
            lane=_s(it.get("lane")), note=_s(it.get("note")), penciled="✎" in label,
            bank_id=bank_id, bank_match=how,  # type: ignore[arg-type]
        ))
    markers_by_day: dict[str, list[Marker]] = {}
    for m in raw.get("markers") or []:
        if isinstance(m, dict) and _s(m.get("date")) and _s(m.get("label")):
            markers_by_day.setdefault(str(m["date"]), []).append(Marker(
                date=str(m["date"]), time=_s(m.get("time")), label=str(m["label"]), cat=_s(m.get("cat")),
            ))
    days: list[ScheduleDay] = []
    if start and end:
        d = start
        while d <= end:
            key = d.isoformat()
            wx = (forecast or {}).get(key)
            days.append(ScheduleDay(
                date=key, weekday=d.strftime("%a"), availability=resolve_availability(av, d),
                items=sorted(items_by_day.get(key, []), key=lambda i: i.start or "99:99"),
                markers=markers_by_day.get(key, []),
                weather=day_weather(wx, th) if wx else None,
            ))
            d += timedelta(days=1)
    return VisitSchedule(
        visit=trip.slug, title=_s(raw.get("title")) or trip.title or trip.slug,
        subtitle=_s(raw.get("subtitle")) or "", doc=trip.doc, start=start, end=end,
        day_start=_hhmm(raw.get("dayStart")) or "08:00", day_end=_hhmm(raw.get("dayEnd")) or "22:00",
        evening_start=_hhmm(raw.get("eveningStart")), availability_groups=groups,
        marker_label=_s(raw.get("markerLabel")) or "markers", marker_cats=cats, days=days,
        weather_place=weather_place, weather_note=weather_note, precip_unit=precip_unit,
        temp_unit=temp_unit, bank=bank,
    )


def empty_schedule(bank: VisitBank) -> VisitSchedule:
    return VisitSchedule(
        visit="", title="No live visit carries a schedule yet", subtitle="", doc="", start=None,
        end=None, day_start="08:00", day_end="22:00", evening_start=None, availability_groups=[],
        marker_label="", marker_cats=[], days=[], weather_place="", weather_note="",
        precip_unit="inch", temp_unit="°F", bank=bank,
    )


def resolve_visit(
    trips: list[Trip], vault_root: Path, slug: str | None = None, scope: str = "visit"
) -> Trip | None:
    """`slug` picks that trip or visit; otherwise the soonest live folder-note one of `scope`
    ("visit" | "trip") with a schedule."""
    if slug:
        return next((t for t in trips if t.slug == slug), None)
    visits = [t for t in trips if t.kind == scope]
    live = sorted((t for t in visits if is_live(t.status)), key=lambda t: t.sort_date)
    past = sorted((t for t in visits if t.status in DONE_STATUSES), key=lambda t: t.sort_date, reverse=True)
    for t in live + past:  # nothing upcoming has a schedule → the most recent past one
        doc = vault_root / t.doc
        if doc.parent.name == t.slug and has_schedule(doc.parent):
            return t
    return None
