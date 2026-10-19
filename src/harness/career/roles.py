"""Role items — the openings scan read through the item model, one item per matched opening.

The pool's grammar one level down: a role is a CHILD item of its company. Its base is the company's
final item level from the merged pool (`hn career pool --survey`); an opening at a company with no
band row is **unranked** (shown, never scored — unknown is not poor). Role modifiers are machine
reads of the posting itself, with their values authored in `pool-enchants.yaml`'s `role_modifiers:`
block (the verb applies them; it never invents a number):

- level   — Staff / Senior Staff / Principal / Distinguished / Architect = in band; a leadership seat
            (Director, Head of, Manager) = the leadership seat; Senior without those = below the band;
- shape   — the shared classifier's lane (`career.shapes`): in-lane, neutral, or off-lane
            (data-center facilities, business/GTM, hardware design — kept in the scan, sorted down);
- location — the home area (overlay `career.roles.home_area`, default the applicant's city) · remote
            in the US · elsewhere in the US (relocation) · outside the US · unknown (no modifier);
- comp    — a posted range that reaches the anchor;
- time    — updated within 14 days · new since the earlier scans (delta mode).

Ranking is FIT first, then new, then item level. Fit is a class, not a number: on-profile (in-lane
shape, in-band or leadership level, an eligible location) · adjacent · off-lane (an off-lane shape or
outside the US). Arithmetic alone cannot rank an unranked company's in-lane Principal SRE against a
legendary company's facilities lead, and the item level is still shown in full on every card. The
company item carries the one-tier cap; role modifiers are not capped, so an off-lane role at a
legendary company can sink below its company's tier.

Inputs are on disk only — the scan twin, the pool surveys + ladder caches, the catalogue. No network.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from harness.career.comp import anchor_tc
from harness.career.geo import home_area, location_class
from harness.career.models import CompanyScan, Opening
from harness.career.pool import (
    CONSTITUTION,
    Modifier,
    classify,
    merge_pool,
    rarity_bands,
    salary_max,
)
from harness.career.service import CareerService, slugify
from harness.career.shapes import IN, OFF, lane_of, shape_of
from harness.errors import ProviderError
from harness.yamlio import load_yaml

FIT_ORDER = ("on-profile", "adjacent", "off-lane")
FRESH_DAYS = 14

_IN_BAND = re.compile(r"\b(staff\+?|principal|distinguished|fellow|architect)\b", re.I)
_LEAD = re.compile(r"\b(director|head of|vp|vice president|chief|manager)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?)\b", re.I)

def level_class(title: str) -> str:
    """in_band · lead · below · none."""
    if _IN_BAND.search(title):
        return "in_band"
    if _LEAD.search(title):
        return "lead"
    if _SENIOR.search(title):
        return "below"
    return "none"


def days_old(updated: str, today: date) -> int | None:
    """Days since the provider's updated/published stamp: an ISO date, or Workday's "Posted N Days
    Ago" / "Posted Today" / "Posted Yesterday" / "Posted 30+ Days Ago". None when unparseable."""
    u = (updated or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", u)
    if m:
        try:
            return (today - datetime.strptime(m.group(1), "%Y-%m-%d").date()).days
        except ValueError:
            return None
    low = u.lower()
    if "today" in low:
        return 0
    if "yesterday" in low:
        return 1
    m = re.search(r"(\d+)\+?\s*days?", low)
    return int(m.group(1)) if m else None


def load_role_catalogue(role_hunt: Path) -> dict[str, Modifier]:
    """`role_modifiers:` from pool-enchants.yaml — key → template. Refuses loud when absent: the
    values are authored judgment and the verb never supplies its own."""
    p = role_hunt / "reference" / "pool-enchants.yaml"
    raw = load_yaml(p.read_text(encoding="utf-8")) if p.exists() else None
    block = (raw or {}).get("role_modifiers") if isinstance(raw, dict) else None
    if not isinstance(block, dict) or not block:
        raise ProviderError(
            f"no role_modifiers: block in {p} — the role items' modifier values are authored there"
        )
    return {
        str(k): Modifier(
            key=str(k), name=str(v.get("name", k)), value=int(v.get("value", 0)),
            family=str(v.get("family", "")), how=str(v.get("how", "machine")),
        )
        for k, v in block.items() if isinstance(v, dict)
    }


class CompEstimate(BaseModel):
    """The role's likely level on its company's levels.fyi ladder and that rung's median TC, with the
    neighboring rungs' medians as the low/high edges (levels.fyi publishes medians per level, not
    percentiles). `basis` says how the rung was chosen; `note` names any fallback."""

    level: str
    band: str  # staff | principal | below | above-principal
    basis: str
    median_tc: int
    low_level: str | None = None
    low_tc: int | None = None
    high_level: str | None = None
    high_tc: int | None = None
    ilvl: int | None = None  # the rung's median vs the anchor (the role's base)
    note: str = ""
    location: str = "united-states"
    attribution: str = ""


class RoleItem(BaseModel):
    company: str
    slug: str
    title: str
    url: str
    location: str = ""
    location_class: str = "unknown"
    salary: str = ""  # as posted
    updated: str = ""
    days_old: int | None = None
    first_seen: str = ""
    new: bool = False
    shape: str = "Other"
    lane: str = "neutral"
    level: str = "none"
    fit: str = "adjacent"
    company_ilvl: int | None = None
    company_rarity: str | None = None
    comp: CompEstimate | None = None
    base_ilvl: int | None = None  # the rung's ilvl when the ladder has it, else the company item's
    base_source: str = ""  # "level ladder" | "company item" | ""
    company_mods: int = 0  # the company item's own named modifiers, carried onto a ladder base
    company_median_tc: int | None = None  # the ladder page's all-levels median (context, never a base)
    modifiers: list[Modifier] = Field(default_factory=list)
    modifier_sum: int = 0
    final_ilvl: int | None = None
    rarity: str = "unranked"
    screened_out: bool = False
    screen_flag: str = "none"
    doc: str | None = None  # the company's research entry (the pool row's)
    profile: str | None = None  # role-hunt/companies/<slug>.md when generated


class RoleFilters(BaseModel):
    new: bool = False
    location: str = "all"
    company: str = "auto"


class RoleCounts(BaseModel):
    total: int = 0
    shown: int = 0
    new: int = 0
    fit: dict[str, int] = Field(default_factory=dict)


class RoleCompany(BaseModel):
    company: str
    slug: str
    openings: int = 0
    on_profile: int = 0
    new: int = 0


class RolesReport(BaseModel):
    """`hn career roles --json` — the Openings tab."""

    as_of: str
    anchor: int
    bands: dict[str, float]
    filters: RoleFilters
    counts: RoleCounts
    rows: list[RoleItem]
    screen_gated: list[RoleItem]
    companies: list[RoleCompany]
    constitution: str


def _mods_for(
    o: Opening, *, shape: str, level: str, loc: str, age: int | None, anchor: int,
    cat: dict[str, Modifier],
) -> list[Modifier]:
    hits: list[tuple[str, str]] = []
    hits.append({
        "in_band": ("level_in_band", f"title: {o.title}"),
        "lead": ("level_lead", f"title: {o.title}"),
        "below": ("level_below", f"title: {o.title}"),
    }.get(level, ("", "")))
    lane = lane_of(shape)
    if lane == IN:
        hits.append(("shape_in_lane", f"shape: {shape}"))
    elif lane == OFF:
        hits.append(("shape_off_lane", f"shape: {shape}"))
    hits.append({
        "home": ("loc_home", f"location: {o.location}"),
        "remote_us": ("loc_remote_us", f"location: {o.location or 'remote'}"),
        "relocation": ("loc_relocation", f"location: {o.location}"),
        "outside_us": ("loc_outside_us", f"location: {o.location}"),
    }.get(loc, ("", "")))
    top = salary_max(o.salary or "")
    if top and top >= anchor and "$" in (o.salary or ""):
        hits.append(("posted_clears_anchor", f"posted: {o.salary}"))
    if age is not None and age <= FRESH_DAYS:
        hits.append(("fresh", f"updated {o.updated}"))
    if o.new:
        hits.append(("new", f"first seen {o.first_seen}"))
    out = []
    for key, src in hits:
        tpl = cat.get(key) if key else None
        if tpl is not None:
            out.append(tpl.model_copy(update={"source": src}))
    return out


_PRINCIPAL = re.compile(r"\bprincipal\b", re.I)
_ABOVE_PRINCIPAL = re.compile(r"\b(distinguished|fellow)\b", re.I)


def comp_estimate(
    title: str, level: str, ladder: dict[str, Any] | None, anchor: int,
) -> CompEstimate | None:
    """The likely rung for this title on the company's ladder (`ladder` = {levels, bands,
    attribution, location} with `bands` from `comp.band_rows`). None when there is no ladder, no
    band row to anchor on, or the title names no level."""
    if not ladder or level == "none":
        return None
    levels = ladder.get("levels") or []
    bands = ladder.get("bands") or {}
    codes = [str(lv["level"]) for lv in levels]
    staff = bands.get("staff")
    principal = bands.get("principal")
    if not staff and not principal:
        return None
    note = ""
    basis = ""
    top = principal or staff
    assert top is not None  # one of the two exists (checked above)
    if _ABOVE_PRINCIPAL.search(title):
        want = "above-principal"
        idx = codes.index(top["level"]) + 1
        basis = f"one above {'principal' if principal else 'staff'} ({top['basis']})"
        if idx >= len(codes):
            idx, note = codes.index(top["level"]), "no rung above it on the ladder; the top band shown"
    elif _PRINCIPAL.search(title):
        want = "principal"
        if principal:
            idx, basis = codes.index(principal["level"]), principal["basis"]
        else:
            idx, basis = codes.index(top["level"]), top["basis"]
            note = "no Principal rung on the ladder; Staff shown"
    elif level in ("in_band", "lead"):
        want = "staff"
        row = staff or top
        idx, basis = codes.index(row["level"]), row["basis"]
        if level == "lead":
            note = "a leadership seat; the IC Staff rung shown (manager ladders differ)"
        if not staff:
            note = (note + " · " if note else "") + "no Staff rung; Principal shown"
    else:  # below the band: the rung under staff
        want = "below"
        if not staff:
            return None
        si = codes.index(staff["level"])
        if si == 0:
            return None
        idx, basis = si - 1, f"one below staff ({staff['basis']})"
    lv = levels[idx]
    lo = levels[idx - 1] if idx > 0 else None
    hi = levels[idx + 1] if idx + 1 < len(levels) else None
    med = int(lv["median_tc"])
    return CompEstimate(
        level=str(lv["level"]), band=want, basis=basis, median_tc=med,
        low_level=str(lo["level"]) if lo else None, low_tc=int(lo["median_tc"]) if lo else None,
        high_level=str(hi["level"]) if hi else None, high_tc=int(hi["median_tc"]) if hi else None,
        ilvl=round((med / anchor - 1) * 100) if anchor else None, note=note,
        location=str(ladder.get("location") or "united-states"),
        attribution=str(ladder.get("attribution") or ""),
    )


def ladder_index(role_hunt: Path, pool: dict[str, Any], anchor: int) -> dict[str, dict[str, Any]]:
    """slug → {levels, bands, attribution, location} for every company in the pool with a cached US
    software-engineer ladder (the Ladders tab's band rows: levels-map.yaml → title → survey → YOE →
    anchor crossing). Disk only."""
    from harness.career.comp import (
        DEFAULT_FAMILY,
        DEFAULT_LOCATION,
        band_rows,
        cached_ladder,
        load_levels_map,
        survey_band_labels,
    )

    levels_map = load_levels_map(role_hunt)
    survey = survey_band_labels(role_hunt)
    out: dict[str, dict[str, Any]] = {}
    for r in [*(pool.get("rows") or []), *(pool.get("screen_gated") or [])]:
        slug = str(r.get("slug") or "")
        if not slug or slug in out:
            continue
        lad = cached_ladder(role_hunt, slug, DEFAULT_FAMILY, DEFAULT_LOCATION)
        if lad is None:
            continue
        # a thin page (aggregates only) still carries the company median — context for the card
        levels = [lv.model_dump() if hasattr(lv, "model_dump") else dict(lv) for lv in lad.levels]
        out[slug] = {
            "levels": levels,
            "bands": band_rows(levels, band_words=["staff", "principal"], mapped=levels_map.get(slug),
                               survey=survey.get(slug), anchor=anchor) if levels else {},
            "attribution": lad.attribution, "location": lad.location, "median_tc": lad.median_tc,
        }
    return out


def levels_aliases(role_hunt: Path, scans: list[CompanyScan]) -> dict[str, str]:
    """slugify(scan company) → its levels.fyi slug, through the levels index (the same resolver the
    company profiles use). Empty when no index is built."""
    from harness.career.levels_index import load_index, resolve_slug_in_index

    index = load_index(role_hunt)
    if not index:
        return {}
    out: dict[str, str] = {}
    for sc in scans:
        exact, candidates = resolve_slug_in_index(index, sc.company)
        hit = exact or (candidates[0] if len(candidates) == 1 else None)  # one candidate = the name
        if hit and hit != slugify(sc.company):
            out[slugify(sc.company)] = hit
    return out


def fit_class(lane: str, level: str, loc: str) -> str:
    if lane == OFF or loc == "outside_us":
        return "off-lane"
    if lane == IN and level in ("in_band", "lead") and loc in ("home", "remote_us", "unknown"):
        return "on-profile"
    return "adjacent"


def _company_index(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for r in [*(pool.get("rows") or []), *(pool.get("screen_gated") or [])]:
        for key in (r.get("watchlist_name"), r.get("company"), r.get("slug")):
            if key:
                idx.setdefault(slugify(str(key)), r)
    return idx


def build_roles(
    scans: list[CompanyScan],
    *,
    as_of: str,
    pool: dict[str, Any],
    anchor: int,
    bands: dict[str, float],
    cat: dict[str, Modifier],
    home: list[str],
    role_hunt: Path,
    today: date,
    ladders: dict[str, dict[str, Any]] | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[list[RoleItem], list[RoleItem]]:
    """(in-play items, screen-gated items) — pure over its inputs; sorted fit → new → ilvl."""
    comp = _company_index(pool)
    items: list[RoleItem] = []
    gated: list[RoleItem] = []
    for s in scans:
        # the scan's company name → the pool/levels slug ("Grafana Labs" → grafana) when they differ
        prow = comp.get(slugify(s.company)) or comp.get((aliases or {}).get(slugify(s.company), ""))
        slug = str(prow.get("slug")) if prow else slugify(s.company)
        profile = role_hunt / "companies" / f"{slugify(s.company)}.md"
        for o in s.matched:
            shape = shape_of(o.title, o.department)
            level = level_class(o.title)
            loc = location_class(o, home)
            age = days_old(o.updated, today)
            it = RoleItem(
                company=s.company, slug=slug, title=o.title.strip(), url=o.url, location=o.location,
                location_class=loc, salary=o.salary or "", updated=o.updated, days_old=age,
                first_seen=o.first_seen, new=bool(o.new), shape=shape, lane=lane_of(shape),
                level=level, fit=fit_class(lane_of(shape), level, loc),
                doc=(prow or {}).get("doc"),
                profile=f"role-hunt/companies/{profile.name}" if profile.exists() else None,
            )
            if prow and prow.get("screened_out"):
                it.screened_out, it.screen_flag = True, str(prow.get("screen_flag", "none"))
                gated.append(it)
                continue
            if prow:
                it.screen_flag = str(prow.get("screen_flag", "none"))
            mods = _mods_for(o, shape=shape, level=level, loc=loc, age=age, anchor=anchor, cat=cat)
            it.modifiers, it.modifier_sum = mods, sum(m.value for m in mods)
            company = prow.get("final_ilvl") if prow else None
            if company is not None:
                it.company_ilvl, it.company_rarity = int(company), prow.get("rarity") if prow else None
            # the base: the role's own rung on the company ladder when there is one (comp drives the
            # item level), carrying the company's named modifiers; else the company item as a whole
            ladder = (ladders or {}).get(slug)
            it.comp = comp_estimate(o.title, level, ladder, anchor)
            if ladder and ladder.get("median_tc"):
                it.company_median_tc = int(ladder["median_tc"])
            if it.comp is not None and it.comp.ilvl is not None:
                it.company_mods = int(prow.get("modifier_sum") or 0) if prow else 0
                it.base_ilvl, it.base_source = it.comp.ilvl + it.company_mods, "level ladder"
            elif company is not None:
                it.base_ilvl, it.base_source = int(company), "company item"
            if it.base_ilvl is not None:
                it.final_ilvl = it.base_ilvl + it.modifier_sum
                it.rarity = classify(it.final_ilvl, bands)
            items.append(it)
    items.sort(key=lambda r: (
        FIT_ORDER.index(r.fit), not r.new, r.final_ilvl is None, -(r.final_ilvl or 0),
        -(salary_max(r.salary) or 0), r.company, r.title,
    ))
    return items, gated


def company_counts(items: list[RoleItem]) -> list[dict[str, Any]]:
    """Per company: openings · on-profile · new — the Pool tile's openings chip and the Openings
    company filter. Most on-profile first."""
    by: dict[str, dict[str, Any]] = {}
    for r in items:
        c = by.setdefault(
            r.slug, {"company": r.company, "slug": r.slug, "openings": 0, "on_profile": 0, "new": 0}
        )
        c["openings"] += 1
        c["on_profile"] += r.fit == "on-profile"
        c["new"] += r.new
    return sorted(by.values(), key=lambda c: (-c["on_profile"], c["company"]))


def attach_openings(tracker: Path, role_hunt: Path, pool: dict[str, Any]) -> dict[str, Any]:
    """Stamp each merged-pool row with `openings: {openings, on_profile, new}` from the latest scan
    (no modifiers needed — the counts are fit classes). A missing scan leaves the pool untouched."""
    scans, as_of = CareerService(tracker, role_hunt_root=role_hunt).latest_scan()
    if not scans:
        return pool
    items, _ = build_roles(
        scans, as_of=as_of or "", pool=pool, anchor=anchor_tc(), bands=rarity_bands(role_hunt),
        cat={}, home=home_area(), role_hunt=role_hunt, today=date.today(),
    )
    counts = {c["slug"]: c for c in company_counts(items)}
    for r in [*(pool.get("rows") or []), *(pool.get("screen_gated") or [])]:
        c = counts.get(str(r.get("slug")))
        if c:
            r["openings"] = {k: c[k] for k in ("openings", "on_profile", "new")}
    pool["openings_as_of"] = as_of or ""
    return pool


def roles_payload(
    tracker: Path,
    role_hunt: Path,
    *,
    new_only: bool = False,
    location: str = "all",
    company: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """The `hn career roles --json` contract (RolesReport). `location`: all | us (drops outside-US)
    | home (home area + remote US + unknown). `company`: a slug or name; "auto"/None = every company."""
    svc = CareerService(tracker, role_hunt_root=role_hunt)
    scans, as_of = svc.latest_scan()
    today = today or date.today()
    pool = merge_pool(tracker, role_hunt)
    anchor = anchor_tc()
    bands = rarity_bands(role_hunt)
    items, gated = build_roles(
        scans, as_of=as_of or "", pool=pool, anchor=anchor, bands=bands,
        cat=load_role_catalogue(role_hunt), home=home_area(), role_hunt=role_hunt, today=today,
        ladders=ladder_index(role_hunt, pool, anchor), aliases=levels_aliases(role_hunt, scans),
    )
    every = items
    if company and company != "auto":
        want = slugify(company)
        items = [r for r in items if want in (r.slug, slugify(r.company))]
        gated = [r for r in gated if want in (r.slug, slugify(r.company))]
    if location == "us":
        items = [r for r in items if r.location_class != "outside_us"]
    elif location == "home":
        items = [r for r in items if r.location_class in ("home", "remote_us", "unknown")]
    if new_only:
        items = [r for r in items if r.new]
    return RolesReport(
        as_of=as_of or "",
        anchor=anchor,
        bands=bands,
        filters=RoleFilters(new=new_only, location=location, company=company or "auto"),
        counts=RoleCounts(
            total=len(every), shown=len(items), new=sum(r.new for r in every),
            fit={f: sum(r.fit == f for r in every) for f in FIT_ORDER},
        ),
        rows=items,
        screen_gated=gated,
        companies=[RoleCompany(**c) for c in company_counts(every)],
        constitution=CONSTITUTION
        + " Roles rank by fit (on-profile · adjacent · off-lane), then new, then item level.",
    ).model_dump()
