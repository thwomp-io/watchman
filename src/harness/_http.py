"""Shared HTTP GET with exponential backoff on 429/5xx + a descriptive, non-PII User-Agent.

Proportionate hardening for a low-volume *personal* tool: serial, interactive, a handful of calls
per turn — NOT a rate limiter (a token bucket would be over-engineering for this load). One place
for the backoff policy + the UA, so every domain's call path behaves identically.

The UA is deliberately *tool*-identifying only (`harness/<version>`): descriptive enough to satisfy
provider policy + be a good API citizen (some providers — Wikimedia is the lesson — throttle/block
requests lacking a proper UA), and carrying **zero PII** (no name/email) by deliberate choice for a
personal tool — the UA identifies the software, never the person running it.
"""

from __future__ import annotations

import time

import httpx

from harness import __version__

USER_AGENT = f"harness/{__version__}"

_RETRY_STATUS = {429, 500, 502, 503, 504}


def get_with_retry(
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    base_delay: float = 1.0,
    follow_redirects: bool = False,
    allow_status: set[int] | None = None,
) -> httpx.Response:
    """GET `url`, retrying on 429/5xx with exponential backoff (base_delay * 2**attempt).

    A non-PII `User-Agent` is always set (caller headers win on collision otherwise). A passed
    `client` carries its own redirect/timeout config; the clientless path honors `follow_redirects`
    (the image-download path needs it).

    `allow_status` is a set of status codes the caller wants returned (not raised, not retried) so it
    can inspect them — e.g. EDGAR returns 404 when a filer simply doesn't report a concept tag, an
    expected outcome in a fallback loop rather than an error.
    """
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            if client is not None:
                resp = client.get(url, params=params, headers=merged)
            else:
                resp = httpx.get(
                    url, params=params, headers=merged,
                    timeout=timeout, follow_redirects=follow_redirects,
                )
            if allow_status and resp.status_code in allow_status:
                return resp
            if resp.status_code in _RETRY_STATUS:
                last = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                time.sleep(base_delay * 2**attempt)
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as e:
            last = e
            time.sleep(base_delay * 2**attempt)
    raise httpx.HTTPError(f"request to {url} failed after {retries} attempts: {last}")


def post_with_retry(
    url: str,
    *,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    base_delay: float = 1.0,
) -> httpx.Response:
    """POST `url` with a JSON body, same retry/UA semantics as `get_with_retry` (Workday CXS et al.)."""
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(url, json=json_body, headers=merged, timeout=timeout)
            if resp.status_code in _RETRY_STATUS:
                last = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                time.sleep(base_delay * 2**attempt)
                continue
            return resp
        except httpx.HTTPError as e:
            last = e
            time.sleep(base_delay * 2**attempt)
    raise httpx.HTTPError(f"POST to {url} failed after {retries} attempts: {last}")
