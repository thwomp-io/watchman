"""Wikimedia / Wikipedia image provider — keyless, quota-free. Best for landmarks/attractions.

Uses the MediaWiki API's `pageimages` + `generator=search` to fetch the lead image of the
best-matching article — usually the iconic representative shot for a place / landmark / hotel.
No API key required; Wikimedia policy refuses a User-Agent without contact information, so this
provider sends `_http.CONTACT_USER_AGENT`.

This is the zero-setup image source: it costs no SerpAPI quota and needs no credentials, so it's
the default for attractions/landmarks. Specific-hotel-room photos come later from `google_hotels`
(rides free on the v1 hotel-pricing call); ambiance from Pexels/Unsplash (config-gated).
"""

from __future__ import annotations

import time
from typing import Any, cast

import httpx

from harness._http import CONTACT_USER_AGENT, describe_http_error, get_with_retry
from harness.travel.models import ImageCandidate
from harness.travel.providers.base import ProviderError

_API = "https://en.wikipedia.org/w/api.php"
_THUMB_PX = 1600
# Wikimedia's robot policy for the unauthenticated Action API: one request at a time, and wait 5
# seconds after any request that took more than 1 second to serve (a slow answer means load).
_SLOW_S = 1.0
_BACKOFF_S = 5.0


class WikimediaImageProvider:
    name = "wikimedia"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self._client = client
        self._timeout = timeout
        self._not_before = 0.0  # monotonic ts the next request must wait for (set after a slow one)

    def search_images(self, query: str, limit: int = 1) -> list[ImageCandidate]:
        params: dict[str, str | int] = {
            "action": "query",
            "format": "json",
            "prop": "pageimages|info",
            "piprop": "thumbnail",
            "pithumbsize": _THUMB_PX,
            "inprop": "url",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": max(1, limit),
            "gsrnamespace": 0,
            "redirects": 1,
        }
        wait = self._not_before - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        start = time.monotonic()
        try:
            resp = get_with_retry(
                _API, params=params, headers={"User-Agent": CONTACT_USER_AGENT},
                client=self._client, timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"wikimedia search failed for {query!r}: {describe_http_error(e)}") from e
        finally:
            if time.monotonic() - start > _SLOW_S:
                self._not_before = time.monotonic() + _BACKOFF_S

        payload = cast(dict[str, Any], resp.json())
        pages = payload.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            return []

        # generator=search returns an `index` for ranking; sort by it, keep ones with a thumbnail.
        ranked = sorted(pages.values(), key=lambda p: p.get("index", 1_000))
        out: list[ImageCandidate] = []
        for page in ranked:
            thumb = page.get("thumbnail")
            if not isinstance(thumb, dict) or not thumb.get("source"):
                continue
            title = str(page.get("title", query))
            out.append(
                ImageCandidate(
                    subject=query,
                    title=title,
                    image_url=str(thumb["source"]),
                    source=self.name,
                    source_url=str(page.get("fullurl", "")),
                    attribution=f"via Wikipedia: {title}",
                    width=thumb.get("width"),
                    height=thumb.get("height"),
                )
            )
            if len(out) >= limit:
                break
        return out
