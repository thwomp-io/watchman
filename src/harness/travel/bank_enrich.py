"""Enrich a visit's (or trip's) bank: hours, a reservation / order link, phone, coordinates and the
Google place id for every place the bank names — the detail the tiles and the schedule legs read.

Places come from the folder's `places.json` sidecar (written by `bank-photos`); their details come
from the provider's on-disk cache first (zero quota — the lookups that resolved the place already
returned hours, coordinates and booking links). Only a place with no cached record costs one search
(`--yes`), and a dry run prices that before anything is spent. Results are written back into the
same sidecar under each item id; `hn travel bank` merges them at read time, so the hand-authored
bank JSONs are never rewritten and an authored `hours` / link always wins.

The sanity check flags any place more than `MAX_MILES` from the bank's median point — a resolver
that sent "Old Town, Springfield" to Utah would show up here instead of on a map.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from harness.travel.bank import VisitBank
from harness.travel.bank_photos import place_query, read_sidecar, write_sidecar

MAX_MILES = 100.0
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class EnrichItem:
    id: str
    label: str
    source: str = ""  # sidecar+cache | search | missing | skipped
    fields: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class EnrichRun:
    visit: str
    searches_planned: int = 0
    searches_spent: int = 0
    items: list[EnrichItem] = field(default_factory=list)
    outliers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "visit": self.visit, "searches_planned": self.searches_planned,
            "searches_spent": self.searches_spent, "outliers": self.outliers,
            "items": [vars(i) for i in self.items],
        }


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s).replace("\u202f", " ")).strip()


RESERVE_HOSTS = ("opentable.", "resy.com", "exploretock.com", "sevenrooms.com", "google.com/maps/reserve")


def weekly_hours(raw: Any) -> dict[str, str] | None:
    """Google's hours → Monday-first {day: "5–9 PM"}. A list row carries `operating_hours` as a
    dict; a place page carries `hours` as a list of one-day dicts."""
    if isinstance(raw, list):
        merged: dict[str, Any] = {}
        for d in raw:
            if isinstance(d, dict):
                merged.update(d)
        raw = merged
    if not isinstance(raw, dict):
        return None
    out = {d: _clean(raw[d]) for d in DAYS if raw.get(d)}
    return out or None


def _url(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("link") or v.get("url")
    if isinstance(v, list):
        v = next((x.get("link") for x in v if isinstance(x, dict) and x.get("link")), None)
    return v if isinstance(v, str) and v.startswith("http") else None


def booking_link(rec: dict[str, Any]) -> str | None:
    """A table reservation, if Google lists one: its own reserve page, or a booking link on a
    reservation platform (OpenTable, Resy, Tock, SevenRooms). Google's reserve URL carries a long
    tracking `sourceurl`; it is cut (the booking still opens)."""
    for key in ("reserve_a_table", "reservation", "reservations", "booking_link"):
        v = _url(rec.get(key))
        if v and (key != "booking_link" or any(h in v for h in RESERVE_HOSTS)):
            return re.sub(r"[&?]sourceurl=.*$", "", v)
    return None


def order_link(rec: dict[str, Any]) -> str | None:
    """Online ordering: Google's order links, or a booking link that is really a pickup/delivery
    storefront (Square, Postmates, …) rather than a reservation."""
    for key in ("order_online", "order_online_link"):
        v = _url(rec.get(key))
        if v:
            return v
    v = _url(rec.get("booking_link"))
    return v if v and not any(h in v for h in RESERVE_HOSTS) else None


def details(rec: dict[str, Any]) -> dict[str, Any]:
    """The enrichment fields from one cached Google place record (absent fields omitted)."""
    gps = rec.get("gps_coordinates") or {}
    out: dict[str, Any] = {
        "lat": gps.get("latitude"), "lon": gps.get("longitude"),
        "place_id": rec.get("place_id"),
        "hours": weekly_hours(rec.get("operating_hours")) or weekly_hours(rec.get("hours")),
        "menu_url": _url(rec.get("menu")),
        "reservation_url": booking_link(rec), "order_url": order_link(rec),
        "phone": _clean(rec["phone"]) if rec.get("phone") else None,
        "website": rec.get("website") if str(rec.get("website") or "").startswith("http") else None,
        "address": rec.get("address"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", {})}


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def outliers(points: dict[str, tuple[float, float]], limit: float = MAX_MILES) -> list[str]:
    """Ids whose point sits more than `limit` miles from the median point of the set."""
    if len(points) < 3:
        return []
    lats = sorted(p[0] for p in points.values())
    lons = sorted(p[1] for p in points.values())
    center = (lats[len(lats) // 2], lons[len(lons) // 2])
    return [k for k, p in points.items() if miles(p, center) > limit]


def free_place(item: Any, vault_root: Path, known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A place for an item with no sidecar entry, at zero cost: the place its borrowed gallery came
    from (that trip's sidecar), else a cached search that named it exactly. {} when neither."""
    from harness.travel.providers.food_provider import normalize_name

    if item.gallery_from and "/" in item.gallery_from:
        slug, other_id = item.gallery_from.split("/", 1)
        for base in ("visits", "trips"):
            folder = vault_root / "travel" / base / slug
            if folder.is_dir():
                hit = read_sidecar(folder).get(other_id)
                if isinstance(hit, dict) and hit.get("data_id"):
                    return {k: hit[k] for k in ("data_id", "title", "address", "lat", "lon") if k in hit}
    hit = known.get(normalize_name(item.label))
    return dict(hit) if hit else {}


