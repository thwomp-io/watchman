"""SerpAPI / Google Hotels provider — the lodging-research layer (v1).

Quota economics (the load-bearing design point): **ONE google_hotels search returns a whole ranked
list of bookable properties** for a destination+dates — not one search per hotel. So pitching N
luxe-stay options costs 1 of the free 250/mo SerpAPI quota (shared with flights). A **date-keyed
on-disk cache** makes re-viewing the same destination+dates cost ZERO further searches (prices drift
slowly; a day-old cache is fine for research). Opt-in by design — callers run it deliberately (like
flights `rank`), never auto-fired during surface/profile.

Mirrors the flights provider's `_raw_search` seam so unit tests never burn quota.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from harness.travel.models import HotelOffer, HotelQuery, HotelSearch, NearbyPlace
from harness.travel.providers.base import ProviderError

_CACHE_DIR = Path.home() / ".cache" / "harness" / "hotels"
_CACHE_TTL_S = 24 * 3600  # prices drift slowly; a day-old cache is fine for research + costs 0 quota


def _cache_key(params: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()


class SerpApiHotelProvider:
    name = "serpapi-hotels"

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
            raise ProviderError(
                "SERPAPI_KEY is not set — cannot run a live Google Hotels search."
            )
        import serpapi  # lazy import so unit tests need no network/SDK auth

        client = serpapi.Client(api_key=self._api_key)
        self.search_count += 1
        return dict(client.search(params))

    def _cached_or_search(
        self, params: dict[str, Any], *, refresh: bool
    ) -> tuple[dict[str, Any], bool]:
        """Return (raw_response, from_cache). A fresh on-disk cache entry costs zero quota."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_dir / f"{_cache_key(params)}.json"
        if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < self._ttl_s:
            self.cache_hits += 1
            return json.loads(path.read_text()), True
        raw = self._raw_search(params)
        path.write_text(json.dumps(raw))
        return raw, False

    def _params(self, q: HotelQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": "google_hotels",
            "q": q.location,
            "check_in_date": q.check_in.isoformat(),
            "check_out_date": q.check_out.isoformat(),
            "adults": q.adults,
            "currency": "USD",
            "gl": "us",
            "hl": "en",
            "sort_by": 8,  # highest rating — the luxe-research default
        }
        if q.vacation_rentals:
            params["vacation_rentals"] = True
        if q.min_hotel_class:
            params["hotel_class"] = ",".join(str(c) for c in range(q.min_hotel_class, 6))
        if q.max_price:
            params["max_price"] = q.max_price
        if q.min_rating is not None:
            # google_hotels rating buckets: 7 = 3.5+, 8 = 4.0+, 9 = 4.5+
            params["rating"] = 9 if q.min_rating >= 4.5 else 8 if q.min_rating >= 4.0 else 7
        return params

    def search_hotels(self, query: HotelQuery, *, refresh: bool = False) -> HotelSearch:
        raw, from_cache = self._cached_or_search(self._params(query), refresh=refresh)
        offers = _parse_offers(raw, query.limit)
        return HotelSearch(
            location=query.location,
            check_in=query.check_in,
            check_out=query.check_out,
            nights=(query.check_out - query.check_in).days,
            from_cache=from_cache,
            offers=offers,
        )


def _parse_offers(raw: dict[str, Any], limit: int) -> list[HotelOffer]:
    offers: list[HotelOffer] = []
    for p in raw.get("properties") or []:
        if not isinstance(p, dict):
            continue
        rpn = p.get("rate_per_night") or {}
        tot = p.get("total_rate") or {}
        img_urls = [
            i.get("original_image") or i.get("thumbnail") or ""
            for i in (p.get("images") or [])
            if isinstance(i, dict)
        ]
        img_urls = [u for u in img_urls if u]
        nearby = []
        for np in (p.get("nearby_places") or [])[:6]:
            if not isinstance(np, dict):
                continue
            trans = np.get("transportations") or []
            t0 = trans[0] if trans and isinstance(trans[0], dict) else {}
            nearby.append(
                NearbyPlace(
                    name=np.get("name", ""),
                    transport=t0.get("type", ""),
                    duration=t0.get("duration", ""),
                )
            )
        gps = p.get("gps_coordinates") or {}
        offers.append(
            HotelOffer(
                name=p.get("name", "Unknown"),
                type=p.get("type", ""),
                hotel_class=p.get("extracted_hotel_class"),
                overall_rating=p.get("overall_rating"),
                reviews=p.get("reviews"),
                price_per_night_usd=rpn.get("extracted_lowest"),
                total_usd=tot.get("extracted_lowest"),
                description=p.get("description") or "",
                deal=p.get("deal_description") or p.get("deal") or "",
                amenities=[a for a in (p.get("amenities") or []) if isinstance(a, str)][:8],
                excluded_amenities=[
                    a for a in (p.get("excluded_amenities") or []) if isinstance(a, str)
                ],
                nearby_places=nearby,
                image_url=img_urls[0] if img_urls else "",
                # capture the FULL photo bank (the API returns at least ~9, sometimes more) — never
                # truncate here; the report curates how many to embed via photos_per.
                image_urls=img_urls,
                booking_link=p.get("link") or p.get("serpapi_property_details_link") or "",
                latitude=gps.get("latitude"),
                longitude=gps.get("longitude"),
            )
        )
        if len(offers) >= limit:
            break
    return offers


def build_serpapi_hotel_provider(api_key: str | None) -> SerpApiHotelProvider:
    return SerpApiHotelProvider(api_key=api_key)
