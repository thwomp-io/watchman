"""Workday CXS provider — the keyless JSON endpoint behind every Workday career portal.

POST https://{host}/wday/cxs/{tenant}/{site}/jobs {"limit","offset","searchText","appliedFacets"}
→ {"total": N, "jobPostings": [{title, externalPath, locationsText, postedOn}]}.

Boards are huge (large tenants carry thousands of postings), so we search PER DOMAIN-KEYWORD
(the watchlist's title_any terms) instead of paginating the world — honest tradeoff: `total_open`
reports the DEDUPED SEARCH-HIT count, not the board-wide census. Verified against a representative
Workday tenant ('reliability' returns clean JSON).
"""

from __future__ import annotations

import json

from harness._http import post_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_JOBS_URL = "https://{host}/wday/cxs/{tenant}/{site}/jobs"
_PAGE = 20  # Workday CXS rejects limit>20 with HTTP 400 (found live against a real tenant)
_MAX_PAGES_PER_TERM = 5  # 100 hits per term is plenty for matching; politeness over completeness


def fetch_workday(
    company: str, host: str, tenant: str, site: str, terms: list[str]
) -> list[Opening]:
    url = _JOBS_URL.format(host=host, tenant=tenant, site=site)
    seen: dict[str, Opening] = {}
    for term in terms or [""]:
        for page in range(_MAX_PAGES_PER_TERM):
            payload = {
                "limit": _PAGE,
                "offset": page * _PAGE,
                "searchText": term,
                "appliedFacets": {},
            }
            resp = post_with_retry(url, json_body=payload)
            if resp.status_code != 200:
                raise ProviderError(f"workday {tenant}/{site}: HTTP {resp.status_code}")
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                raise ProviderError(f"workday {tenant}/{site}: bad JSON") from e
            postings = data.get("jobPostings") or []
            for p in postings:
                path = p.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen[path] = Opening(
                    company=company,
                    title=p.get("title", ""),
                    url=f"https://{host}{site and '/' + site or ''}{path}",
                    location=p.get("locationsText", ""),
                    updated=p.get("postedOn", ""),
                    source="workday",
                )
            if len(postings) < _PAGE:
                break
    return list(seen.values())
