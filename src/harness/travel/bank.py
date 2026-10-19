"""A visit's idea bank as one tile contract — the Visits tab's browsable outings + restaurants.

A home-anchored visit's folder carries two agent-authored banks beside its folder-note:
`schedule.json` (`.bank[]`, the outings/things-to-do) and `restaurants.json` (`.restaurants[]` +
`.groups[]` for display order). Both also feed the static schedule-bank / food-bank renderers, so
this module only READS them and never rewrites their schema. It merges the two into one item list
with a shared shape (id, label, group, image, links …) so a console widget renders either kind with
one tile, and keeps every field it doesn't model in `extra` (unknown keys pass through, never drop).

Image paths are rewritten vault-relative (`travel/visits/<slug>/assets/x.jpg`) so the console's
guarded image read resolves them without knowing which visit they came from. The vault stays the
source of truth: nothing here edits a bank, and booking stays out — links only.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from harness.travel.models import DONE_STATUSES, Trip, is_live

BankKind = Literal["outing", "restaurant"]

# Per-item photo galleries live beside the banks: <visit>/assets/gallery/<item-id>/NN.jpg
GALLERY_DIR = "assets/gallery"
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def gallery_files(folder: Path, item_id: str) -> list[Path]:
    """An item's gallery photos, name-ordered (01.jpg, 02.jpg …). Curating = deleting a file."""
    d = folder / GALLERY_DIR / item_id
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXT)

# Fields the item models directly; every other key of a bank entry lands in `extra`.
_OUTING_KEYS = {
    "id", "label", "group", "lane", "area", "drive_min", "indoor", "why", "note", "image",
    "links", "season", "hours", "rating", "reviews", "price", "gallery", "gallery_from",
}
_RESTAURANT_KEYS = {
    "name", "group", "area", "cuisine", "meals", "price", "rating", "reviews", "yelp_rating",
    "note", "status", "confirm", "image", "links", "hours", "drive_min", "indoor", "id", "gallery",
    "gallery_from",
}


class BankItem(BaseModel):
    """One tile: an outing or a restaurant, normalized to a shared shape."""

    id: str
    kind: BankKind
    label: str
    group: str  # group key (outings: the lane, else the group label slugged)
    group_label: str
    lane: str | None
    area: str | None
    drive_min: int | None
    indoor: bool | None
    why: str | None
    note: str | None
    image: str | None  # vault-relative cover photo, or None (the tile shows a placeholder)
    images: list[str]  # cover first, then the item's gallery photos — what the tile pages through
    gallery: bool  # False marks a non-place (a cook-in, a TV game): no photo gallery is fetched
    gallery_from: str | None = None  # "<trip-slug>/<item-id>" whose gallery this item shows, when borrowed
    rating: float | None
    reviews: int | None
    yelp_rating: float | None
    price: str | None
    cuisine: str | None
    meals: list[str]
    status: str | None
    confirm: str | None
    season: str | None
    hours: list[str]  # opening hours lines when the bank carries them (enrichment adds them)
    links: dict[str, str]  # maps / website / tickets / reservations / order — authored, then enriched
    lat: float | None = None  # from the enrichment sidecar (places.json)
    lon: float | None = None
    place_id: str | None = None
    phone: str | None = None
    extra: dict[str, Any]  # unmodeled keys, passed through for the detail view


class BankGroup(BaseModel):
    key: str
    label: str
    kind: BankKind
    count: int


class VisitBank(BaseModel):
    """`hn travel bank --json`: the resolved visit and its merged bank."""

    visit: str  # slug; "" when no live visit carries a bank
    title: str
    doc: str  # the visit's folder-note, tracker-relative
    start: date | None
    end: date | None
    outings_title: str
    restaurants_title: str
    notes: list[str]  # the banks' own subtitles / legends, shown under the grid
    groups: list[BankGroup]
    items: list[BankItem]


class TripEntry(BaseModel):
    """One picker tile: a visit or trip folder and what it carries."""

    slug: str
    kind: str  # visit | trip
    title: str
    status: str
    when: str  # countdown / "completed" / a window hint
    date: str
    start: str | None
    end: str | None
    destination: str
    doc: str
    cover: str | None  # vault-relative cover photo
    outings: int
    restaurants: int
    photos: int
    scheduled: int
    live: bool
    phase: Literal["live", "someday", "past"] = "live"  # someday = a parked idea


