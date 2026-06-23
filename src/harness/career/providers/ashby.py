"""Ashby posting API provider — keyless public JSON.

GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
Compensation is structured here (`compensation.scrapeableCompensationSalarySummary`, e.g.
"$257K - $335K") when the org publishes it; `isRemote`/`workplaceType` are also structural.
"""

from __future__ import annotations

import json

from harness._http import get_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch_ashby(company: str, token: str) -> list[Opening]:
    resp = get_with_retry(_BOARD_URL.format(token=token), params={"includeCompensation": "true"})
    if resp.status_code != 200:
        raise ProviderError(f"ashby board {token!r}: HTTP {resp.status_code}")
    try:
        jobs = resp.json().get("jobs", [])
    except json.JSONDecodeError as e:
        raise ProviderError(f"ashby board {token!r}: bad JSON") from e
    out: list[Opening] = []
    for j in jobs:
        comp = j.get("compensation") or {}
        out.append(
            Opening(
                company=company,
                title=j.get("title", ""),
                url=j.get("jobUrl") or j.get("applyUrl", ""),
                location=j.get("location", ""),
                remote=j.get("isRemote"),
                salary=comp.get("scrapeableCompensationSalarySummary") or None,
                department=j.get("department", ""),
                updated=(j.get("publishedAt") or "")[:10],
                source="ashby",
            )
        )
    return out
