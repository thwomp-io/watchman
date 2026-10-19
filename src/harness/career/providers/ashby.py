"""Ashby posting API provider — keyless public JSON.

GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
Compensation is structured here (`compensation.scrapeableCompensationSalarySummary`, e.g.
"$257K - $335K") when the org publishes it; `isRemote`/`workplaceType` are also structural.
"""

from __future__ import annotations

import json

import httpx

from harness._http import describe_http_error, get_with_retry, post_with_retry
from harness.career.models import Opening
from harness.errors import ProviderError

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"
# The hosted job page's own GraphQL door (what a browser calls to render the application form).
# The documented `posting-api/application-form/{id}` needs the ORG's API key (401 for a candidate,
# verified live 2026-09); this one is keyless and returns the same schema. Read-only use only: the
# page's submit mutation is undocumented and introspection is disabled — never submit through it.
_GQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"
_FORM_QUERY = (
    "query ApiJobPostingApplicationForm($organizationHostedJobsPageName: String!, "
    "$jobPostingId: String!) { jobPosting(organizationHostedJobsPageName: "
    "$organizationHostedJobsPageName, jobPostingId: $jobPostingId) { id title applicationForm "
    "{ sections { title fieldEntries { isRequired field } } } } }"
)


def fetch_ashby_posting(token: str, job_id: str) -> tuple[dict, dict]:  # type: ignore[type-arg]
    """One posting (from the board list — title/location/comp/description) + its application form
    (from the hosted page's GraphQL). Returns `(job, form)` raw for
    `harness.career.posting.normalize_ashby`; `form` is the `applicationForm` node."""
    try:
        resp = get_with_retry(
            _BOARD_URL.format(token=token), params={"includeCompensation": "true"}
        )
    except httpx.HTTPError as e:
        raise ProviderError(
            f"ashby board {token!r}: "
            f"{describe_http_error(e, hint_404='no such job board — check the org slug')}"
        ) from e
    try:
        jobs = resp.json().get("jobs", [])
    except json.JSONDecodeError as e:
        raise ProviderError(f"ashby board {token!r}: bad JSON") from e
    job = next((j for j in jobs if j.get("id") == job_id), None)
    if job is None:
        raise ProviderError(
            f"ashby {token}/{job_id}: not on the public board (closed, unlisted, or a bad id)"
        )
    try:
        gql = post_with_retry(
            f"{_GQL_URL}?op=ApiJobPostingApplicationForm",
            json_body={
                "operationName": "ApiJobPostingApplicationForm",
                "variables": {"organizationHostedJobsPageName": token, "jobPostingId": job_id},
                "query": _FORM_QUERY,
            },
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"ashby form {token}/{job_id}: {describe_http_error(e)}") from e
    if gql.status_code != 200:
        raise ProviderError(f"ashby form {token}/{job_id}: HTTP {gql.status_code}")
    try:
        body = gql.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"ashby form {token}/{job_id}: bad JSON") from e
    if body.get("errors"):
        msg = body["errors"][0].get("message", "graphql error")
        raise ProviderError(f"ashby form {token}/{job_id}: {msg}")
    form = ((body.get("data") or {}).get("jobPosting") or {}).get("applicationForm") or {}
    if not form.get("sections"):
        raise ProviderError(f"ashby form {token}/{job_id}: no application form returned")
    return job, form


def fetch_ashby(company: str, token: str) -> list[Opening]:
    try:
        resp = get_with_retry(
            _BOARD_URL.format(token=token), params={"includeCompensation": "true"}
        )
    except httpx.HTTPError as e:
        raise ProviderError(
            f"ashby board {token!r}: "
            f"{describe_http_error(e, hint_404='no such job board — check the org slug')}"
        ) from e
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