class CalendarCard(BaseModel):
    """One Planning "on the calendar" card: a live (or parked) trip or visit at a glance."""

    slug: str
    kind: str  # visit | trip — also the picker scope its click lands on
    name: str  # the title's lead clause, emoji-free ("Beach weekend")
    title: str
    anchor: str
    destination: str
    who: list[str]
    status: str
    start: str | None
    end: str | None
    window: str  # "Fri 5/1 → Sun 5/3", or the corpus window hint when undated
    days: int | None  # days until start (0 = today, negative while it runs); None when undated
    when: str  # the pipeline's countdown grammar ("in 6d", "in 9w", "someday")
    cover: str | None
    doc: str
    decision: bool  # its folder carries a compare radar (the open-decision panel's source)


class PlanningCalendar(BaseModel):
    """`hn travel upcoming --json`: the Planning tab's top band. `upcoming` = live and dated,
    soonest first; `someday` = live-but-undated plus parked ideas — listed, never counted down."""

    as_of: str
    upcoming: list[CalendarCard]
    someday: list[CalendarCard]


class TripBanks(BaseModel):
    """`hn travel banks --json`: the Visits/Trips picker."""

    scope: str
    default: str | None  # the entry the other widgets show for "auto"
    entries: list[TripEntry]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _hours(v: Any) -> list[str]:
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, dict):
        return [f"{k}: {val}" for k, val in v.items()]
    return []


def _links(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {str(k): val for k, val in v.items() if isinstance(val, str) and val.startswith("http")}


def maps_search(*parts: str | None) -> str:
    """A Google Maps search URL for a place named by its label + area — the deterministic fallback
    when an older bank entry carries no `links.maps` (every tile keeps its Maps button)."""
    from urllib.parse import quote_plus

    label = re.sub(r"[^\w&'+.,() -]", " ", " ".join(p for p in parts if p)).strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(' '.join(label.split()))}"


def _image(v: Any, folder: str) -> str | None:
    """A bank image is relative to the visit folder ('assets/x.jpg'); rewrite it vault-relative.
    Absolute URLs and parent-escaping paths are dropped (the console reads vault files only)."""
    s = _as_str(v)
    if s is None or s.startswith(("http://", "https://", "/")) or ".." in s.split("/"):
        return None
    return f"{folder}/{s}"


def _unique(item_id: str, seen: set[str]) -> str:
    out, n = item_id, 2
    while out in seen:
        out, n = f"{item_id}-{n}", n + 1
    seen.add(out)
    return out


def parse_outings(data: Any, folder: str, seen: set[str]) -> tuple[list[BankItem], list[BankGroup]]:
    """schedule.json `.bank[]` → items. Groups are the entry's `group` text (keyed by its slug), in
    first-appearance order; `lane` is carried separately — it colors the item and can cut across
    groups (an older bank files dining and outings under one group). Older banks without
    ids/links/lanes parse."""
    bank = data.get("bank") if isinstance(data, dict) else None
    if not isinstance(bank, list):
        return [], []
    items: list[BankItem] = []
    groups: dict[str, BankGroup] = {}
    for raw in bank:
        if not isinstance(raw, dict):
            continue
        label = _as_str(raw.get("label")) or _as_str(raw.get("name"))
        if label is None:
            continue
        group_label = _as_str(raw.get("group")) or "Ideas"
        lane = _as_str(raw.get("lane"))
        key = slugify(group_label)
        g = groups.setdefault(key, BankGroup(key=key, label=group_label, kind="outing", count=0))
        g.count += 1
        indoor = raw.get("indoor")
        items.append(
            BankItem(
                id=_unique(_as_str(raw.get("id")) or slugify(label), seen),
                kind="outing",
                label=label,
                group=key,
                group_label=g.label,
                lane=lane,
                area=_as_str(raw.get("area")),
                drive_min=_as_int(raw.get("drive_min")),
                indoor=indoor if isinstance(indoor, bool) else None,
                why=_as_str(raw.get("why")),
                note=_as_str(raw.get("note")),
                image=_image(raw.get("image"), folder),
                images=[],
                gallery=raw.get("gallery") is not False,
                gallery_from=_as_str(raw.get("gallery_from")),
                rating=_as_float(raw.get("rating")),
                reviews=_as_int(raw.get("reviews")),
                yelp_rating=None,
                price=_as_str(raw.get("price")),
                cuisine=None,
                meals=[],
                status=None,
                confirm=None,
                season=_as_str(raw.get("season")),
                hours=_hours(raw.get("hours")),
                links=_links(raw.get("links")),
                extra={k: v for k, v in raw.items() if k not in _OUTING_KEYS},
            )
        )
    return items, list(groups.values())


