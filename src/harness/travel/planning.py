"""The planning views over the trip corpus: the open decision's radar, the pipeline of trips and
visits with their countdowns, the picker entries, the calendar band, and a trip's candidate map."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness.travel import bank as _bank
from harness.travel import schedule as _sched
from harness.travel.models import DONE_STATUSES, PARKED_STATUSES, Trip, is_live

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def hero_image(doc: Path) -> str | None:
    """The first image a trip/visit note embeds (`![[travel/…/x.jpg…]]`) — its curated hero, the
    picker's cover. None when the note embeds no image."""
    try:
        text = doc.read_text()
    except OSError:
        return None
    m = re.search(r"!\[\[([^\]|#]+\.(?:jpe?g|png|webp))", text, re.IGNORECASE)
    return m.group(1).strip() if m and ".." not in m.group(1) else None


def _short_date(d: date) -> str:
    """'Fri 1/8' — the compact window grammar of the Planning cards."""
    return f"{d.strftime('%a')} {d.month}/{d.day}"


def trip_name(t: Trip) -> str:
    """A trip's short name for a header: its title's lead clause without the leading emoji
    ("🏖 Beach weekend — Sat May 2 · …" → "Beach weekend"), else the anchor, else the slug."""
    lead = (t.title or "").split(" — ")[0].split(" · ")[0]
    start = next((i for i, ch in enumerate(lead) if ch.isalnum()), len(lead))
    return lead[start:].strip() or t.anchor or t.slug


