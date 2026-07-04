"""Applications-pipeline corpus loader — `corpus/role-hunt/applications.yaml` is the source of truth.

The application/opportunity pipeline as domain state (hand-edited corpus YAML — the tool
reads it, never writes it). The CAREER dashboard's PIPELINE table reads this
via `hn career applications --json`. Read-only here; advancing a stage is the user's edit, never the
tool's act (the read-rich / execute-gated doctrine — applying/contacting is the user's act).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from harness.errors import ProviderError

# Pipeline stages, ordered earliest → latest (display/sort hint; not enforced).
STAGES = (
    "inbound",
    "researching",
    "drafting",
    "applied",
    "screening",
    "interviewing",
    "offer",
    "closed",
)


class Application(BaseModel):
    """One opportunity/application in the pipeline. `stage` should be one of STAGES (free-text
    tolerated — the corpus is human-edited)."""

    company: str
    role: str = ""
    stage: str = "inbound"
    source: str = ""  # how it arrived (recruiter / referral / direct apply / inbound)
    next_step: str = ""
    location: str = ""
    comp_signal: str = ""  # directional, recruiter-framed until employer-confirmed
    updated: str = ""  # YYYY-MM-DD
    url: str = ""
    ref: str = ""  # tracker-relative path to the inbound/research note, for deep-linking
    notes: str = ""

    @field_validator("updated", mode="before")
    @classmethod
    def _stringify_date(cls, v: object) -> object:
        """YAML auto-parses an unquoted `2024-01-15` into a date — tolerate it (the corpus is
        hand-edited; humans write bare dates) by coercing to an isoformat string."""
        if isinstance(v, (date, datetime)):
            return v.isoformat()[:10]
        return v


class Pipeline(BaseModel):
    applications: list[Application] = []


def load_applications(tracker_path: Path, *, root: Path | None = None) -> Pipeline:
    """Load the pipeline corpus. A MISSING file is not an error — it's an empty pipeline (the
    dashboard renders an empty table until the first opportunity lands).

    `root` (the role-hunt corpus root) wins when given — the pack-resolved dir (a loaded pack's
    `career/` IS the role-hunt root). Default keeps the legacy `<tracker>/role-hunt/` join.
    """
    path = (root if root is not None else tracker_path / "role-hunt") / "applications.yaml"
    if not path.exists():
        return Pipeline()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ProviderError(f"applications YAML parse failed: {e}") from e
    return Pipeline.model_validate(raw)
