"""Career-lane models: job openings scanned from public ATS board APIs (read-only)."""

from __future__ import annotations

from pydantic import BaseModel


class Opening(BaseModel):
    """One live job posting, normalized across ATS providers."""

    company: str
    title: str
    url: str
    location: str = ""
    remote: bool | None = None  # only Ashby states this structurally; None = unknown
    salary: str | None = None  # raw posted range text (pay-transparency states) — not normalized
    department: str = ""
    updated: str = ""  # provider's updated/published stamp, as given
    source: str  # greenhouse | ashby


class CompanyScan(BaseModel):
    """Scan result for one watchlist company — keeps partial failures loud, not swallowed."""

    company: str
    ats: str
    matched: list[Opening] = []
    total_open: int = 0  # board-wide posting count before title filters
    error: str | None = None
    skipped: str | None = None  # ats=none companies: why there's nothing to query
