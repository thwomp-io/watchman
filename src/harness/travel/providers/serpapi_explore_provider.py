"""SerpAPI / Google Travel Explore provider: priced destination discovery.

Google Flights' Explore map as data. From one origin, ONE search returns a list of destinations with
the cheapest round-trip fare to each, for exact dates or a flexible month + trip length. It answers
"where can we go, and what does it cost" before a destination is picked; `research_flights` is the
deep read after the pick.

Quota: 1 search per origin. A 24h on-disk cache (same shape as hotels) makes re-views free.
The avoid list rides the request as `exclude_airlines`, so a cheapest fare is never an avoided
carrier's. Mirrors the hotels provider's `_raw_search` seam so tests never spend quota.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from harness.paths import cache_file
from harness.travel.models import ExploreDestination, ExploreFare, ExploreQuery
from harness.travel.providers.base import ProviderError

_CACHE_SUBDIR = "explore"
_CACHE_TTL_S = 24 * 3600

_CLASS = {"economy": 1, "premium": 2, "business": 3, "first": 4}
_STOPS = {"any": 0, "nonstop": 1, "1": 2, "2": 3}
_DURATION = {"weekend": 1, "week": 2, "two-weeks": 3}
# Region → Google's knowledge-graph id for `arrival_area_id`. Each verified live (a single-origin
# search returned only that region's places). The documented `interest` filter is NOT offered:
# the same search with and without it returned the identical 87 destinations in the same order.
REGIONS = {
    "caribbean": "/m/0261m", "mexico": "/m/0b90_r", "hawaii": "/m/03gh4",
    "central-america": "/m/01tzh", "south-america": "/m/06n3y", "europe": "/m/02j9z",
    "asia": "/m/0j0k", "japan": "/m/03_3d", "canada": "/m/0d060g",
}


def _cache_key(params: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()


def explore_params(q: ExploreQuery) -> dict[str, Any]:
    """Pure: the query → SerpAPI params. Raises ValueError on an unknown option (named)."""
    for label, value, table in (("class", q.travel_class, _CLASS), ("stops", q.stops, _STOPS),
                                ("duration", q.duration, _DURATION)):
        if value not in table:
            raise ValueError(f"unknown {label} {value!r} (one of: {', '.join(table)})")
    to = (q.to or "").strip()
    params: dict[str, Any] = {
        "engine": "google_travel_explore",
        "departure_id": q.origin.upper(),
        "travel_class": _CLASS[q.travel_class],
        "stops": _STOPS[q.stops],
        "travel_mode": 1,  # flights only (0 would mix in drive/train destinations)
        "adults": 1,  # per-person fares, comparable with research_flights
        "currency": "USD",
        "gl": "us",
        "hl": "en",
    }
    if q.outbound_date:
        params["outbound_date"] = q.outbound_date.isoformat()
        if q.return_date:
            params["return_date"] = q.return_date.isoformat()
        else:
            params["type"] = 2  # one way
    else:
        params["month"] = q.month
        params["travel_duration"] = _DURATION[q.duration]
    if q.max_price:
        params["max_price"] = q.max_price
    if to:
        if to.lower() in REGIONS:
            params["arrival_area_id"] = REGIONS[to.lower()]
        elif to.startswith(("/m/", "/g/")):
            params["arrival_area_id"] = to  # any Google region id
        elif re.fullmatch(r"[A-Za-z]{3}", to):
            params["arrival_id"] = to.upper()  # one airport: its fares across the dates
        else:
            raise ValueError(f"unknown --to {to!r}: a region ({', '.join(REGIONS)}), "
                             "a Google id (/m/…), or an airport code")
    if q.exclude_airlines:
        params["exclude_airlines"] = ",".join(sorted(c.upper() for c in q.exclude_airlines))
    return params


class SerpApiExploreProvider:
    name = "serpapi-explore"

    def __init__(
        self, api_key: str | None, *, cache_dir: Path | None = None, ttl_s: int = _CACHE_TTL_S
    ) -> None:
        self._api_key = api_key
        self._cache_dir = cache_dir or cache_file(_CACHE_SUBDIR)
        self._ttl_s = ttl_s
        self.search_count = 0
        self.cache_hits = 0

    def _raw_search(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError("SERPAPI_KEY is not set — cannot run a live Travel Explore search.")
        import serpapi

        client = serpapi.Client(api_key=self._api_key)
        self.search_count += 1
        return dict(client.search(params))

    def explore(self, query: ExploreQuery, *, refresh: bool = False) -> list[ExploreDestination]:
        params = explore_params(query)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_dir / f"{_cache_key(params)}.json"
        if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < self._ttl_s:
            self.cache_hits += 1
            raw = json.loads(path.read_text())
        else:
            raw = self._raw_search(params)
            if raw.get("error"):
                raise ProviderError(f"Travel Explore from {query.origin}: {raw['error']}")
            path.write_text(json.dumps(raw))
        return parse_destinations(raw, query.origin.upper())


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def parse_destinations(raw: dict[str, Any], origin: str) -> list[ExploreDestination]:
    """Pure: a Travel Explore reply → one ExploreDestination (with one fare) per row."""
    out: list[ExploreDestination] = []
    for d in raw.get("destinations") or []:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        gps = d.get("gps_coordinates") or {}
        airport = d.get("destination_airport") or {}
        out.append(ExploreDestination(
            destination_id=str(d.get("destination_id") or ""),
            name=str(d["name"]),
            country=str(d.get("country") or ""),
            latitude=_num(gps.get("latitude")),
            longitude=_num(gps.get("longitude")),
            thumbnail=str(d.get("thumbnail") or ""),
            fares=[ExploreFare(
                origin=origin,
                price_usd=_num(d.get("flight_price")),
                start_date=str(d.get("start_date") or ""),
                end_date=str(d.get("end_date") or ""),
                airport=str(airport.get("code") or "") if isinstance(airport, dict) else "",
                stops=_int(d.get("number_of_stops")),
                flight_minutes=_int(d.get("flight_duration")),
                airline=str(d.get("airline") or ""),
                airline_code=str(d.get("airline_code") or ""),
                link=str(d.get("link") or ""),
                hotel_price_usd=_num(d.get("hotel_price")),
            )],
        ))
    return out


def build_serpapi_explore_provider(api_key: str | None) -> SerpApiExploreProvider:
    return SerpApiExploreProvider(api_key=api_key)


_ACCOUNT = "https://serpapi.com/account.json"
# Only the budget fields leave this function: the reply also carries the API key and the email.
QUOTA_FIELDS = ("plan_name", "searches_per_month", "plan_searches_left", "extra_credits",
                "total_searches_left", "this_month_usage", "this_hour_searches",
                "plan_renewal_date", "account_rate_limit_per_hour")


def account_quota(api_key: str | None) -> dict[str, Any]:
    """The SerpAPI budget from the Account API (free: it does not count against the quota)."""
    import httpx

    if not api_key:
        raise ProviderError("SERPAPI_KEY is not set — cannot read the SerpAPI account.")
    try:
        resp = httpx.get(_ACCOUNT, params={"api_key": api_key}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        # the key rides the URL: name the failure without echoing the request
        raise ProviderError(f"SerpAPI account read failed: {type(e).__name__}") from None
    return {k: data.get(k) for k in QUOTA_FIELDS}