def parse_restaurants(data: Any, folder: str, seen: set[str]) -> tuple[list[BankItem], list[BankGroup]]:
    """restaurants.json `.restaurants[]` → items. `.groups[]` sets display order and labels; a
    group an entry names but `.groups[]` omits is appended with its key as the label."""
    rows = data.get("restaurants") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], []
    order: dict[str, BankGroup] = {}
    for g in data.get("groups") or []:
        if isinstance(g, dict) and _as_str(g.get("key")):
            key = str(g["key"])
            order[key] = BankGroup(key=key, label=_as_str(g.get("label")) or key, kind="restaurant", count=0)
    items: list[BankItem] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        label = _as_str(raw.get("name")) or _as_str(raw.get("label"))
        if label is None:
            continue
        key = _as_str(raw.get("group")) or "other"
        g = order.setdefault(key, BankGroup(key=key, label=key, kind="restaurant", count=0))
        g.count += 1
        meals = raw.get("meals")
        indoor = raw.get("indoor")
        items.append(
            BankItem(
                id=_unique(_as_str(raw.get("id")) or slugify(label), seen),
                kind="restaurant",
                label=label,
                group=key,
                group_label=g.label,
                lane=None,
                area=_as_str(raw.get("area")),
                drive_min=_as_int(raw.get("drive_min")),
                indoor=indoor if isinstance(indoor, bool) else None,
                why=None,
                note=_as_str(raw.get("note")),
                image=_image(raw.get("image"), folder),
                images=[],
                gallery=raw.get("gallery") is not False,
                gallery_from=_as_str(raw.get("gallery_from")),
                rating=_as_float(raw.get("rating")),
                reviews=_as_int(raw.get("reviews")),
                yelp_rating=_as_float(raw.get("yelp_rating")),
                price=_as_str(raw.get("price")),
                cuisine=_as_str(raw.get("cuisine")),
                meals=[str(m) for m in meals] if isinstance(meals, list) else [],
                status=_as_str(raw.get("status")),
                confirm=_as_str(raw.get("confirm")),
                season=None,
                hours=_hours(raw.get("hours")),
                links=_links(raw.get("links")),
                extra={k: v for k, v in raw.items() if k not in _RESTAURANT_KEYS},
            )
        )
    return items, [g for g in order.values() if g.count]


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


_STOP = {"a", "an", "and", "at", "by", "for", "in", "near", "of", "on", "the", "to", "with", "w"}
REUSE_MIN_OVERLAP = 0.75  # share of the smaller label's words that must match to borrow a gallery


def name_words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", label.lower()) if w not in _STOP}


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


class _Gallery(BaseModel):
    ref: str  # "<slug>/<item-id>"
    kind: BankKind
    label: str
    place: str
    where: set[str]  # area + resolved address words — the tie-break between same-name places
    images: list[str]


