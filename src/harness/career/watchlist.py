"""Watchlist corpus loader — `corpus/role-hunt/watchlist.yml` is the source of truth.

Corpus-as-moat: the human-curated watchlist (companies + ATS board tokens + title/seniority filter
keywords) lives in the vault; the toolkit reads it. Machine-readable YAML kept in manual sync with
the prose docs (`target-map.md`), per the corpus conventions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from harness.errors import ProviderError


class WatchCompany(BaseModel):
    name: str
    ats: Literal["greenhouse", "ashby", "workday", "eightfold", "none"] = "none"
    token: str = ""  # ATS board token (probe-verified before committing)
    # workday: {host, tenant, site} · eightfold: {host, domain} — per-tenant config
    workday: dict[str, str] = {}
    eightfold: dict[str, str] = {}
    tier: str = ""  # target-map tier label, for grouping in output
    portal: str = ""  # ats=none: the careers URL for manual checks
    note: str = ""


class WatchFilters(BaseModel):
    """Default matching: an opening matches if ANY title keyword hits the title-or-department, AND
    ANY seniority keyword hits the title, AND NO exclusion keyword hits either. `title_none` is
    noise control (non-engineering functions riding domain words like "infrastructure"), NOT
    role-shape narrowing — the breadth directive stands. `--all`/`--grep` bypass/extend."""

    title_any: list[str] = []
    seniority_any: list[str] = []
    title_none: list[str] = []


class Watchlist(BaseModel):
    companies: list[WatchCompany] = []
    filters: WatchFilters = WatchFilters()
    # Shortlist tier ordering, most-actionable first. Optional: when absent, tiers rank by first
    # appearance in `companies` (the watchlist is already curated top-down). Tiers not listed sort
    # last but are kept — never dropped.
    tier_order: list[str] = []


def load_watchlist(tracker_path: Path, *, root: Path | None = None) -> Watchlist:
    # `root` (the role-hunt corpus root) wins when given — it's the pack-resolved dir (a loaded
    # pack's `career/` IS the role-hunt root, dropping the infix). Default keeps the legacy
    # `<tracker>/role-hunt/` join, so direct callers are unchanged when no pack is loaded.
    path = (root if root is not None else tracker_path / "role-hunt") / "watchlist.yml"
    if not path.exists():
        raise ProviderError(f"watchlist not found at {path} — seed role-hunt/watchlist.yml first")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ProviderError(f"watchlist YAML parse failed: {e}") from e
    return Watchlist.model_validate(raw)
