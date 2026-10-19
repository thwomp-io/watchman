"""Food / eatery discovery — the two-tier provider.

Tier 1 — **OSM Overpass, KEYLESS/free (default)**: enumerate every mapped eatery near a point
(name, category, cuisine, hours, website). This is the fix for banking restaurants from memory:
enumerate what exists from map data instead of recalling venues.
Open-source data, no key, no quota: aligned with the privacy/self-hosted bias.

Tier 2 — **SerpAPI google_maps local results, QUOTA (opt-in)**: ratings / review counts / price
for the same area — "what's good". 1 search per query against the shared 250/mo budget, date-keyed
cache like hotels (re-views cost 0). Callers confirm before spending (hotels/flights
discipline).

The service merges: OSM enumeration enriched with Google ratings on normalized-name match; Google-
only finds appended (OSM coverage gaps happen — small-town data quality varies honestly).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from harness._http import get_with_retry
from harness.paths import cache_file
from harness.travel.models import Eatery
from harness.travel.providers.base import ProviderError

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_UA = "harness/0.1 (personal travel-planning harness)"
_AMENITIES = "restaurant|cafe|bar|pub|fast_food|ice_cream"
_SHOPS = "bakery|deli"

_CACHE_SUBDIR = "food"  # under harness.paths.harness_cache_dir() — sealable via HARNESS_CACHE_DIR
_CACHE_TTL_S = 24 * 3600  # local-pack ratings drift slowly; day-old is fine + costs 0 quota


class OverpassFoodProvider:
    """Keyless OSM Overpass client — enumerates mapped eateries around a point."""

    name = "overpass"

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 40.0) -> None:
        self._client = client
        self._timeout = timeout

    def eateries_near(self, latitude: float, longitude: float, *, radius_m: int = 1500) -> list[Eatery]:
        around = f"(around:{radius_m},{round(latitude, 5)},{round(longitude, 5)})"
        query = (
            "[out:json][timeout:30];("
            f'nwr["amenity"~"^({_AMENITIES})$"]["name"]{around};'
            f'nwr["shop"~"^({_SHOPS})$"]["name"]{around};'
            ");out center tags;"
        )
        try:
            resp = get_with_retry(
                _OVERPASS_URL, params={"data": query}, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
            elements = resp.json().get("elements") or []
        except httpx.HTTPError as e:
            raise ProviderError(f"Overpass query failed near ({latitude},{longitude}): {e}") from e
        except ValueError as e:
            raise ProviderError(f"Overpass returned non-JSON: {e}") from e

        out: list[Eatery] = []
        seen: set[str] = set()
        for el in elements:
            tags = el.get("tags") or {}
            name = tags.get("name", "").strip()
            if not name or normalize_name(name) in seen:
                continue
            seen.add(normalize_name(name))
            out.append(
                Eatery(
                    name=name,
                    category=tags.get("amenity") or tags.get("shop") or "",
                    cuisine=(tags.get("cuisine") or "").replace("_", " ").replace(";", ", "),
                    address=_osm_address(tags),
                    website=tags.get("website") or tags.get("contact:website") or "",
                    opening_hours=tags.get("opening_hours") or "",
                    sources=["osm"],
                )
            )
        out.sort(key=lambda e: e.name.lower())
        return out


class SerpApiLocalFoodProvider:
    """SerpAPI google_maps local results — the opt-in ratings tier. 1 search per query, cached."""

    name = "serpapi-food"

    def __init__(
        self, api_key: str | None, *, cache_dir: Path | None = None, ttl_s: int = _CACHE_TTL_S
    ) -> None:
        self._api_key = api_key
        self._cache_dir = cache_dir or cache_file(_CACHE_SUBDIR)
        self._ttl_s = ttl_s
        self.search_count = 0  # live searches actually spent (quota)
        self.cache_hits = 0

    # --- seam for tests: override to feed canned JSON instead of hitting the network/quota ---
    def _raw_search(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError("SERPAPI_KEY is not set — cannot run a live local-ratings search.")
        import serpapi  # lazy import so unit tests need no network/SDK auth

        client = serpapi.Client(api_key=self._api_key)
        self.search_count += 1
        return dict(client.search(params))

    def rated_eateries(
        self, place_label: str, latitude: float, longitude: float, *, refresh: bool = False
    ) -> list[Eatery]:
        params: dict[str, Any] = {
            "engine": "google_maps",
            "q": f"restaurants in {place_label}",
            "ll": f"@{round(latitude, 5)},{round(longitude, 5)},14z",
            "type": "search",
            "hl": "en",
        }
        raw, _ = self._cached_or_search(params, refresh=refresh)
        out: list[Eatery] = []
        for r in raw.get("local_results") or []:
            name = (r.get("title") or "").strip()
            if not name:
                continue
            out.append(
                Eatery(
                    name=name,
                    category="restaurant",
                    cuisine=r.get("type") or "",
                    address=r.get("address") or "",
                    website=r.get("website") or "",
                    rating=r.get("rating"),
                    reviews=r.get("reviews"),
                    price=r.get("price") or "",
                    sources=["google"],
                    # free riders on the same response (use-the-full-response rule):
                    thumbnail=r.get("thumbnail") or "",
                    data_id=r.get("data_id") or "",
                )
            )
        return out

    def yelp_eateries(
        self, find_loc: str, *, find_desc: str = "restaurants", refresh: bool = False
    ) -> list[Eatery]:
        """SerpAPI `yelp` engine — the review-TEXTURE tier. 1 quota search, cached
        like everything else. Returns Yelp's organic ranking for `find_desc` near `find_loc`
        (a location STRING — Yelp geocodes it; no lat/lng needed, unlike google_maps).

        What this tier adds over the google_maps ratings pack: the review `snippet` (the
        what-locals-say one-liner), `neighborhoods`, Yelp's own rating/review-count (kept separate —
        cross-source divergence is signal), and `place_ids` (the key the yelp_reviews engine will
        want for the deep-reviews follow-up). `find_desc` is a real lens — "Best dinner
        restaurants" / "brunch" / "cocktail bars" each pull a different Yelp ranking."""
        params: dict[str, Any] = {
            "engine": "yelp",
            "find_desc": find_desc,
            "find_loc": find_loc,
        }
        raw, _ = self._cached_or_search(params, refresh=refresh)
        out: list[Eatery] = []
        for r in raw.get("organic_results") or []:
            name = (r.get("title") or "").strip()
            if not name:
                continue
            categories = ", ".join(
                c.get("title", "") for c in (r.get("categories") or []) if c.get("title")
            )
            place_ids = r.get("place_ids") or []
            out.append(
                Eatery(
                    name=name,
                    category="restaurant",
                    cuisine=categories,
                    sources=["yelp"],
                    yelp_rating=r.get("rating"),
                    yelp_reviews=r.get("reviews"),
                    price=r.get("price") or "",
                    snippet=(r.get("snippet") or "").strip(),
                    neighborhood=r.get("neighborhoods") or "",
                    yelp_url=r.get("link") or "",
                    yelp_place_id=str(place_ids[0]) if place_ids else "",
                    thumbnail=r.get("thumbnail") or "",
                )
            )
        return out

    def place_photos(self, data_id: str, *, refresh: bool = False, limit: int = 12) -> list[str]:
        """Full photo gallery for ONE place (google_maps_photos engine) — 1 quota search per place,
        date-cached like everything else. The rich layer for FINALISTS, never the whole sweep:
        the local-pack thumbnail is free; this is the tap-into-the-restaurant gallery."""
        params: dict[str, Any] = {"engine": "google_maps_photos", "data_id": data_id, "hl": "en"}
        raw, _ = self._cached_or_search(params, refresh=refresh)
        out: list[str] = []
        for ph in raw.get("photos") or []:
            url = ph.get("image") or ph.get("thumbnail") or ""
            if url:
                out.append(url)
            if len(out) >= limit:
                break
        return out

    def targeted_place(
        self, name: str, latitude: float, longitude: float, *, refresh: bool = False
    ) -> Eatery | None:
        """Targeted per-name lookup — 1 quota search for ONE named place. The
        local-pack sweep favors Google's 'prominent' tier and famously misses institutions
        (a legendary taqueria loses the pack to trendy sit-downs); this asks for the place BY NAME.
        Returns the top match (place_results, else first local_result) or an honest None."""
        params: dict[str, Any] = {
            "engine": "google_maps",
            "q": name,
            "ll": f"@{round(latitude, 5)},{round(longitude, 5)},15z",
            "type": "search",
            "hl": "en",
        }
        raw, _ = self._cached_or_search(params, refresh=refresh)
        r = raw.get("place_results") or next(iter(raw.get("local_results") or []), None)
        if not isinstance(r, dict) or not (r.get("title") or "").strip():
            return None
        return Eatery(
            name=r["title"].strip(),
            category="restaurant",
            cuisine=r.get("type") or "",
            address=r.get("address") or "",
            website=r.get("website") or "",
            rating=r.get("rating"),
            reviews=r.get("reviews"),
            price=r.get("price") or "",
            sources=["google"],
            thumbnail=r.get("thumbnail") or "",
            data_id=r.get("data_id") or "",
        )

    def known_places(self) -> dict[str, dict[str, Any]]:
        """Every place an earlier google_maps search already returned, keyed by `normalize_name`
        of its title — read from the on-disk cache regardless of age (a place id doesn't go
        stale the way ratings do). Costs zero quota; a resolver checks here before searching."""
        out: dict[str, dict[str, Any]] = {}
        if not self._cache_dir.is_dir():
            return out
        for path in sorted(self._cache_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            engine = (raw.get("search_parameters") or {}).get("engine") if isinstance(raw, dict) else None
            if engine != "google_maps":
                continue
            rows = list(raw.get("local_results") or [])
            if isinstance(raw.get("place_results"), dict):
                rows.append(raw["place_results"])
            for r in rows:
                if isinstance(r, dict) and r.get("data_id") and (r.get("title") or "").strip():
                    out.setdefault(normalize_name(r["title"]), _place_record(r))
        return out

    def cached_details(self) -> dict[str, dict[str, Any]]:
        """Every place record the on-disk cache holds, keyed by `data_id`, merged across the
        searches that returned it. Any age: hours and booking links drift slowly, and an
        enrichment pass re-fetches on --refresh. Costs zero quota."""
        out: dict[str, dict[str, Any]] = {}
        if not self._cache_dir.is_dir():
            return out
        for path in sorted(self._cache_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            engine = (raw.get("search_parameters") or {}).get("engine") if isinstance(raw, dict) else None
            if engine != "google_maps":
                continue
            rows = [(r, 0) for r in raw.get("local_results") or []]
            if isinstance(raw.get("place_results"), dict):
                rows.append((raw["place_results"], 1))
            # list rows first, then place pages: a page's non-empty fields override a row's, and
            # a row fills what the page lacks (hours can sit on one, the booking link on the other)
            for r, _is_page in sorted(rows, key=lambda x: x[1]):
                if not isinstance(r, dict) or not r.get("data_id"):
                    continue
                merged = out.setdefault(str(r["data_id"]), {})
                merged.update({k: v for k, v in r.items() if v not in (None, "", [], {})})
        return out

    def lookup_place(
        self, query: str, latitude: float, longitude: float, *, zoom: int = 11, refresh: bool = False
    ) -> dict[str, Any] | None:
        """1 quota search: the top Google Maps match for a free-text place query ("name, area")
        around a point. `zoom` sets the search radius (11z ≈ a metro area). Returns
        {data_id, title, address, lat, lon, type} or None — never a guessed place."""
        params: dict[str, Any] = {
            "engine": "google_maps",
            "q": query,
            "ll": f"@{round(latitude, 5)},{round(longitude, 5)},{zoom}z",
            "type": "search",
            "hl": "en",
        }
        raw, _ = self._cached_or_search(params, refresh=refresh)
        r = raw.get("place_results") or next(iter(raw.get("local_results") or []), None)
        if not isinstance(r, dict) or not r.get("data_id") or not (r.get("title") or "").strip():
            return None
        return _place_record(r)

    def place_gallery(self, data_id: str, *, refresh: bool = False, edge: int = 1200) -> list[str]:
        """1 quota search: a place's photo gallery as download URLs, cleaned of what never makes a
        good tile: video entries (their `image` is a poster frame), and gstatic thumbnails (tiny,
        low-res). Google photo URLs carry a size suffix (`=w3072-h3072-k-no`); it is rewritten to
        `edge` so a download is gallery-sized, not the multi-megabyte original."""
        params: dict[str, Any] = {"engine": "google_maps_photos", "data_id": data_id, "hl": "en"}
        raw, _ = self._cached_or_search(params, refresh=refresh)
        out: list[str] = []
        for ph in raw.get("photos") or []:
            if not isinstance(ph, dict) or ph.get("video"):
                continue
            url = str(ph.get("image") or "")
            if not url.startswith("https://lh3.googleusercontent.com/"):
                continue
            url = re.sub(r"=w\d+-h\d+(-k-no)?$", f"=w{edge}-h{edge}-k-no", url)
            if url not in out:
                out.append(url)
        return out

    def _cached_or_search(
        self, params: dict[str, Any], *, refresh: bool
    ) -> tuple[dict[str, Any], bool]:
        """Return (raw_response, from_cache). A fresh on-disk cache entry costs zero quota."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
        path = self._cache_dir / f"{key}.json"
        if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < self._ttl_s:
            self.cache_hits += 1
            return json.loads(path.read_text()), True
        raw = self._raw_search(params)
        path.write_text(json.dumps(raw))
        return raw, False


def _place_record(r: dict[str, Any]) -> dict[str, Any]:
    gps = r.get("gps_coordinates") or {}
    return {
        "data_id": str(r["data_id"]),
        "title": str(r["title"]).strip(),
        "address": r.get("address") or "",
        "lat": gps.get("latitude"),
        "lon": gps.get("longitude"),
        "type": r.get("type") or "",
    }


def normalize_name(name: str) -> str:
    """Merge key across tiers: lowercase, alphanumerics only ('Ultra House Ramen' == 'ultra-house ramen')."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def merge_eateries(osm: list[Eatery], rated: list[Eatery]) -> list[Eatery]:
    """OSM enumeration enriched with Google ratings on name match; Google-only finds appended.

    Both tiers are honest about gaps: OSM may miss a place Google ranks (append it), Google's local
    pack is ~20 results (an unrated OSM row is NOT a bad sign — just below the pack fold)."""
    by_key = {normalize_name(e.name): e for e in osm}
    merged_keys: set[str] = set()
    for r in rated:
        key = normalize_name(r.name)
        if key in by_key:
            base = by_key[key]
            base.rating, base.reviews = r.rating, r.reviews
            base.price = r.price or base.price
            base.cuisine = base.cuisine or r.cuisine
            base.address = base.address or r.address
            base.thumbnail = r.thumbnail or base.thumbnail
            base.data_id = r.data_id or base.data_id
            base.sources = [*base.sources, "google"]
            merged_keys.add(key)
        else:
            by_key[key] = r
    out = list(by_key.values())
    out.sort(key=lambda e: (-(e.rating or 0), -(e.reviews or 0), e.name.lower()))
    return out


def merge_yelp_eateries(base: list[Eatery], yelp: list[Eatery]) -> list[Eatery]:
    """Yelp texture merged onto the existing rows on normalized-name match; Yelp-only finds
    appended. Same honest-gaps semantics as `merge_eateries` — Yelp's organic page is ~10 rows, so
    an un-snippeted row just fell below Yelp's fold. Google fields are never overwritten (the
    separate-ratings doctrine on the Eatery model); Yelp contributes its own fields + fallbacks
    for price/cuisine when the base row has none."""
    by_key = {normalize_name(e.name): e for e in base}
    for y in yelp:
        key = normalize_name(y.name)
        if key in by_key:
            row = by_key[key]
            row.yelp_rating, row.yelp_reviews = y.yelp_rating, y.yelp_reviews
            row.snippet = y.snippet or row.snippet
            row.neighborhood = y.neighborhood or row.neighborhood
            row.yelp_url, row.yelp_place_id = y.yelp_url, y.yelp_place_id
            row.price = row.price or y.price
            row.cuisine = row.cuisine or y.cuisine
            row.thumbnail = row.thumbnail or y.thumbnail
            row.sources = [*row.sources, "yelp"]
        else:
            by_key[key] = y
    out = list(by_key.values())
    # Sort keeps the Google-rating primary ordering; Yelp-only rows rank by their own rating.
    out.sort(
        key=lambda e: (-(e.rating or e.yelp_rating or 0), -(e.reviews or e.yelp_reviews or 0), e.name.lower())
    )
    return out


# The og:image regex is deliberately permissive about attribute order/quoting — restaurant sites
# run every CMS under the sun. content="..." captured either side of property="og:image".
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image(?::secure_url)?["\']',
    re.IGNORECASE,
)


def enrich_hero_images(
    eateries: list[Eatery], *, limit: int = 25, timeout: float = 6.0,
    client: httpx.Client | None = None,
) -> int:
    """KEYLESS imagery enrichment: fetch each eatery's OWN website and lift its og:image hero —
    usually the place's best food/interior shot, no API, no quota. Read-only
    public GETs with the tool UA; per-site failures are silent skips (a dead restaurant site is
    normal, never a dead run). Returns how many heroes landed. `limit` bounds total fetches —
    enrichment is for the report tier, not an unbounded crawl."""
    from harness._http import USER_AGENT

    own = client or httpx.Client(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    hits = 0
    fetched = 0
    try:
        for e in eateries:
            if fetched >= limit:
                break
            if not e.website or e.hero_image:
                continue
            fetched += 1
            try:
                resp = own.get(e.website)
                if resp.status_code >= 400:
                    continue
                m = _OG_IMAGE_RE.search(resp.text[:200_000])
                if m:
                    url = (m.group(1) or m.group(2) or "").strip()
                    # protocol-relative + relative URLs resolved against the site
                    if url.startswith("//"):
                        url = "https:" + url
                    elif url.startswith("/"):
                        url = str(httpx.URL(str(resp.url)).join(url))
                    if url.startswith("http"):
                        e.hero_image = url
                        hits += 1
            except httpx.HTTPError:
                continue  # dead/slow site — honest skip
    finally:
        if client is None:
            own.close()
    return hits


def _osm_address(tags: dict[str, Any]) -> str:
    num, street = tags.get("addr:housenumber", ""), tags.get("addr:street", "")
    city = tags.get("addr:city", "")
    street_part = f"{num} {street}".strip()
    return ", ".join(p for p in (street_part, city) if p)


def build_overpass_food_provider() -> OverpassFoodProvider:
    """Factory mirror of the other keyless providers."""
    return OverpassFoodProvider()
