"""Wikimedia / Wikipedia image provider — keyless, quota-free. Best for landmarks/attractions.

Uses the MediaWiki API's `pageimages` + `generator=search` to fetch the lead image of the
best-matching article — usually the iconic representative shot for a place / landmark / hotel.
No API key required; Wikimedia policy expects a descriptive User-Agent.

This is the zero-setup image source: it costs no SerpAPI quota and needs no credentials, so it's
the default for attractions/landmarks. Specific-hotel-room photos come later from `google_hotels`
(rides free on the v1 hotel-pricing call); ambiance from Pexels/Unsplash (config-gated).
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from harness._http import get_with_retry
from harness.travel.models import ImageCandidate
from harness.travel.providers.base import ProviderError

_API = "https://en.wikipedia.org/w/api.php"
_UA = "harness/0.1 (personal travel-planning harness)"
_THUMB_PX = 1600


class WikimediaImageProvider:
    name = "wikimedia"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self._client = client
        self._timeout = timeout

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
        headers = {"User-Agent": _UA}
        try:
            resp = get_with_retry(
                _API, params=params, headers=headers,
                client=self._client, timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"wikimedia search failed for {query!r}: {e}") from e

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
