"""FastMCP adapter for the career lane (mounted under the root server as `career_*`).

One read-only tool for v1: `openings` — the agent-native twin of `hn career openings`. Returns the
markdown report (the same renderer as `--write`), so the agent can reason over titles/comp inline.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.career.cli import get_settings
from harness.career.config import role_hunt_root
from harness.career.service import CareerService

mcp = FastMCP("harness-career")


@mcp.tool()
def openings(company: str | None = None, grep: str | None = None, unfiltered: bool = False) -> str:
    """Scan the role-hunt watchlist's public ATS boards (keyless, read-only) for matching job
    openings. `company` narrows by name substring; `grep` adds a title-contains filter;
    `unfiltered` bypasses the watchlist keyword filters. Salary is as-posted text."""
    svc = CareerService(
        get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings())
    )
    scans = svc.scan(
        companies=[company] if company else None, grep=grep, unfiltered=unfiltered
    )
    return CareerService.to_markdown(scans, "Openings scan (live)")
