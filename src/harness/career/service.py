"""CareerService — read-only openings scan across the watchlist (the read side of the
read-rich/execute-gated doctrine: scan + watch + surface comp freely; applying is the maintainer's act).

Stateless + explainable: reads `role-hunt/watchlist.yml`, queries each company's public board API,
filters titles by the watchlist keywords (ANY title-kw AND ANY seniority-kw, lowercase-contains),
and returns per-company results with partial failures surfaced loudly (a board erroring must never
read as "no openings there" — the false-empty lesson)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness.career.models import CompanyScan
from harness.career.providers import (
    fetch_ashby,
    fetch_eightfold,
    fetch_greenhouse,
    fetch_workday,
)
from harness.career.watchlist import Watchlist, load_watchlist
from harness.errors import ProviderError

_FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby}


def slugify(name: str) -> str:
    """Company name → filename/URL slug (the per-company doc home key). 'Sony PlayStation' →
    'sony-playstation'; 'T-Mobile' → 't-mobile'; 'Grafana Labs' → 'grafana-labs'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

# Shortlist tier ordering — most-actionable first (primary-region leadership, then IC, then
# ai-infra, then seeds). Unknown tiers sort last. Mirrors the role-hunt corpus tier vocabulary
# (role-hunt/watchlist.yml); a tier not listed here lands at the bottom (rank 9), never dropped.
_TIER_RANK: dict[str, int] = {
    "shape-a-region": 0,
    "shape-b-ic": 1,
    "ai-infra": 2,
    "shape-b-seed": 3,
    "region-anchor": 4,
}

# The role-shape buckets that count as deep-IC / infra (Shape-B-ish) vs leadership (Shape-A-ish),
# for the shortlist summary tiles. Maps the deterministic shape_bucket labels onto the goal frame.
_IC_INFRA_SHAPES = ("SRE / Reliability", "Platform / Infra", "Security")


def opening_matches(
    title: str,
    department: str,
    title_any: list[str],
    seniority_any: list[str],
    title_none: list[str] | None = None,
) -> bool:
    """Domain keywords match against title OR department/team (lots of infra roles are titled just
    "Senior Software Engineer" with the infra signal in the team field); seniority keywords match the
    title only; exclusion keywords (noise control —
    accounting/marketing/etc. riding domain words) match either."""
    t, hay = title.lower(), f"{title} {department}".lower()
    if any(k.lower() in hay for k in (title_none or [])):
        return False
    domain_ok = any(k.lower() in hay for k in title_any) if title_any else True
    seniority_ok = any(k.lower() in t for k in seniority_any) if seniority_any else True
    return domain_ok and seniority_ok


