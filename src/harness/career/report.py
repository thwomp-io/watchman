"""The reports rail — levels.fyi's year-end pay report + the family/metro percentile pages as a
yearly anchor-calibration input.

Why: the overlay's comp anchor is a point-in-time number; without this, "is the anchor mid-field?"
gets re-derived by hand. levels.fyi publishes `/{year}/report.md` (keyless markdown — the per-level
medians with YoY, the Top-Pay-by-Level tables, the US metros, the top US companies) and the
family pages `/t/{family}.md` + `/t/{family}/locations/{geo}.md` (median + p25/p75/p90 + top-5
companies). This verb fetches the three, writes `role-hunt/reference/pay-report-{year}.md` (+ a
JSON twin the Ladders tab header reads) with the attribution line, and prints the ANCHOR READ:
where the overlay's comp anchor sits against the metro percentiles, the report's level medians
and the Staff/Principal top-list floors — stated in words, never a verdict.

Facts (verified live 2026-09): `/2025/report.md` serves; `/2024/report.md` answers 403 with a 404
page (the archives are HTML only), so "newest available" is found by probing years downward and
naming the first that serves markdown. The `.md` family pages cap top-company lists at five (the
full list is the leaderboard — the pool). All three routes sit behind the same browser-UA gate as
the ladders, so the overlay opt-in applies (the provider handles it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from harness.career.providers.levels import ATTRIBUTION, BASE, fetch_levels_page, parse_money
from harness.errors import ProviderError

DEFAULT_FAMILY = "software-engineer"
DEFAULT_METRO = "united-states"  # the fallback when the overlay names no metro


def default_metro() -> str:
    """The metro page the anchor read compares against: the overlay's `career.comp.metro` (a
    levels.fyi location slug, e.g. `san-francisco-bay-area`), else US-wide."""
    from harness.settings import overlay_get

    raw = overlay_get("career", "comp", "metro", default=None)
    return str(raw).strip() if raw and str(raw).strip() else DEFAULT_METRO
LEVEL_KEYS = (
    "Entry Level Engineer",
    "Software Engineer",
    "Senior Engineer",
    "Staff Engineer",
    "Principal Engineer",
)


def report_url(year: int) -> str:
    return f"{BASE}/{year}/report.md"


def family_url(family: str, metro: str | None = None) -> str:
    return f"{BASE}/t/{family}/locations/{metro}.md" if metro else f"{BASE}/t/{family}.md"


def _table(block: str) -> list[list[str]]:
    """Markdown table rows (cells stripped), header + separator skipped."""
    rows = []
    for ln in block.splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def _section(text: str, heading: str, level: str = "#") -> str:
    """The text under a heading up to the next heading of the same-or-higher level."""
    m = re.search(rf"^{re.escape(level)} {re.escape(heading)}.*?$", text, re.M)
    if not m:
        return ""
    rest = text[m.end() :]
    n = re.search(rf"^#{{1,{len(level)}}} ", rest, re.M)
    return rest[: n.start()] if n else rest


def parse_report_md(text: str) -> dict[str, Any]:
    """Structure the year-end report page: title/year, the highlights, the YoY summary, the SWE
    per-level medians (prior/current/change), Top Pay by Level for the software-engineer family
    (level → ranked rows), the US metros, the top US companies."""
    out: dict[str, Any] = {
        "levels": {},
        "top_by_level": {},
        "metros": [],
        "top_us_companies": [],
        "highlights": {},
        "yoy": {},
    }
    m = re.search(r"^# .*?(\d{4})", text, re.M)
    out["year"] = int(m.group(1)) if m else None
    for cells in _table(
        _section(text, "2025 Highlights", "##") or _section(text, f"{out['year']} Highlights", "##")
    ):
        if len(cells) >= 2:
            out["highlights"][cells[0]] = cells[1]
    for cells in _table(_section(text, "Year-over-Year Growth Summary", "###")):
        if len(cells) >= 2:
            out["yoy"][cells[0]] = cells[1]
    lv = _section(text, "Median Total Compensation by Level (Software Engineer)", "###")
    hdr = re.search(r"^\|\s*Level\s*\|\s*(\d{4})\s*\|\s*(\d{4})", lv, re.M)
    out["level_years"] = [int(hdr.group(1)), int(hdr.group(2))] if hdr else []
    for cells in _table(lv):
        if len(cells) >= 4:
            out["levels"][cells[0]] = {
                "prior": parse_money(cells[1]),
                "current": parse_money(cells[2]),
                "change": cells[3],
            }
    swe = _section(_section(text, "Top Pay by Level", "##"), "Software Engineer", "###")
    for lvl_m in re.finditer(r"^#### (.+?)\s*$", swe, re.M):
        name = lvl_m.group(1).strip()
        block = _section(swe, name, "####")
        out["top_by_level"][name] = [
            {"rank": int(c[0]), "company": c[1], "location": c[2], "total": parse_money(c[3])}
            for c in _table(block)
            if len(c) >= 4 and c[0].isdigit()
        ]
    for cells in _table(_section(text, "Top Paying U.S. Metros (SWE)", "####")):
        if len(cells) >= 3 and cells[0].isdigit():
            out["metros"].append(
                {
                    "rank": int(cells[0]),
                    "metro": cells[1],
                    "median": parse_money(cells[2]),
                    "yoy": cells[3] if len(cells) > 3 else "",
                }
            )
    for cells in _table(_section(text, "Top Paying U.S. Companies (SWE)", "####")):
        if len(cells) >= 3 and cells[0].isdigit():
            out["top_us_companies"].append(
                {"rank": int(cells[0]), "company": cells[1], "median": parse_money(cells[2])}
            )
    return out


def parse_family_md(text: str) -> dict[str, Any]:
    """A `/t/{family}[.../locations/{geo}].md` page: scope/location, median, p25/p75/p90, the
    top-5 companies, last-updated."""
    out: dict[str, Any] = {"top_companies": []}
    for k, v in re.findall(r"^\*\*(Scope|Location|Currency|Generated):\*\*\s*(.+?)\s*$", text, re.M):
        out[k.lower()] = v.strip()
    m = re.search(r"^- Median Total Compensation:\s*(\S+)", text, re.M)
    out["median"] = parse_money(m.group(1)) if m else None
    m = re.search(r"^- 25th / 75th Percentile:\s*(\S+)\s*/\s*(\S+)", text, re.M)
    out["p25"], out["p75"] = (parse_money(m.group(1)), parse_money(m.group(2))) if m else (None, None)
    m = re.search(r"^- 90th Percentile:\s*(\S+)", text, re.M)
    out["p90"] = parse_money(m.group(1)) if m else None
    m = re.search(r"^- Last Updated:\s*(.+?)\s*$", text, re.M)
    out["last_updated"] = m.group(1) if m else ""
    for cells in _table(_section(text, "Top Paying Companies", "###")):
        if len(cells) >= 3 and cells[0].isdigit():
            out["top_companies"].append(
                {"rank": int(cells[0]), "company": cells[1], "median": parse_money(cells[2])}
            )
    return out


def newest_available_year(
    fetch: Callable[[str], str], today: date | None = None, span: int = 3
) -> tuple[int, str]:
    """(year, text) for the newest year whose report serves as markdown, probing downward from
    this year; a named refusal when none does."""
    y = (today or date.today()).year
    tried = []
    for year in range(y, y - span - 1, -1):
        try:
            text = fetch(report_url(year))
        except ProviderError:
            tried.append(year)
            continue
        if text.lstrip().startswith("#"):
            return year, text
        tried.append(year)
    raise ProviderError(f"no year-end report serves as markdown for {', '.join(map(str, tried))}")


def anchor_read(anchor: int, metro: dict[str, Any], us: dict[str, Any], rep: dict[str, Any]) -> list[str]:
    """The position of the anchor against the field, in words — a surface to react to."""
    lines: list[str] = []

    def bracket(label: str, pts: list[tuple[str, int | None]]) -> str:
        known = [(n, v) for n, v in pts if v]
        if not known:
            return f"{label}: no percentiles on the page"
        below = [(n, v) for n, v in known if anchor >= v]
        above = [(n, v) for n, v in known if anchor < v]
        if not below:
            return f"{label}: below the {known[0][0]} (${known[0][1]:,})"
        if not above:
            return f"{label}: above the {below[-1][0]} (${below[-1][1]:,}) — past every listed percentile"
        return (
            f"{label}: above the {below[-1][0]} (${below[-1][1]:,}), "
            f"below the {above[0][0]} (${above[0][1]:,})"
        )

    pts = [
        ("p25", metro.get("p25")),
        ("median", metro.get("median")),
        ("p75", metro.get("p75")),
        ("p90", metro.get("p90")),
    ]
    lines.append(bracket(f"Anchor ${anchor:,} vs {metro.get('location') or 'the metro'} (all levels)", pts))
    pts = [
        ("p25", us.get("p25")),
        ("median", us.get("median")),
        ("p75", us.get("p75")),
        ("p90", us.get("p90")),
    ]
    lines.append(bracket("vs the US family page (all levels)", pts))
    for key in ("Staff Engineer", "Principal Engineer"):
        cur = rep.get("levels", {}).get(key, {}).get("current")
        if cur:
            rel = anchor / cur - 1
            word = "at" if abs(rel) < 0.01 else ("above" if rel > 0 else "below")
            yrs = rep.get("level_years") or ["", ""]
            lines.append(
                f"vs the {yrs[-1]} US {key} median (${cur:,}): {word} it"
                + ("" if word == "at" else f" by {abs(rel) * 100:.0f}%")
            )
    for name, rows in rep.get("top_by_level", {}).items():
        if not rows or not re.search(r"Staff|Principal", name):
            continue
        floor_row = rows[-1]
        n = len(rows)
        if anchor >= floor_row["total"]:
            lines.append(
                f"vs the {name} top-{n}: inside it — above the floor (#{floor_row['rank']} "
                f"{floor_row['company']} ${floor_row['total']:,})"
            )
        else:
            lines.append(
                f"vs the {name} top-{n}: below the floor (#{floor_row['rank']} {floor_row['company']} "
                f"${floor_row['total']:,}) by {(1 - anchor / floor_row['total']) * 100:.0f}%"
            )
    return lines


def build_report(
    role_hunt: Path,
    *,
    year: int | None,
    anchor: int,
    family: str = DEFAULT_FAMILY,
    metro: str | None = None,
    refresh: bool = False,
    fetch: Callable[[str], str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Fetch (or reuse the vault copy of) the year's report + the two family pages, parse, write
    the reference doc + JSON twin, return the contract (report · metro · us · anchor read)."""
    fetch = fetch or fetch_levels_page
    metro = metro or default_metro()
    ref = role_hunt / "reference"
    if year is not None and not refresh:
        twin = ref / f"pay-report-{year}.json"
        if twin.exists():
            try:
                cached: dict[str, Any] = dict(json.loads(twin.read_text(encoding="utf-8")))
                cached["from_cache"] = True
                cached["anchor"] = anchor
                cached["anchor_read"] = anchor_read(anchor, cached["metro"], cached["us"], cached["report"])
                return cached
            except (OSError, ValueError, KeyError):
                pass
    if year is None:
        year, text = newest_available_year(fetch, today)
    else:
        try:
            text = fetch(report_url(year))
        except ProviderError as e:
            newest, _ = newest_available_year(fetch, today)
            raise ProviderError(
                f"no {year} report as markdown ({e}); the newest that serves is {newest}"
            ) from e
        if not text.lstrip().startswith("#"):
            newest, _ = newest_available_year(fetch, today)
            raise ProviderError(
                f"the {year} report is not served as markdown; the newest that serves is {newest}"
            )
    rep = parse_report_md(text)
    us = parse_family_md(fetch(family_url(family)))
    met = parse_family_md(fetch(family_url(family, metro)))
    out: dict[str, Any] = {
        "year": year,
        "family": family,
        "metro_slug": metro,
        "anchor": anchor,
        "report": rep,
        "us": us,
        "metro": met,
        "urls": {"report": report_url(year), "us": family_url(family), "metro": family_url(family, metro)},
        "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "attribution": ATTRIBUTION,
        "from_cache": False,
    }
    out["anchor_read"] = anchor_read(anchor, met, us, rep)
    ref.mkdir(parents=True, exist_ok=True)
    (ref / f"pay-report-{year}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (ref / f"pay-report-{year}.md").write_text(report_markdown(out), encoding="utf-8")
    return out


def report_markdown(r: dict[str, Any]) -> str:
    rep, us, met = r["report"], r["us"], r["metro"]
    yrs = rep.get("level_years") or ["prior", "current"]
    lines = [
        "---",
        f"tags: [career, role-hunt, comp, levels-fyi, pay-report, {r['year']}]",
        f"year: {r['year']}",
        f"fetched: {r['fetched_at'][:10]}",
        "---",
        "",
        f"# levels.fyi year-end pay report {r['year']} — the anchor read",
        "",
        f"> Fetched {r['fetched_at'][:10]}. {r['attribution']}. Anchor ${r['anchor']:,} "
        "(`career.comp_anchor.anchor_tc`).",
        "",
        "## Anchor vs the field",
        "",
    ]
    lines += [f"- {ln}" for ln in r["anchor_read"]]
    lines += [
        "",
        f"## {met.get('location') or r.get('metro_slug', '')} — {r['family']} (all levels)",
        "",
        f"median ${met.get('median') or 0:,} · p25 ${met.get('p25') or 0:,} · p75 ${met.get('p75') or 0:,} · "
        f"p90 ${met.get('p90') or 0:,} · site updated {met.get('last_updated', '?')}",
        "",
    ]
    if met.get("top_companies"):
        lines += ["| # | Company | Median TC |", "|---|---|---|"]
        lines += [f"| {c['rank']} | {c['company']} | ${c['median']:,} |" for c in met["top_companies"]]
        lines.append("")
    lines += [
        f"## United States — {r['family']} (all levels)",
        "",
        f"median ${us.get('median') or 0:,} · p25 ${us.get('p25') or 0:,} · p75 ${us.get('p75') or 0:,} · "
        f"p90 ${us.get('p90') or 0:,}",
        "",
    ]
    if rep.get("levels"):
        lines += [
            f"## Median TC by level ({r['family']})",
            "",
            f"| Level | {yrs[0]} | {yrs[1]} | Change |",
            "|---|---|---|---|",
        ]
        for k, v in rep["levels"].items():
            lines.append(f"| {k} | ${v['prior'] or 0:,} | ${v['current'] or 0:,} | {v['change']} |")
        lines.append("")
    for name, rows in rep.get("top_by_level", {}).items():
        if not rows:
            continue
        lines += [f"## Top pay — {name}", "", "| # | Company | Location | Total |", "|---|---|---|---|"]
        lines += [f"| {c['rank']} | {c['company']} | {c['location']} | ${c['total']:,} |" for c in rows]
        lines.append("")
    if rep.get("metros"):
        lines += ["## Top US metros (SWE)", "", "| # | Metro | Median | YoY |", "|---|---|---|---|"]
        lines += [f"| {m['rank']} | {m['metro']} | ${m['median']:,} | {m['yoy']} |" for m in rep["metros"]]
        lines.append("")
    if rep.get("yoy"):
        lines += ["## YoY summary", ""] + [f"- {k}: {v}" for k, v in rep["yoy"].items()] + [""]
    lines += [f"_{r['attribution']} · {r['urls']['report']} · {r['urls']['metro']} · {r['urls']['us']}_", ""]
    return "\n".join(lines)
