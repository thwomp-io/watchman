"""Eightfold provider — the JSON API behind Eightfold-hosted career portals.

GET https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=50&query={term}
→ {"count": N, "positions": [{name, location, t_update, canonicalPositionUrl, department}]}.

Same search-per-term strategy as Workday (boards are large; the watchlist's domain keywords ARE
the search terms). Verified against a representative Eightfold tenant ('reliability' returns
remote SRE roles).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness._http import get_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_JOBS_URL = "https://{host}/api/apply/v2/jobs"
_NUM = 50


def fetch_eightfold(company: str, host: str, domain: str, terms: list[str]) -> list[Opening]:
    seen: dict[str, Opening] = {}
    for term in terms or [""]:
        resp = get_with_retry(
            _JOBS_URL.format(host=host),
            params={"domain": domain, "start": 0, "num": _NUM, "query": term},
        )
        if resp.status_code != 200:
            raise ProviderError(f"eightfold {host}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ProviderError(f"eightfold {host}: bad JSON") from e
        for p in data.get("positions") or []:
            url = p.get("canonicalPositionUrl") or ""
            if not url or url in seen:
                continue
            ts = p.get("t_update")
            updated = (
                datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
                if isinstance(ts, (int, float))
                else ""
            )
            seen[url] = Opening(
                company=company,
                title=p.get("name", ""),
                url=url,
                location=p.get("location", ""),
                department=p.get("department", "") or "",
                updated=updated,
                source="eightfold",
            )
    return list(seen.values())
