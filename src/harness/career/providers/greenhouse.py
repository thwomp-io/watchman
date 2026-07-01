"""Greenhouse Job Board API provider — keyless public JSON.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Salary is NOT a structured field on this API; pay-transparency ranges live in the HTML `content`
(WA/CA/CO/NY postings) — extracted by regex, surfaced as the raw matched text (honest, unnormalized).
"""

from __future__ import annotations

import html
import json
import re

from harness._http import get_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

# "$185,000 - $260,000" / "$185,000—$260,000 USD" / "$240K - $320K" — first range wins.
_SALARY_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})+(?:\s*[-–—]+\s*\$?\s?\d{2,3}(?:,\d{3})+)"
    r"|\$\s?\d{2,3}(?:\.\d)?K\s*[-–—]+\s*\$?\s?\d{2,3}(?:\.\d)?K",
    re.IGNORECASE,
)


def extract_salary(content_html: str) -> str | None:
    """First posted salary *range* in the (HTML-escaped) job content, as raw text."""
    if not content_html:
        return None
    m = _SALARY_RE.search(html.unescape(content_html))
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def fetch_greenhouse(company: str, token: str) -> list[Opening]:
    resp = get_with_retry(_BOARD_URL.format(token=token), params={"content": "true"})
    if resp.status_code != 200:
        raise ProviderError(f"greenhouse board {token!r}: HTTP {resp.status_code}")
    try:
        jobs = resp.json().get("jobs", [])
    except json.JSONDecodeError as e:
        raise ProviderError(f"greenhouse board {token!r}: bad JSON") from e
    out: list[Opening] = []
    for j in jobs:
        out.append(
            Opening(
                company=company,
                title=j.get("title", ""),
                url=j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                salary=extract_salary(j.get("content", "")),
                department=", ".join(d.get("name", "") for d in j.get("departments", []) or []),
                updated=(j.get("updated_at") or "")[:10],
                source="greenhouse",
            )
        )
    return out