def enrich(
    bank: VisitBank,
    vault_root: Path,
    provider: Any,
    center: tuple[float, float],
    *,
    dry_run: bool = False,
    refresh: bool = False,
    cached_only: bool = False,
) -> EnrichRun:
    """Fill each bank item's sidecar entry with its place details. Cached records cost nothing; a
    place with no sidecar entry or no cached record costs one lookup (planned on a dry run)."""
    folder = vault_root / Path(bank.doc).parent
    sidecar = read_sidecar(folder)
    cache = provider.cached_details()
    known = provider.known_places()
    run = EnrichRun(visit=bank.visit)
    today = date.today().isoformat()
    for item in bank.items:
        res = EnrichItem(id=item.id, label=item.label)
        run.items.append(res)
        if not item.gallery:
            res.source, res.note = "skipped", "not a place"
            continue
        entry = dict(sidecar.get(item.id) or {})
        if not entry.get("data_id"):
            entry.update(free_place(item, vault_root, known))
        rec = cache.get(str(entry.get("data_id"))) if entry.get("data_id") and not refresh else None
        if rec is None and cached_only:
            res.source, res.note = "uncached", "needs a search (skipped: --cached-only)"
            continue
        if rec is None:
            run.searches_planned += 1
            if dry_run:
                res.source = "search"
                continue
            before = provider.search_count
            found = provider.lookup_place(place_query(item), center[0], center[1], refresh=refresh)
            run.searches_spent += provider.search_count - before
            if found is None:
                res.source, res.note = "missing", "no Maps match"
                continue
            rec = provider.cached_details().get(str(found["data_id"]), found)
            entry.update({k: v for k, v in found.items() if k not in entry or refresh})
            res.source = "search"
        else:
            res.source = "sidecar+cache"
        got = details(rec)
        if dry_run:
            res.fields = sorted(got)
            continue
        entry.update(got)
        entry["enriched"] = today
        sidecar[item.id] = entry
        res.fields = sorted(got)
    pts = {k: (float(v["lat"]), float(v["lon"])) for k, v in sidecar.items()
           if isinstance(v, dict) and v.get("lat") is not None and v.get("lon") is not None}
    run.outliers = outliers(pts)
    if not dry_run:
        write_sidecar(folder, sidecar)
    return run
