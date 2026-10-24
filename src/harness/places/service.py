"""Ad-hoc local place search — "which places near X do Y", for any lane (a recycler, an installer, food
near a neighborhood, what's around a listing).

The current layer is Google Maps (hours, phone, open-now, rating) via SerpAPI's `google_maps` engine,
through the travel lane's provider: the same key, the same on-disk cache discipline, and every live
search counts against the same SerpAPI account (`hn travel quota` reads it). A search is priced
before it is spent: `plan()` says how many live searches a run would cost and what the cache already
covers; the caller passes `spend=True` to run them.

`--near` is resolved by Google itself (a ZIP or a neighborhood name resolves the way the Maps search
box does — the keyless geocoder places ZIPs at their city's center and misreads neighborhood names),
once per distinct value: a place doesn't move, so that lookup is cached for good. A literal
`lat,lon` costs nothing. Nothing defaults to a home address and nothing is logged beyond the cache.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from harness.paths import cache_file
from harness.travel.providers.base import ProviderError
from harness.travel.providers.food_provider import SerpApiLocalFoodProvider

RESULTS_TTL_S = 24 * 3600  # hours and open/closed drift; a day-old answer is still useful
ANCHOR_TTL_S = 10 * 365 * 24 * 3600  # a ZIP or a neighborhood doesn't move
ZOOM = 13  # ≈ a few miles across: "near" for an errand
_LATLON = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


class Anchor(BaseModel):
    label: str  # what Google resolved it to ("Springfield, ST 12345")
    lat: float
    lon: float


class Place(BaseModel):
    name: str
    address: str = ""
    distance_km: float | None = None
    distance_mi: float | None = None
    phone: str = ""
    open_state: str = ""  # Google's own line: "Open · Closes 5 PM", "Closed · Opens 9 AM Thu"
    today_hours: str = ""
    hours: dict[str, str] = Field(default_factory=dict)  # weekday → hours, when Google has them
    rating: float | None = None
    reviews: int | None = None
    type: str = ""
    types: list[str] = Field(default_factory=list)
    website: str = ""
    place_id: str = ""
    lat: float | None = None
    lon: float | None = None
    description: str = ""


class Plan(BaseModel):
    """What a run would cost before anything is spent."""

    anchor_cached: bool  # the --near lookup is already cached (or free coordinates)
    search_cached: bool  # the place search itself is fresh in the cache (needs the anchor)
    cost: int  # live searches the run would spend


class SearchResult(BaseModel):
    query: str
    near: str
    anchor: Anchor | None = None
    places: list[Place] = Field(default_factory=list)
    plan: Plan
    spent: int = 0  # live searches this run actually made
    from_cache: bool = False


class PlacesService:
    def __init__(self, api_key: str | None, *, cache_dir: Path | None = None) -> None:
        root = cache_dir or cache_file("places")
        self.results = SerpApiLocalFoodProvider(api_key, cache_dir=root, ttl_s=RESULTS_TTL_S)
        self.anchors = SerpApiLocalFoodProvider(api_key, cache_dir=root / "anchors", ttl_s=ANCHOR_TTL_S)

    # ---- the near point ----

    @staticmethod
    def _anchor_params(near: str) -> dict[str, Any]:
        return {"engine": "google_maps", "q": near.strip(), "type": "search", "hl": "en"}

    @staticmethod
    def _search_params(query: str, anchor: Anchor) -> dict[str, Any]:
        return {
            "engine": "google_maps",
            "q": query.strip(),
            "ll": f"@{round(anchor.lat, 5)},{round(anchor.lon, 5)},{ZOOM}z",
            "type": "search",
            "hl": "en",
        }

    def _literal(self, near: str) -> Anchor | None:
        m = _LATLON.match(near)
        return Anchor(label=near.strip(), lat=float(m.group(1)), lon=float(m.group(2))) if m else None

    def _cached_anchor(self, near: str) -> Anchor | None:
        if (lit := self._literal(near)) is not None:
            return lit
        params = self._anchor_params(near)
        if not self.anchors.is_cached(params):
            return None
        raw, _ = self.anchors.search(params)
        return anchor_from(raw, near)

    def resolve_anchor(self, near: str) -> Anchor:
        """The near point (spends one search when it isn't cached and isn't coordinates)."""
        if (a := self._cached_anchor(near)) is not None:
            return a
        raw, _ = self.anchors.search(self._anchor_params(near))
        a = anchor_from(raw, near)
        if a is None:
            raise ProviderError(f"Google Maps couldn't place {near!r} — try a ZIP, a town, or lat,lon")
        return a

    # ---- search ----

    def plan(self, query: str, near: str) -> Plan:
        anchor = self._cached_anchor(near)
        search_cached = anchor is not None and self.results.is_cached(self._search_params(query, anchor))
        cost = (0 if anchor is not None else 1) + (0 if search_cached else 1)
        return Plan(anchor_cached=anchor is not None, search_cached=search_cached, cost=cost)

    def search(
        self, query: str, near: str, *, limit: int = 10, spend: bool = False, refresh: bool = False,
        by_distance: bool = False, today: date | None = None,
    ) -> SearchResult:
        """Places matching `query` near `near`, in Google's relevance order (the search is already
        centered on `near`, so results sit within a few miles; `by_distance` sorts nearest first —
        on a category query it surfaces kiosks and shops over the actual service). Without `spend`,
        a run that needs a live search returns only its plan."""
        plan = self.plan(query, near)
        if refresh:
            extra = 1 if plan.search_cached else 0
            plan = Plan(anchor_cached=plan.anchor_cached, search_cached=False, cost=plan.cost + extra)
        out = SearchResult(query=query, near=near, plan=plan)
        if plan.cost and not spend:
            return out
        before = self.results.search_count + self.anchors.search_count
        anchor = self.resolve_anchor(near)
        raw, cached = self.results.search(self._search_params(query, anchor), refresh=refresh)
        out.anchor, out.from_cache = anchor, cached
        out.spent = self.results.search_count + self.anchors.search_count - before
        places = [place_from(r, anchor, today or date.today()) for r in raw.get("local_results") or []]
        if isinstance(raw.get("place_results"), dict):  # a query Google answers with one place page
            places = [place_from(raw["place_results"], anchor, today or date.today())]
        if by_distance:
            places.sort(key=lambda p: p.distance_km if p.distance_km is not None else math.inf)
        out.places = places[: max(1, limit)]
        return out

    # ---- one place ----

    def cached_place(self, place_id: str) -> dict[str, Any] | None:
        """The richest record any cached search holds for `place_id` (free, any age)."""
        best: dict[str, Any] | None = None
        root = self.results._cache_dir
        if not root.is_dir():
            return None
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            rows = list(raw.get("local_results") or [])
            if isinstance(raw.get("place_results"), dict):
                rows.append(raw["place_results"])
            for r in rows:
                if isinstance(r, dict) and r.get("place_id") == place_id:
                    best = {**(best or {}), **{k: v for k, v in r.items() if v not in (None, "", [], {})}}
        return best

    def show(
        self, place_id: str, *, spend: bool = False, today: date | None = None
    ) -> tuple[Place | None, int]:
        """(place, live searches spent). Free when a cached search returned it; otherwise one place-page
        lookup, only with `spend`. (None, 0) = not cached and not spent."""
        rec = self.cached_place(place_id)
        if rec is not None:
            return place_from(rec, None, today or date.today()), 0
        if not spend:
            return None, 0
        raw, cached = self.results.search({"engine": "google_maps", "place_id": place_id, "hl": "en"})
        page = raw.get("place_results")
        if not isinstance(page, dict):
            raise ProviderError(f"no place page for {place_id}")
        return place_from(page, None, today or date.today()), 0 if cached else 1


# ---- parsing (pure) ----

def _clean(s: Any) -> str:
    """Google pads times with narrow no-break spaces ("9 PM")."""
    return str(s or "").replace(" ", " ").replace("\xa0", " ").strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def anchor_from(raw: dict[str, Any], near: str) -> Anchor | None:
    """The point Google resolved `near` to: its place page, else the first result."""
    page = raw.get("place_results")
    r = page if isinstance(page, dict) else next(iter(raw.get("local_results") or []), None)
    gps = (r or {}).get("gps_coordinates") or {}
    if gps.get("latitude") is None or gps.get("longitude") is None:
        return None
    label = _clean((r or {}).get("address") or (r or {}).get("title") or near)
    return Anchor(label=label, lat=float(gps["latitude"]), lon=float(gps["longitude"]))


def _hours(r: dict[str, Any]) -> dict[str, str]:
    oh = r.get("operating_hours")
    if isinstance(oh, dict):
        return {str(k).lower(): _clean(v) for k, v in oh.items()}
    out: dict[str, str] = {}
    if isinstance(r.get("hours"), list):  # a place page: [{"monday": "9 AM–5 PM"}, …]
        for item in r["hours"]:
            if isinstance(item, dict):
                out.update({str(k).lower(): _clean(v) for k, v in item.items()})
    return out


def place_from(r: dict[str, Any], anchor: Anchor | None, today: date) -> Place:
    gps = r.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    km = mi = None
    if anchor is not None and lat is not None and lon is not None:
        km = round(haversine_km(anchor.lat, anchor.lon, float(lat), float(lon)), 1)
        mi = round(km / 1.609344, 1)
    hours = _hours(r)
    open_state = r.get("open_state") or (r.get("hours") if isinstance(r.get("hours"), str) else "")
    reviews = r.get("reviews")
    return Place(
        name=_clean(r.get("title")),
        address=_clean(r.get("address")),
        distance_km=km,
        distance_mi=mi,
        phone=_clean(r.get("phone")),
        open_state=_clean(open_state),
        today_hours=hours.get(today.strftime("%A").lower(), ""),
        hours=hours,
        rating=r.get("rating"),
        reviews=int(reviews) if isinstance(reviews, int | float) else None,
        type=_clean(r.get("type")),
        types=[_clean(t) for t in r.get("types") or []],
        website=_clean(r.get("website")),
        place_id=_clean(r.get("place_id")),
        lat=lat,
        lon=lon,
        description=_clean(r.get("description")),
    )
