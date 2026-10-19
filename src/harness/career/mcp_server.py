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


@mcp.tool()
def comp(company: str, family: str = "software-engineer", location: str = "united-states",
         refresh: bool = False) -> str:
    """A company's engineering pay ladder from levels.fyi (keyless markdown route, geo-pinned,
    cached in role-hunt/comp/ for a week) as the Comp markdown block — level → median TC, YOE,
    the median base/stock/bonus mix, with the required attribution line. Read-only."""
    from harness.career.comp import get_ladder, ladder_markdown

    ladder, _ = get_ladder(
        role_hunt_root(get_settings()), company, family=family, location=location, refresh=refresh
    )
    return ladder_markdown(ladder)


@mcp.tool()
def pool(level: str = "staff", location: str = "us", family: str = "software-engineer",
         refresh: bool = False) -> str:
    """One levels.fyi leaderboard cut read through the pool's item model (base ilvl = comp vs the
    anchor, named modifiers, set bonuses, the one-tier cap, rarity) joined to the watchlist / bench /
    screen flags — the survey JSON. Never writes the watchlist."""
    import json as _json

    from harness.career.pool import build_pool

    s = get_settings()
    return _json.dumps(build_pool(s.tracker_path, role_hunt_root(s), family=family, level=level,
                                  location=location, refresh=refresh))


@mcp.tool()
def roles(new_only: bool = False, location: str = "all", company: str | None = None) -> str:
    """The latest openings scan as role items (no network): one item per opening — company base
    item level from the pool, named role modifiers (level, shape lane, location, posted range vs the
    anchor, freshness, new), rarity, and a fit class (on-profile · adjacent · off-lane) that orders
    them. `location`: all | us | home. The RolesReport JSON."""
    import json as _json

    from harness.career.roles import roles_payload

    s = get_settings()
    return _json.dumps(roles_payload(s.tracker_path, role_hunt_root(s), new_only=new_only,
                                     location=location, company=company))


@mcp.tool()
def campaign() -> str:
    """Where the career search stands (no network, read-only): pipeline rows in flight with next
    step + date, last touch, channel, screen line, stale/drift flags; the career calendar from
    today; recruiter/interview/ATS mail. The CampaignReport JSON."""
    from harness.career.campaign import build_campaign

    s = get_settings()
    return build_campaign(s.tracker_path, role_hunt_root(s)).model_dump_json()
