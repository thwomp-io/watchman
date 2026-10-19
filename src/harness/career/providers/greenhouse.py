"""Greenhouse Job Board API provider — keyless public JSON.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Salary is NOT a structured field on this API; pay-transparency ranges live in the HTML `content`
(WA/CA/CO/NY postings) — extracted by regex, surfaced as the raw matched text (honest, unnormalized).
"""

from __future__ import annotations

import html
import json
import re

import httpx

from harness._http import describe_http_error, get_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
_JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
_DEGREES_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/education/degrees"


_SCHOOLS_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/education/schools"


def fetch_greenhouse_schools(token: str, term: str) -> list[str]:
    """Search the board's school list (keyless, `?term=`) — the names the form's School typeahead
    accepts, spelled as Greenhouse lists them (a school's old name may be the listed one).
    Used at AUTHORING time (`hn career schools`) so `answers.yaml` carries a name the fill can
    match exactly; the fill itself never guesses a school."""
    try:
        resp = get_with_retry(_SCHOOLS_URL.format(token=token), params={"term": term})
    except httpx.HTTPError as e:
        raise ProviderError(f"greenhouse schools {token!r}: {describe_http_error(e)}") from e
    try:
        items = resp.json().get("items") or []
    except (json.JSONDecodeError, AttributeError) as e:
        raise ProviderError(f"greenhouse schools {token!r}: bad JSON") from e
    return [str(i.get("text")) for i in items if isinstance(i, dict) and i.get("text")]


def fetch_greenhouse_degrees(token: str) -> list[str]:
    """The board's degree picklist (keyless; ~12 items) for the form's Education block. The
    school list is paginated and huge — it stays a typeahead the browser fill drives. A failure
    here degrades to an empty list (the Degree field then reads as a free lookup), never an
    aborted capture."""
    try:
        resp = get_with_retry(_DEGREES_URL.format(token=token))
        items = resp.json().get("items") or []
    except (httpx.HTTPError, json.JSONDecodeError, AttributeError):
        return []
    return [str(i.get("text")) for i in items if isinstance(i, dict) and i.get("text")]


def fetch_greenhouse_posting(token: str, job_id: str) -> dict:  # type: ignore[type-arg]
    """One posting WITH its application form (`?questions=true`) — keyless. Returns the raw JSON
    (title/location/content/questions/location_questions/compliance/data_compliance …) for
    `harness.career.posting.normalize_greenhouse`.

    Capture is the read side; the same path's POST is the board owner's (Basic auth with the Job
    Board API key — a candidate gets 401, verified live 2026-09), which is why submission never rides
    this provider."""
    try:
        resp = get_with_retry(
            _JOB_URL.format(token=token, job_id=job_id), params={"questions": "true"}
        )
    except httpx.HTTPError as e:
        hint = "no such posting on that board — check the id, or the posting closed"
        raise ProviderError(
            f"greenhouse {token}/{job_id}: {describe_http_error(e, hint_404=hint)}"
        ) from e
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"greenhouse {token}/{job_id}: bad JSON") from e
    if not isinstance(data, dict) or "id" not in data:
        raise ProviderError(f"greenhouse {token}/{job_id}: unexpected payload")
    return data

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
    try:
        resp = get_with_retry(_BOARD_URL.format(token=token), params={"content": "true"})
    except httpx.HTTPError as e:
        # One dead board must stay one loud row, never abort the scan (a token 404s when the company
        # moves ATS).
        hint = "no such board token — the company may have moved ATS; probe ashby"
        raise ProviderError(
            f"greenhouse board {token!r}: {describe_http_error(e, hint_404=hint)}"
        ) from e
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
