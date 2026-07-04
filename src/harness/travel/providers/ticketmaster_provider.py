"""Ticketmaster Discovery API event provider — the dynamic-characteristics layer.

Given a city + date window, returns what's *on* (games / concerts / festivals / theatre) so the
harness can surface live events as trip signals. The provider returns RAW, classified events;
the configured sports-interest tiering (the corpus sets which leagues are centerpiece vs perk) is
applied downstream by EventWeights.tier_for — keeping this provider a generic Ticketmaster source.

Free Discovery tier (~5k calls/day). Uses the app's Consumer Key as the `apikey` param.
https://developer.ticketmaster.com/  (My Apps → default app → Consumer Key)
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, cast

import httpx

from harness._http import get_with_retry
from harness.travel.models import EventResult
from harness.travel.providers.base import ProviderError

_API = "https://app.ticketmaster.com/discovery/v2/events.json"
_UA = "harness/0.1 (personal travel-planning harness)"
_PAGE_SIZE = 200  # TM max per page
_MAX_PAGES = 5  # TM deep-paging cap is size*page <= 1000 → 5 pages of 200 covers any trip window


def _clean(name: object) -> str:
    """TM fills unclassified slots with the literal 'Undefined'; normalize those to ''."""
    s = str(name or "").strip()
    return "" if s == "Undefined" else s


class TicketmasterEventProvider:
    name = "ticketmaster"
    # Proactive pacing: TM's free tier caps at 5 req/sec. A multi-city scan paginates per city
    # (up to _MAX_PAGES per city × N cities), and scan_events reuses ONE provider instance across
    # all cities, so without pacing a broad scan bursts past the cap → 429s. get_with_retry backs
    # off reactively, but scan_events then swallows an exhausted city to empty → false-empty cities.
    # ~0.25s between requests (~4/sec) keeps us under the cap without manual batching.
    _MIN_INTERVAL = 0.25

    def __init__(
        self, api_key: str | None, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout
        self._last_req = 0.0  # monotonic ts of the last request; 0.0 = none yet (no first-call delay)

    def _throttle(self) -> None:
        """Sleep the remainder of ``_MIN_INTERVAL`` since the last request, so paginated multi-city
        scans self-pace under TM's 5 req/sec cap. Instance-level (scan_events reuses the instance),
        so it paces both inter-page and inter-city requests."""
        if self._last_req:
            wait = self._MIN_INTERVAL - (time.monotonic() - self._last_req)
            if wait > 0:
                time.sleep(wait)
        self._last_req = time.monotonic()

    def _params(
        self,
        city: str,
        start_date: date,
        end_date: date,
        classification: str | None,
        size: int,
        page: int = 0,
    ) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "apikey": self._api_key or "",
            "city": city,
            # Discovery expects ISO8601 UTC with a trailing Z.
            "startDateTime": f"{start_date.isoformat()}T00:00:00Z",
            "endDateTime": f"{end_date.isoformat()}T23:59:59Z",
            "size": size,
            "page": page,
            "sort": "date,asc",
        }
        if classification:
            params["classificationName"] = classification
        return params

    def _raw_search(self, params: dict[str, str | int]) -> dict[str, Any]:
        self._throttle()
        try:
            resp = get_with_retry(
                _API, params=params, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"ticketmaster search failed: {e}") from e
        return cast(dict[str, Any], resp.json())

    def search_events(
        self,
        city: str,
        start_date: date,
        end_date: date,
        *,
        classification: str | None = None,
        size: int = _PAGE_SIZE,
    ) -> list[EventResult]:
        if not self._api_key:
            raise ProviderError(
                "TICKETMASTER_KEY is not set — cannot search events. Add it to .env "
                "(Consumer Key from developer.ticketmaster.com)."
            )
        # Discovery's date filter is UTC; users think in local trip dates. End-pad the UTC query +1
        # day so a late-last-day local event (e.g. 6/22 PM local = 6/23 UTC) is still caught, then
        # local-date-filter below. Do NOT start-pad: it only pulls evening-before-local events we'd
        # filter out anyway, AND with sort=date,asc it lets a busy city's pad-day consume the whole
        # first page → the false-zero bug. Paginate (sort=date,asc) so busy cities aren't truncated.
        api_end = end_date + timedelta(days=1)
        raw: list[dict[str, Any]] = []
        for page in range(_MAX_PAGES):
            payload = self._raw_search(
                self._params(city, start_date, api_end, classification, size, page)
            )
            events = payload.get("_embedded", {}).get("events", [])
            if not isinstance(events, list) or not events:
                break  # zero/last results → Discovery omits _embedded or returns an empty list
            raw.extend(e for e in events if isinstance(e, dict))
            total_pages = (payload.get("page") or {}).get("totalPages", 1)
            if page + 1 >= total_pages:
                break
        parsed = [self._parse_event(e) for e in raw]
        lo, hi = start_date.isoformat(), end_date.isoformat()
        # Keep in-window local dates; keep undated events (rare) rather than silently dropping them.
        return [ev for ev in parsed if not ev.local_date or lo <= ev.local_date <= hi]

    @staticmethod
    def _parse_event(e: dict[str, Any]) -> EventResult:
        start = (e.get("dates") or {}).get("start") or {}
        classifications = e.get("classifications") or []
        cls0 = (
            classifications[0]
            if classifications and isinstance(classifications[0], dict)
            else {}
        )
        venues = (e.get("_embedded") or {}).get("venues") or []
        venue0 = venues[0] if venues and isinstance(venues[0], dict) else {}
        return EventResult(
            name=str(e.get("name", "")),
            segment=_clean((cls0.get("segment") or {}).get("name")),
            genre=_clean((cls0.get("genre") or {}).get("name")),
            subgenre=_clean((cls0.get("subGenre") or {}).get("name")),
            local_date=str(start.get("localDate", "")),
            local_time=str(start["localTime"]) if start.get("localTime") else None,
            venue=str(venue0.get("name", "")),
            city=str((venue0.get("city") or {}).get("name", "")),
            url=str(e.get("url", "")),
        )


def build_ticketmaster_provider(api_key: str | None) -> TicketmasterEventProvider:
    return TicketmasterEventProvider(api_key=api_key)