def _rank_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    """A compare radar's candidates ranked by their axis total (ties keep the authored order), each
    with its strongest and weakest axis — the list beside the Planning radar, computed here so the
    console never re-derives it."""
    axes = [str(a) for a in data.get("axes") or []]
    rows: list[dict[str, Any]] = []
    for c in data.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        vals = [float(v) for v in c.get("values") or [] if isinstance(v, (int, float))]
        if not vals or len(vals) != len(axes):
            continue
        hi, lo = vals.index(max(vals)), vals.index(min(vals))
        total = sum(vals)
        rows.append({
            "label": str(c.get("label", "")),
            "total": int(total) if total.is_integer() else round(total, 1),
            "strongest": axes[hi],
            "weakest": axes[lo],
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def active_decision(svc: TravelService) -> dict[str, Any]:
    """The Planning radar's DYNAMIC source: resolve the soonest LIVE trip/visit
    whose folder carries a compare radar (`visuals/compare-data.json` beside the folder-note)
    and pass its JSON through, stamping the resolved slug in as `trip`.

    Kills the hardcoded-trip-path staleness class: the compiled default calls THIS verb, so the
    widget follows the corpus as trips open/close instead of freezing a slug into config
    (a config value that names a trip is stale by construction).
    Honest empty radar shape when no live trip has a compare file."""
    live = sorted(
        (t for t in svc.reader.scan_trips() if is_live(t.status)), key=lambda t: t.sort_date
    )
    vault_root = svc.reader._root.parent
    for t in live:
        doc = vault_root / t.doc
        # folder-note trips only — a flat trip's parent is trips/ itself, where a stray
        # visuals/ dir would false-hit for every flat trip at once
        if doc.parent.name != t.slug:
            continue
        data_path = doc.parent / "visuals" / "compare-data.json"
        if not data_path.is_file():
            continue
        data = json.loads(data_path.read_text())
        if isinstance(data, dict):
            data.setdefault("trip", t.slug)
            data["trip_name"] = trip_name(t)
            data["trip_kind"] = t.kind
            data["start"] = t.start.isoformat() if t.start else None
            data["end"] = t.end.isoformat() if t.end else None
            data["window"] = (
                f"{_short_date(t.start)} → {_short_date(t.end)}" if t.start and t.end
                else (_short_date(t.start) if t.start else t.window)
            )
            data["ranked"] = _rank_candidates(data)
            return data
    return {
        "title": "Active decision — no live trip carries a compare radar yet",
        "axes": [],
        "candidates": [],
        "max": 5,
        "trip": "",
        "ranked": [],
    }


def trip_banks(svc: TravelService, scope: str = "visit", today: date | None = None) -> _bank.TripBanks:
    """Every folder-note visit (or trip) as a picker entry: live soonest-first, then past
    most-recent; each with its dates, status, countdown, a cover photo and what it carries
    (bank counts, schedule days). Entries with nothing yet stay listed — the picker says so."""

    today = today or date.today()
    vault_root = svc.reader._root.parent
    trips = [t for t in svc.reader.scan_trips() if t.kind == scope]
    live = sorted((t for t in trips if is_live(t.status)), key=lambda t: t.sort_date)
    parked = sorted((t for t in trips if t.status in PARKED_STATUSES), key=lambda t: t.sort_date)
    past = sorted(
        (t for t in trips if t.status in DONE_STATUSES), key=lambda t: t.sort_date, reverse=True
    )
    entries: list[_bank.TripEntry] = []
    for t in live + parked + past:
        folder = (vault_root / t.doc).parent
        if folder.name != t.slug:
            continue  # flat docs carry no banks
        bank = _bank.build_bank(t, vault_root)
        sched = _sched.read_schedule(folder) or {}
        cover = _cover(svc, t, bank)
        row = _trip_row(t, today)
        entries.append(_bank.TripEntry(
            slug=t.slug, kind=t.kind, title=t.title or t.anchor or t.slug,
            status=t.status, when=row["when"], date=row["date"],
            start=t.start.isoformat() if t.start else None,
            end=t.end.isoformat() if t.end else None,
            destination=t.destination, doc=t.doc, cover=cover,
            outings=sum(1 for i in bank.items if i.kind == "outing"),
            restaurants=sum(1 for i in bank.items if i.kind == "restaurant"),
            photos=sum(len(i.images) for i in bank.items),
            scheduled=len([i for i in sched.get("items") or [] if isinstance(i, dict)]),
            live=is_live(t.status),
            phase="live" if is_live(t.status) else (
                "someday" if t.status in PARKED_STATUSES else "past"
            ),
        ))
    has = [e for e in entries if e.outings or e.restaurants or e.scheduled]
    # the same rule the bank/schedule verbs apply to "auto": soonest live with content, else
    # the most recent past one with content (entries are already in that order)
    return _bank.TripBanks(scope=scope, default=has[0].slug if has else None, entries=entries)


def _cover(svc: TravelService, t: Trip, bank: _bank.VisitBank | None = None) -> str | None:
    """The picker's cover rule: the note's first embedded image, else a bank item's own photo
    (a borrowed gallery last), else the folder's first asset."""
    vault_root = svc.reader._root.parent
    doc = vault_root / t.doc
    cover = hero_image(doc)
    if cover is not None or doc.parent.name != t.slug:
        return cover
    if bank is None:
        bank = _bank.build_bank(t, vault_root)
    cover = next((i.image for i in bank.items if i.image and not i.gallery_from), None)
    if cover is None:
        cover = next((i.image for i in bank.items if i.image), None)
    if cover is None:
        adir = doc.parent / "assets"
        assets = sorted(adir.glob("*.jpg")) if adir.is_dir() else []
        cover = f"{Path(t.doc).parent}/assets/{assets[0].name}" if assets else None
    return cover


def planning_calendar(svc: TravelService, today: date | None = None) -> _bank.PlanningCalendar:
    """The Planning tab's "on the calendar" band: one card per live trip or visit, soonest
    first, each with its cover, window, countdown and whether its folder carries an open
    decision. Undated live items and parked ideas go to `someday` — never counted down."""
    today = today or date.today()
    vault_root = svc.reader._root.parent
    trips = svc.reader.scan_trips()
    live = sorted((t for t in trips if is_live(t.status)), key=lambda t: t.sort_date)
    parked = sorted((t for t in trips if t.status in PARKED_STATUSES), key=lambda t: t.sort_date)

    def card(t: Trip, *, someday: bool) -> _bank.CalendarCard:
        doc = vault_root / t.doc
        folder_note = doc.parent.name == t.slug
        if t.start and t.end and t.end != t.start:
            window = f"{_short_date(t.start)} → {_short_date(t.end)}"
        elif t.start:
            window = _short_date(t.start)
        else:
            window = t.window or "undated"
        return _bank.CalendarCard(
            slug=t.slug, kind=t.kind, name=trip_name(t), title=t.title or t.anchor or t.slug,
            anchor=t.anchor, destination=t.destination, who=list(t.travelers), status=t.status,
            start=t.start.isoformat() if t.start else None,
            end=t.end.isoformat() if t.end else None,
            window=window,
            days=None if someday or t.start is None else (t.start - today).days,
            when="someday" if someday else _trip_row(t, today)["when"],
            cover=_cover(svc, t), doc=t.doc,
            decision=folder_note and (doc.parent / "visuals" / "compare-data.json").is_file(),
        )

    upcoming = [card(t, someday=False) for t in live if t.start is not None]
    someday = [card(t, someday=True) for t in live if t.start is None]
    someday += [card(t, someday=True) for t in parked]
    return _bank.PlanningCalendar(as_of=today.isoformat(), upcoming=upcoming, someday=someday)


def trip_pipeline(svc: TravelService, today: date, *, include_past: bool = False, phase: str = "live"
) -> list[dict[str, str]]:
    """The whole travel horizon as dashboard-ready rows: every live trip/visit (and past ones
    with `include_past`), countdown-enriched and ordered live-soonest-first then past-most-recent.
    `phase="someday"` returns the parked ideas instead (never part of the live horizon).
    Reads the `trip:` frontmatter via the corpus — deterministic, no tag-guessing."""
    trips = svc.reader.scan_trips()
    if phase == "someday":
        parked = sorted((t for t in trips if t.status in PARKED_STATUSES), key=lambda t: t.sort_date)
        return [_trip_row(t, today) for t in parked]
    live = sorted((t for t in trips if is_live(t.status)), key=lambda t: t.sort_date)
    past = sorted(
        (t for t in trips if t.status in DONE_STATUSES), key=lambda t: t.sort_date, reverse=True
    )
    ordered = live + (past if include_past else [])
    return [_trip_row(t, today) for t in ordered]


def _trip_row(t: Trip, today: date) -> dict[str, str]:
    if t.status in DONE_STATUSES:
        when = t.status
    elif t.status in PARKED_STATUSES:
        when = "someday"
    elif t.start is None:
        when = t.window or "planning"
    else:
        days = (t.start - today).days
        if days < 0:
            when = "now" if (t.end and t.end >= today) else "past"
        elif days == 0:
            when = "TODAY"
        elif days == 1:
            when = "tomorrow"
        elif days < 14:
            when = f"in {days}d"
        elif days < 70:
            when = f"in {days // 7}w"
        else:
            when = f"in {days // 30}mo"
    return {
        "when": when,
        "date": t.start.isoformat() if t.start else (t.window or "—"),
        "status": t.status,
        "kind": t.kind,
        "destination": t.destination or "—",
        "anchor": t.anchor or "—",
        "who": ", ".join(t.travelers) or "—",
        "doc": t.doc,  # tracker-relative vault path → the dashboard renders it as an "open ↗" link
    }


def map_data_for_trip(svc: TravelService, trip_slug: str,
    *,
    origin_label: str | None = None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> dict[str, Any]:
    """Build `map` viz data for a trip: geocode each candidate's event-city + arc from the
    origin. All origin details are config-driven (no hardcoded home location): the label is the
    configured hub / local airport pair, and the lat/lon geocode the configured home locale
    (`conditions.home`) when not passed explicitly. Reach label is curated — '{local} direct' if
    the gateway IATA is on the local airport's route map, else 'via {hub}' (NOT a routed/computed
    time). Candidates lacking a city mapping or geocode are skipped (honest partial)."""
    fw = svc.weights.flight
    home_iata, hub_iata = fw.home_airport.upper(), fw.comparison_airport.upper()
    if origin_label is None:
        origin_label = " / ".join(x for x in (hub_iata, home_iata) if x) or "home"
    if origin_lat is None or origin_lon is None:
        home_locale = svc.weights.conditions.home
        loc = svc.geocode_place(home_locale) if home_locale else None
        origin_lat = loc.latitude if loc else 0.0
        origin_lon = loc.longitude if loc else 0.0
    plan = svc.reader.build_trip_plan(trip_slug)
    points: list[dict[str, Any]] = []
    for c in plan.candidates:
        city = svc.weights.destination_cities.get(c.slug)
        if not city:
            continue
        loc = svc.geocode_place(city)
        if loc is None:
            continue
        iata = svc.weights.destination_airports.get(c.slug)
        served = home_iata and iata in fw.home_airport_served_iata
        reach = f"{home_iata} direct" if served else (f"via {hub_iata}" if hub_iata else "")
        points.append(
            {"label": c.display_name, "lat": loc.latitude, "lon": loc.longitude, "reach": reach}
        )
    return {
        "title": f"{trip_slug} — candidate map",
        "subtitle": f"great-circle from {origin_label} · reach is curated, not routed",
        "origin": {"label": origin_label, "lat": origin_lat, "lon": origin_lon},
        "points": points,
    }