def gallery_index(vault_root: Path) -> list[_Gallery]:
    """Every item gallery under travel/{visits,trips}/*/assets/gallery/, with the names it can be
    matched by (its bank label, its resolved place title). Read-time only — nothing is copied."""
    out: list[_Gallery] = []
    for base in ("visits", "trips"):
        root = vault_root / "travel" / base
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if (p / GALLERY_DIR).is_dir()):
            rel = f"travel/{base}/{folder.name}"
            seen: set[str] = set()
            items = parse_outings(_read_json(folder / "schedule.json"), rel, seen)[0]
            items += parse_restaurants(_read_json(folder / "restaurants.json"), rel, seen)[0]
            raw_places = _read_json(folder / "places.json")
            places: dict[str, Any] = raw_places if isinstance(raw_places, dict) else {}
            for it in items:
                files = gallery_files(folder, it.id)
                if not files:
                    continue
                cover = [it.image] if it.image else []
                hit = places.get(it.id)
                place: dict[str, Any] = hit if isinstance(hit, dict) else {}
                out.append(_Gallery(
                    ref=f"{folder.name}/{it.id}", kind=it.kind, label=it.label,
                    place=str(place.get("title") or ""),
                    where=name_words(f"{it.area or ''} {place.get('address') or ''} {it.label}"),
                    images=cover + [f"{rel}/{GALLERY_DIR}/{it.id}/{p.name}" for p in files],
                ))
    return out


def borrow_gallery(item: BankItem, own_slug: str, index: list[_Gallery]) -> _Gallery | None:
    """The gallery another trip/visit already holds for this item's place: an explicit
    `gallery_from` ref, else an exact name match (bank label or resolved place title), else a word
    overlap of ≥75% of the smaller label (≥2 words) — always within the same kind."""
    pool = [g for g in index if not g.ref.startswith(f"{own_slug}/")]
    if item.gallery_from:
        return next((g for g in index if g.ref == item.gallery_from), None)
    pool = [g for g in pool if g.kind == item.kind]
    here = name_words(item.area or "")

    def near(g: _Gallery) -> int:
        return len(here & g.where)

    key = _norm(item.label)
    exact = [g for g in pool if key and key in (_norm(g.label), _norm(g.place))]
    if exact:
        return max(exact, key=near)  # same name in two towns: the one sharing the item's area
    mine = name_words(item.label)
    best_key: tuple[float, int] = (0.0, 0)
    best: _Gallery | None = None
    for g in pool:
        for theirs in (name_words(g.label), name_words(g.place)):
            small = min(len(mine), len(theirs))
            if small < 2:
                continue
            key2 = (len(mine & theirs) / small, near(g))
            if key2 > best_key:
                best_key, best = key2, g
    return best if best_key[0] >= REUSE_MIN_OVERLAP else None


def has_bank(folder: Path) -> bool:
    sched = _read_json(folder / "schedule.json")
    if isinstance(sched, dict) and isinstance(sched.get("bank"), list) and sched["bank"]:
        return True
    rest = _read_json(folder / "restaurants.json")
    return isinstance(rest, dict) and isinstance(rest.get("restaurants"), list) and bool(rest["restaurants"])


def empty_bank() -> VisitBank:
    return VisitBank(
        visit="", title="No live visit carries a bank yet", doc="", start=None, end=None,
        outings_title="", restaurants_title="", notes=[], groups=[], items=[],
    )


_DAY_ABBR = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
             "friday": "Fri", "saturday": "Sat", "sunday": "Sun"}


def apply_place(it: BankItem, place: Any) -> None:
    """Merge an enrichment sidecar entry (places.json) into an item. Authored values win: hours and
    links the bank already carries are kept; the sidecar fills what's missing."""
    if not isinstance(place, dict):
        return
    it.lat = _as_float(place.get("lat"))
    it.lon = _as_float(place.get("lon"))
    it.place_id = _as_str(place.get("place_id"))
    it.phone = _as_str(place.get("phone"))
    if not it.hours and isinstance(place.get("hours"), dict):
        # Monday-first by name: the sidecar is written with sorted keys, so its order is alphabetical
        week = place["hours"]
        it.hours = [f"{abbr} {week[d]}" for d, abbr in _DAY_ABBR.items() if week.get(d)]
    for key, field in (("reservations", "reservation_url"), ("order", "order_url"), ("menu", "menu_url"),
                       ("website", "website")):
        v = place.get(field)
        if key not in it.links and isinstance(v, str) and v.startswith("http"):
            it.links[key] = v
    if "maps" not in it.links and it.place_id:
        from urllib.parse import quote_plus

        it.links["maps"] = (
            "https://www.google.com/maps/search/?api=1"
            f"&query={quote_plus(it.label)}&query_place_id={it.place_id}"
        )


