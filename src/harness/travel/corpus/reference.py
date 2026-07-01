"""Static reference-almanac reader — the proactive-surfacing layer.

Parses ``{corpus}/travel/reference/*.md`` (the `sports-schedules` + `key-dates-and-holidays` almanac)
into dated :class:`EventResult` entries tagged ``source="reference"``, so a window scan can MERGE
known centerpiece games + holidays + mega-events with live Ticketmaster results. The point: surface
trip-worthy dates the harness would otherwise only catch by a *reactive* live scan of a window it was
already looking at (e.g. a followed team's away game in a city the user was already eyeing).

The followed teams are **config-driven** (`EventWeights.followed_teams`) — the parser carries no
hardcoded team names or cities; a schedule section is matched to a team by its configured
``section_match`` substring.

Markdown-table driven + column-name aware (tolerant of column order). Full-date cells (e.g.
"Mon Sep 7 2026") parse directly; the year-less NFL game tables infer the year from the file's
``current_through: 2026-2027`` season range (month >= Aug -> first year, else second). A row with no
parseable date is skipped. Heading-anchored classification only — never parses prose for numbers.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from harness.travel.models import EventResult
from harness.travel.ranking.weights import FollowedTeam

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}
# "Mon Sep 7 2026" | "December 7, 2026" | "Sep 7" (year-less → season-inferred)
_DATE_RE = re.compile(
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?",
    re.IGNORECASE,
)
_SEASON_RE = re.compile(r"(\d{4})\s*-\s*(\d{4})")


def _clean(cell: str) -> str:
    """Strip markdown emphasis / link-markup / decorative glyphs; collapse whitespace."""
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)  # [text](url) -> text
    for ch in ("**", "*", "⭐", "🏟️", "🏟", "⭐⭐"):
        s = s.replace(ch, "")
    return " ".join(s.split()).strip()


def _parse_date(cell: str, season: tuple[int, int] | None) -> date | None:
    m = _DATE_RE.search(cell)
    if not m:
        return None
    month = _MONTHS[m.group(1)[:3].lower()]
    day = int(m.group(2))
    if m.group(3):
        year = int(m.group(3))
    elif season:
        year = season[0] if month >= 8 else season[1]
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def read_reference(
    corpus_path: Path, followed_teams: list[FollowedTeam] | None = None
) -> list[EventResult]:
    """Parse every ``reference/*.md`` (except README) into source="reference" EventResults.
    `followed_teams` config-drives the schedule parser (default none → no team schedules surfaced)."""
    ref_dir = corpus_path / "reference"
    if not ref_dir.is_dir():
        return []
    teams = followed_teams or []
    out: list[EventResult] = []
    for fp in sorted(ref_dir.glob("*.md")):
        if fp.name.lower() == "readme.md":
            continue
        out.extend(_parse_file(fp, teams))
    return out


def _parse_file(fp: Path, teams: list[FollowedTeam]) -> list[EventResult]:
    text = fp.read_text()
    season: tuple[int, int] | None = None
    for line in text.splitlines():
        if line.lower().startswith("current_through"):
            sm = _SEASON_RE.search(line)
            if sm:
                season = (int(sm.group(1)), int(sm.group(2)))
            break
    out: list[EventResult] = []
    section = ""
    cols: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            section = s.lstrip("#").strip().lower()
            cols = None
            continue
        if not s:
            cols = None  # blank line ends a table
            continue
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue  # |---|---| separator
        low = [c.lower() for c in cells]
        if cols is None:
            if any(h in low for h in ("date", "when")):
                cols = low  # header row
            continue
        row = dict(zip(cols, cells, strict=False))
        entry = _row_to_event(row, section, season, teams)
        if entry is not None:
            out.append(entry)
    return out


def _row_to_event(
    row: dict[str, str], section: str, season: tuple[int, int] | None, teams: list[FollowedTeam]
) -> EventResult | None:
    d = _parse_date(row.get("date", "") or row.get("when", ""), season)
    if d is None:
        return None
    iso = d.isoformat()

    team = next((t for t in teams if t.section_match.lower() in section), None)
    if team is not None and ("opponent" in row or "h/a" in row):
        opp = re.sub(r"^@\s*", "", _clean(row.get("opponent", ""))) or "TBD"
        ha = (row.get("h/a") or "").lower()
        is_home = team.home_only or "home" in ha
        # home → "{team} vs {opp}" at the team's home venue; away → "{team} @ {opp}", venue unknown.
        name = f"{team.name} vs {opp}" if is_home else f"{team.name} @ {opp}"
        city = team.home_venue if is_home else ""
        return EventResult(
            name=name, segment="Sports", genre=team.sport, subgenre=team.league, local_date=iso,
            venue=city, city=city, source="reference",
        )
    if "mega-event" in section:
        return EventResult(
            name=_clean(row.get("event", "")) or "Mega-event", segment="Mega-event",
            local_date=iso, venue=_clean(row.get("where", "")), city=_clean(row.get("where", "")),
            source="reference",
        )
    if "personal" in section:
        return EventResult(
            name=_clean(row.get("what", "")) or "Personal date", segment="Personal",
            local_date=iso, source="reference",
        )
    if "holiday" in section or "long-weekend" in section:
        shape = _clean(row.get("long-weekend shape", ""))
        hol = _clean(row.get("holiday", "")) or "Holiday"
        return EventResult(
            name=f"{hol} — {shape}" if shape else hol, segment="Holiday", local_date=iso,
            source="reference",
        )
    return None  # unrecognized section — don't guess
