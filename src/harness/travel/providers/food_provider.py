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
from harness.travel.models import Eatery
from harness.travel.providers.base import ProviderError

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_UA = "harness/0.1 (personal travel-planning harness)"
_AMENITIES = "restaurant|cafe|bar|pub|fast_food|ice_cream"
_SHOPS = "bakery|deli"

_CACHE_DIR = Path.home() / ".cache" / "harness" / "food"
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
        self._cache_dir = cache_dir or _CACHE_DIR
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