class CareerService:
    def __init__(self, tracker_path: Path, *, role_hunt_root: Path | None = None) -> None:
        self.tracker_path = tracker_path
        # The role-hunt corpus root every read resolves from. Defaults to `<tracker>/role-hunt`
        # (legacy + tests); the CLI/MCP pass the pack-resolved root so a loaded weight pack drives
        # every career read (watchlist, discoveries, target-map, fit-profiles). Additive.
        self.role_hunt = role_hunt_root or (tracker_path / "role-hunt")

    def load(self) -> Watchlist:
        return load_watchlist(self.tracker_path, root=self.role_hunt)

    def scan(
        self,
        companies: list[str] | None = None,
        grep: str | None = None,
        unfiltered: bool = False,
    ) -> list[CompanyScan]:
        """Scan the watchlist's queryable companies. `companies` narrows by name (case-insensitive
        substring); `grep` is an extra title-contains filter; `unfiltered` bypasses the watchlist
        keyword filters (grep still applies)."""
        wl = self.load()
        wanted = [c.lower() for c in companies] if companies else None
        out: list[CompanyScan] = []
        for comp in wl.companies:
            if wanted and not any(w in comp.name.lower() for w in wanted):
                continue
            if comp.ats == "none":
                out.append(
                    CompanyScan(
                        company=comp.name,
                        ats="none",
                        skipped=comp.note or f"no public board API — check {comp.portal or 'portal'}",
                    )
                )
                continue
            try:
                if comp.ats == "workday":
                    openings = fetch_workday(
                        comp.name, terms=wl.filters.title_any, **comp.workday
                    )
                elif comp.ats == "eightfold":
                    openings = fetch_eightfold(
                        comp.name, terms=wl.filters.title_any, **comp.eightfold
                    )
                else:
                    openings = _FETCHERS[comp.ats](comp.name, comp.token)
            except ProviderError as e:
                out.append(CompanyScan(company=comp.name, ats=comp.ats, error=str(e)))
                continue
            f = wl.filters
            matched = [
                o
                for o in openings
                if (
                    unfiltered
                    or opening_matches(o.title, o.department, f.title_any, f.seniority_any, f.title_none)
                )
                and (not grep or grep.lower() in o.title.lower())
            ]
            matched.sort(key=lambda o: (o.salary is None, o.title))
            out.append(
                CompanyScan(
                    company=comp.name, ats=comp.ats, matched=matched, total_open=len(openings)
                )
            )
        return out

    # Role-shape buckets for the openings matrix (deterministic keyword classification —
    # the doc + its visual derive from the same scan in the same call; lock-step by construction).
    _SHAPE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("SRE / Reliability", ("sre", "reliability", "site reliability")),
        ("Platform / Infra", ("platform", "infrastructure", "infra", "kubernetes", "cloud",
                              "devops", "systems engineer")),
        ("Security", ("security",)),
        ("Leadership", ("director", "manager", "head of", "lead,")),
    )

    @classmethod
    def shape_bucket(cls, title: str) -> str:
        t = title.lower()
        for label, keys in cls._SHAPE_BUCKETS:
            if any(k in t for k in keys):
                return label
        return "Other"

    @classmethod
    def openings_matrix_data(cls, scans: list[CompanyScan], title: str) -> dict[str, Any] | None:
        """Company × role-shape counts for the `matrix` viz; None when no hits."""
        axes = [label for label, _ in cls._SHAPE_BUCKETS] + ["Other"]
        rows: list[dict[str, Any]] = []
        for s in scans:
            if not s.matched:
                continue
            counts = dict.fromkeys(axes, 0)
            for o in s.matched:
                counts[cls.shape_bucket(o.title)] += 1
            rows.append({"label": f"{s.company} ({len(s.matched)})",
                         "values": [counts[a] for a in axes],
                         # deep-link the row to the company's profile doc (the scatter-ref pattern).
                         # The static render.js ignores `ref`; the interactive Matrix
                         # twin makes the label clickable → VAULT. Doc generated by company_profiles.
                         "ref": {"zone": "vault", "doc": f"role-hunt/companies/{slugify(s.company)}.md"}})
        if not rows:
            return None
        rows.sort(key=lambda r: -sum(int(v) for v in r["values"]))
        return {
            "title": title,
            "subtitle": "matched openings per company × role shape · deterministic title buckets · counts",
            "axes": axes,
            "rows": rows,
        }

    def _discoveries_dir(self) -> Path:
        return self.role_hunt / "discoveries"

    def latest_scan(self) -> tuple[list[CompanyScan], str | None]:
        """Load the most recent persisted scan JSON twin (written by `openings --write`).

        Returns (scans, as_of-date-string) — empty + None when no twin exists yet. The shortlist /
        dashboard read this LOCAL artifact (never a live board scan on refresh); refreshing the data
        is the deliberate `openings --write` action. Filenames sort lexically = chronologically."""
        twins = sorted(self._discoveries_dir().glob("*-openings.json"))
        if not twins:
            return [], None
        latest = twins[-1]
        as_of = latest.stem.replace("-openings", "")  # YYYY-MM-DD
        try:
            raw = json.loads(latest.read_text())
        except (OSError, ValueError) as e:
            raise ProviderError(f"could not read scan twin {latest}: {e}") from e
        return [CompanyScan.model_validate(s) for s in raw], as_of

    def shortlist(self, limit: int = 30) -> dict[str, Any]:
        """High-priority role shortlist from the latest scan twin + watchlist tiers (LOCAL, no
        network). The CAREER-dashboard contract: a `summary` (tile counts) + ranked `roles` rows.

        Ranking: tier (most-actionable first) → company match-count (busier boards surface) →
        salary-known → title. Tier is a displayed column; unknown tiers sort last but are kept."""
        scans, as_of = self.latest_scan()
        tier_by_company: dict[str, str] = {}
        try:
            for c in self.load().companies:
                tier_by_company[c.name] = c.tier
        except ProviderError:
            pass  # no watchlist → roles just carry empty tiers

        roles: list[dict[str, Any]] = []
        shape_counts: dict[str, int] = {}
        leadership = 0
        boards_with_hits = 0
        scanned = 0
        for s in scans:
            if s.ats != "none":
                scanned += 1
            if not s.matched:
                continue
            boards_with_hits += 1
            tier = tier_by_company.get(s.company, "")
            for o in s.matched:
                shape = self.shape_bucket(o.title)
                shape_counts[shape] = shape_counts.get(shape, 0) + 1
                if shape == "Leadership":
                    leadership += 1
                roles.append({
                    "company": s.company,
                    "tier": tier,
                    "shape": shape,
                    "title": o.title,
                    "location": o.location + (" · remote" if o.remote else ""),
                    "salary": o.salary or "—",
                    "updated": o.updated or "—",
                    "url": o.url,
                })
        roles.sort(key=lambda r: (
            _TIER_RANK.get(str(r["tier"]), 9),
            r["salary"] == "—",
            str(r["title"]),
        ))
        total = len(roles)
        ic_infra = sum(1 for r in roles if r["shape"] in _IC_INFRA_SHAPES)
        # Living visuals (lock-step with this scan): the matrix (company × role-shape grid) + the
        # shape bar (aggregate openings-by-role-shape — the at-a-glance market shape). The CAREER
        # dashboard renders both off this same call (no drift); shape_bar is the rank-bar contract.
        ordered_shapes = [label for label, _ in self._SHAPE_BUCKETS] + ["Other"]
        shape_bar = {
            "title": "Openings by role shape",
            "subtitle": f"matched openings per deterministic title bucket · as of {as_of or '—'}",
            "rows": [
                {"label": label, "parts": [{"key": "openings", "value": shape_counts[label]}]}
                for label in ordered_shapes
                if shape_counts.get(label)
            ],
        }
        matrix = self.openings_matrix_data(
            scans, f"Openings — company × role shape · {as_of or '—'}"
        ) or {"title": "Openings — company × role shape", "axes": ordered_shapes, "rows": []}
        return {
            "summary": {
                "total": total,
                "leadership": leadership,
                "ic_infra": ic_infra,
                "boards_with_hits": boards_with_hits,
                "scanned_boards": scanned,
                "as_of": as_of or "—",
            },
            "roles": roles[:limit],
            "shape_bar": shape_bar,
            "matrix": matrix,
        }

    @staticmethod
    def to_markdown(scans: list[CompanyScan], title: str) -> str:
        """Discoveries-report renderer (the `--write` artifact): one table per company with hits."""
        lines = [f"# {title}", ""]
        hits = sum(len(s.matched) for s in scans)
        boards = [s for s in scans if s.ats != "none"]
        lines.append(
            f"> {hits} matched openings across {len(boards)} queryable boards "
            f"({sum(s.total_open for s in scans)} total postings scanned). "
            "Read-only scan; salary text is as-posted (pay-transparency states), unnormalized."
        )
        lines.append("")
        for s in scans:
            if s.error:
                lines += [f"## {s.company} — ⚠ scan error", "", f"`{s.error}`", ""]
                continue
            if s.skipped:
                lines += [f"## {s.company} — manual-watch ({s.skipped})", ""]
                continue
            if not s.matched:
                lines += [f"## {s.company} — 0 matches ({s.total_open} total postings)", ""]
                continue
            lines += [f"## {s.company} — {len(s.matched)} matches ({s.total_open} total postings)", ""]
            lines.append("| Title | Location | Salary (as posted) | Updated | Link |")
            lines.append("|---|---|---|---|---|")
            for o in s.matched:
                loc = o.location + (" · remote" if o.remote else "")
                lines.append(
                    f"| {o.title} | {loc} | {o.salary or '—'} | {o.updated or '—'} | [post]({o.url}) |"
                )
            lines.append("")
        return "\n".join(lines)

    # ----- per-company profiles (the Hiring-Map deep-link targets) -----

    def _target_map(self) -> dict[str, dict[str, Any]]:
        """Index target-map-data.json by company name (lowercased) → {axes:[(axis,score)], comp}."""
        path = self.role_hunt / "target-map-data.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        axes = data.get("axes", [])
        out: dict[str, dict[str, Any]] = {}
        for row in data.get("rows", []):
            label = str(row.get("label", "")).strip()
            if not label:
                continue
            out[label.lower()] = {
                "axes": list(zip(axes, row.get("values", []), strict=False)),
                "comp": row.get("reach", ""),
            }
        return out

    def _fit_excerpt(self, name: str) -> str | None:
        """Pull a company's `### {name}` section from fit-profiles.md (prose excerpt — not numbers).
        Matches a heading that starts with the name (or vice-versa) so 'Acme' finds 'Acme / AcmeCloud'."""
        path = self.role_hunt / "fit-profiles.md"
        if not path.exists():
            return None
        nl = name.lower()
        lines = path.read_text().splitlines()
        grab: list[str] = []
        capturing = False
        for ln in lines:
            if ln.startswith("### "):
                if capturing:
                    break  # next company section — stop
                head = ln[4:].strip().lower()
                first = head.split(" ")[0]
                if head.startswith(nl) or nl.startswith(first):
                    capturing = True
                continue
            if ln.startswith("## ") and capturing:
                break  # next group section
            if capturing:
                grab.append(ln)
        text = "\n".join(grab).strip()
        return text or None

    def company_profiles(self) -> list[dict[str, Any]]:
        """Assemble a per-company profile for every matrix-present company (those with matched
        openings in the latest scan twin) from the corpus: watchlist (tier/ats/portal/note) +
        target-map axes/comp + fit-profiles excerpt + the scan's openings. LOCAL, no network."""
        scans, as_of = self.latest_scan()
        wl_by: dict[str, Any] = {}
        try:
            for c in self.load().companies:
                wl_by[c.name] = c
        except ProviderError:
            pass
        tmap = self._target_map()
        out: list[dict[str, Any]] = []
        for s in scans:
            if not s.matched:
                continue
            name = s.company
            shapes: dict[str, int] = {}
            for o in s.matched:
                sh = self.shape_bucket(o.title)
                shapes[sh] = shapes.get(sh, 0) + 1
            tm = tmap.get(name.lower()) or next(
                (v for k, v in tmap.items() if k.startswith(name.lower()) or name.lower().startswith(k)),
                None,
            )
            wc = wl_by.get(name)
            out.append({
                "slug": slugify(name), "name": name,
                "tier": getattr(wc, "tier", ""), "ats": getattr(wc, "ats", ""),
                "portal": getattr(wc, "portal", ""), "note": getattr(wc, "note", ""),
                "as_of": as_of or "—", "openings": len(s.matched), "total_open": s.total_open,
                "shapes": shapes,
                "roles": [
                    {"title": o.title, "location": o.location + (" · remote" if o.remote else ""),
                     "salary": o.salary or "—", "url": o.url}
                    for o in s.matched
                ],
                "target_map": tm,
                "fit_excerpt": self._fit_excerpt(name),
            })
        return out

    @staticmethod
    def company_profile_md(d: dict[str, Any]) -> str:
        """Render the AUTO-GENERATED block of a company profile (the CLI preserves a hand-edited
        notes section below a sentinel on regen)."""
        shape_str = ", ".join(f"{k} {v}" for k, v in sorted(d["shapes"].items(), key=lambda x: -x[1]))
        lines = [
            "---",
            "tags: [career, role-hunt, company-profile]",
            f"company: {d['name']}",
            f"tier: {d['tier'] or ''}",
            f"generated: {d['as_of']}",
            "---",
            "",
            f"# {d['name']} — role-hunt profile",
            "",
            f"> Auto-generated (`hn career company-profiles`). Tier **{d['tier'] or '—'}** · "
            f"ATS `{d['ats'] or '—'}`"
            + (f" · [careers]({d['portal']})" if d["portal"] else "")
            + f"  ·  _last scan {d['as_of']}: {d['openings']} matched openings._",
            "",
            "## At a glance",
            f"- **Tier**: {d['tier'] or '—'}",
            f"- **Openings (last scan)**: {d['openings']} matched / {d['total_open']} total — {shape_str}",
        ]
        if d["note"]:
            lines.append(f"- **Watchlist note**: {d['note']}")
        lines.append("")
        if d["target_map"]:
            lines += ["## Target-map read", "", f"_Comp band: {d['target_map'].get('comp') or '—'}_", ""]
            lines += ["| Axis | Score |", "|---|---|"]
            for axis, score in d["target_map"]["axes"]:
                lines.append(f"| {axis} | {score} |")
            lines.append("")
        lines += ["## Open roles (last scan)", "",
                  "| Title | Location | Salary | Link |", "|---|---|---|---|"]
        for r in d["roles"]:
            lines.append(f"| {r['title']} | {r['location']} | {r['salary']} | [post]({r['url']}) |")
        lines += ["", "## Fit", "", d["fit_excerpt"] or "_No fit-profile section yet — add one in "
                  "`fit-profiles.md`._", ""]
        return "\n".join(lines)