def build_bank(trip: Trip, vault_root: Path, kind: BankKind | None = None) -> VisitBank:
    """Merge one visit folder's banks. `kind` limits the output to outings or restaurants."""
    doc = vault_root / trip.doc
    folder_rel = str(Path(trip.doc).parent)
    sched = _read_json(doc.parent / "schedule.json")
    rest = _read_json(doc.parent / "restaurants.json")
    seen: set[str] = set()
    items: list[BankItem] = []
    groups: list[BankGroup] = []
    notes: list[str] = []
    if kind in (None, "outing"):
        o_items, o_groups = parse_outings(sched, folder_rel, seen)
        items += o_items
        groups += o_groups
    if kind in (None, "restaurant"):
        r_items, r_groups = parse_restaurants(rest, folder_rel, seen)
        items += r_items
        groups += r_groups
        if isinstance(rest, dict):
            for key in ("subtitle", "status_legend", "confirm_note"):
                if _as_str(rest.get(key)):
                    notes.append(str(rest[key]))
    index: list[_Gallery] | None = None
    places_raw = _read_json(doc.parent / "places.json")
    places: dict[str, Any] = places_raw if isinstance(places_raw, dict) else {}
    for it in items:
        apply_place(it, places.get(it.id))
        if "maps" not in it.links and it.gallery:
            it.links = {"maps": maps_search(it.label, it.area), **it.links}
        rel = [f"{folder_rel}/{GALLERY_DIR}/{it.id}/{p.name}" for p in gallery_files(doc.parent, it.id)]
        if not rel and it.gallery:
            # no gallery of its own: reuse the one another trip/visit holds for the same place
            index = gallery_index(vault_root) if index is None else index
            hit = borrow_gallery(it, trip.slug, index)
            if hit is not None:
                it.gallery_from = hit.ref
                rel = [p for p in hit.images if p != it.image]
        it.images = ([it.image] if it.image else []) + rel
        if it.image is None and rel:
            it.image = rel[0]
    sched_d = sched if isinstance(sched, dict) else {}
    rest_d = rest if isinstance(rest, dict) else {}
    return VisitBank(
        visit=trip.slug,
        title=trip.title or trip.anchor or trip.slug,
        doc=trip.doc,
        start=trip.start,
        end=trip.end,
        outings_title=_as_str(sched_d.get("bankTitle")) or "Outings",
        restaurants_title=_as_str(rest_d.get("title")) or "Restaurants",
        notes=notes,
        groups=groups,
        items=items,
    )


def resolve_visit(
    trips: list[Trip], vault_root: Path, slug: str | None = None, scope: str = "visit"
) -> Trip | None:
    """`slug` picks that trip or visit (any status, any kind); otherwise the soonest LIVE one of
    `scope` ("visit" | "trip") whose folder carries a bank — the Planning radar's ordering."""
    if slug:
        return next((t for t in trips if t.slug == slug), None)
    visits = [t for t in trips if t.kind == scope]
    live = sorted((t for t in visits if is_live(t.status)), key=lambda t: t.sort_date)
    past = sorted((t for t in visits if t.status in DONE_STATUSES), key=lambda t: t.sort_date, reverse=True)
    # nothing upcoming carries a bank yet → the most recent past one that does
    for t in live + past:
        doc = vault_root / t.doc
        # folder-note visits only: a flat visit's parent is visits/ itself
        if doc.parent.name == t.slug and has_bank(doc.parent):
            return t
    return None


def bank_kind(value: str | None) -> BankKind | None:
    """CLI/MCP `--kind` → the item kind filter (outings|restaurants|all)."""
    v = (value or "all").strip().lower()
    if v in ("all", ""):
        return None
    if v in ("outings", "outing"):
        return "outing"
    if v in ("restaurants", "restaurant", "food"):
        return "restaurant"
    raise ValueError(f"unknown --kind {value!r} (outings | restaurants | all)")


__all__ = [
    "GALLERY_DIR", "BankGroup", "BankItem", "TripBanks", "TripEntry", "VisitBank", "bank_kind",
    "build_bank", "empty_bank",
    "borrow_gallery", "gallery_files", "gallery_index", "has_bank", "maps_search", "name_words",
    "parse_outings", "parse_restaurants", "resolve_visit", "slugify",
]
