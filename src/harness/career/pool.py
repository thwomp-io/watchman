"""The pool — the levels.fyi leaderboard cut as a survey of companies NOT yet on the watchlist,
read through the ITEM MODEL. The goal: a ranked list of top-paying companies, refreshed over time,
to browse when widening the search beyond the watchlist — with a transparent modifier layer on top
of the comp base (like item enchantments that add or subtract from item level), so every score
shows the judgment that moved it.

The split: the REGISTRY is authored judgment
(`role-hunt/reference/pool-enchants.yaml` = the catalogue; `pool-items.yaml` = per-company applied
modifiers for the judged tiers), the VERB is arithmetic, and the surface shows the arithmetic in
the open:

- base ilvl  = (median TC / anchor − 1) × 100 — the anchor is the overlay's
  `career.comp_anchor.anchor_tc` (the anchor you negotiate from);
- machine modifiers are computed HERE from data on disk (the scan twin, the watchlist, the finance
  bench, the SEC ticker map) and never hand-entered; agent/operator modifiers come from pool-items.yaml;
- set bonuses when ≥N ENCHANTMENTS of one family apply (debuffs never complete a set);
- the cap (an operator choice): the final rarity may sit at most one tier from the base rarity — the verb
  clamps the final ilvl at the adjacent tier's boundary and says "capped";
- rarity (poor/common/rare/epic/legendary) is classified engine-side from config bands
  (`watchlist.yml pool_rarity:` floors; `DEFAULT_BANDS` are the default floors) — every surface agrees;
- a screen OUT flag (`watchlist.yml pool_flags:`) moves the company to the screen-gated pane with NO
  arithmetic (a screened-out company stays visible and marked, so the survey still shows the whole
  landscape); gray flags stay in-play, shown;
- the agency-violation compound is never a number: every row says "unknown until the loop".

Never writes the watchlist (the graduation gate): the pool is a survey; the watchlist row is the
operator's nod. Output carries the constitutional line — a lens, never sole reasoning.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from harness.career.geo import home_area, word_in
from harness.career.levels_index import load_index, resolve_slug_in_index
from harness.career.providers.levels import (
    ATTRIBUTION,
    fetch_levels_html,
    leaderboard_url,
    parse_leaderboard_html,
)
from harness.career.service import CareerService, slugify
from harness.errors import ProviderError
from harness.paths import cache_file
from harness.yamlio import load_yaml

CONSTITUTION = "a lens, never sole reasoning — comp base + named modifiers; fit is interview-evaluated."
AGENCY_LINE = "unknown until the loop"

LEVELS = {
    "all": "All-Levels",
    "entry": "Entry-Level-Engineer",
    "mid": "Software-Engineer",
    "senior": "Senior-Engineer",
    "staff": "Staff-Engineer",
    "principal": "Principal-Engineer",
}
# Built-in `--location` aliases (neutral). Named metros come from the overlay:
# `career.comp.locations: {<alias>: "<kind>/<Geo>"}` (e.g. `sf: region/San-Francisco-Bay-Area`).
LOCATIONS: dict[str, tuple[str, str]] = {
    "us": ("country", "United-States"),
}


def locations() -> dict[str, tuple[str, str]]:
    """`--location` aliases: the built-ins plus the overlay's `career.comp.locations`
    (`{alias: "<kind>/<Geo>"}`); a malformed entry is skipped."""
    from harness.settings import overlay_get

    out = dict(LOCATIONS)
    raw = overlay_get("career", "comp", "locations", default=None)
    if isinstance(raw, dict):
        for alias, spec in raw.items():
            kind, _, geo = str(spec).partition("/")
            if kind and geo:
                out[str(alias).lower()] = (kind, geo)
    return out


UNRANKED = "unranked"  # the rarity of an item with no band-row base: shown, never scored
RARITY_ORDER = ("poor", "common", "rare", "epic", "legendary")
# Floors, in ilvl points (% above the anchor) — the default floors; the
# watchlist's `pool_rarity:` block overrides by tier name. `poor` is everything below `common`.
DEFAULT_BANDS: dict[str, float] = {"common": -22.0, "rare": 0.0, "epic": 20.0, "legendary": 60.0}
CACHE_TTL_S = 24 * 3600


class Modifier(BaseModel):
    key: str
    name: str
    value: int
    family: str
    how: str  # machine | agent | the `by:` value of the pool-items.yaml entry (e.g. operator)
    source: str = ""


class SetBonus(BaseModel):
    family: str
    name: str
    count: int
    have: int
    bonus: int


class PoolRow(BaseModel):
    rank: int
    company: str
    slug: str
    label: str = ""
    median_tc: int
    base: int | None = None
    stock: int | None = None
    bonus: int | None = None
    on_watchlist: bool = False
    watchlist_name: str = ""
    ticker: str = ""
    bench: bool = False
    screen_flag: str = "none"
    screened_out: bool = False
    base_ilvl: int | None = None
    base_rarity: str | None = None
    modifiers: list[Modifier] = Field(default_factory=list)
    set_bonuses: list[SetBonus] = Field(default_factory=list)
    modifier_sum: int = 0
    final_ilvl: int | None = None
    rarity: str | None = None
    capped: bool = False
    capped_at: str | None = None
    agency: str = AGENCY_LINE
    doc: str | None = None  # the vault research entry when one exists (the tile's click target)
    source: str = "leaderboard"  # leaderboard | ladder (the appearance the base number came from)
    cut: str = ""  # "<level>/<geo>" of that appearance
    basis: str = ""  # how the base number was chosen (merged pools; empty on a single cut)
    appearances: list[dict[str, Any]] = Field(default_factory=list)


# ————— config + registries —————


from harness.career.comp import anchor_tc  # noqa: E402 — the shared anchor accessor


def _watchlist_raw(role_hunt: Path) -> dict[str, Any]:
    p = role_hunt / "watchlist.yml"
    if not p.exists():
        return {}
    try:
        raw = load_yaml(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProviderError(f"watchlist YAML parse failed: {e}") from e
    return raw if isinstance(raw, dict) else {}


def rarity_bands(role_hunt: Path) -> dict[str, float]:
    bands = dict(DEFAULT_BANDS)
    cfg = _watchlist_raw(role_hunt).get("pool_rarity") or {}
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k in bands:
                try:
                    bands[k] = float(v)
                except (TypeError, ValueError):
                    continue
    return bands


def pool_flags(role_hunt: Path) -> dict[str, str]:
    cfg = _watchlist_raw(role_hunt).get("pool_flags") or {}
    return {str(k): str(v) for k, v in cfg.items()} if isinstance(cfg, dict) else {}


def screen_out(role_hunt: Path) -> frozenset[str]:
    """Flag values that screen a company out of ranking (watchlist `pool_screen_out:`); none by default."""
    cfg = _watchlist_raw(role_hunt).get("pool_screen_out") or []
    return frozenset(str(v) for v in cfg) if isinstance(cfg, list) else frozenset()


def ladder(bands: dict[str, float]) -> list[tuple[float, str]]:
    """Highest floor first (a rarity ladder), from config."""
    return sorted(((v, k) for k, v in bands.items()), reverse=True)


def classify(ilvl: float, bands: dict[str, float]) -> str:
    for floor, name in ladder(bands):
        if ilvl >= floor:
            return name
    return "poor"


def tier_floor(name: str, bands: dict[str, float]) -> float | None:
    return bands.get(name)


def load_catalogue(role_hunt: Path) -> tuple[dict[str, Modifier], dict[str, dict[str, Any]]]:
    """(key → Modifier template, family → set-bonus spec) from pool-enchants.yaml."""
    p = role_hunt / "reference" / "pool-enchants.yaml"
    if not p.exists():
        raise ProviderError(
            f"no modifier catalogue at {p} — the pool needs role-hunt/reference/pool-enchants.yaml"
        )
    raw = load_yaml(p.read_text(encoding="utf-8")) or {}
    cat: dict[str, Modifier] = {}
    for group in ("enchantments", "debuffs"):
        for key, spec in (raw.get(group) or {}).items():
            cat[str(key)] = Modifier(
                key=str(key),
                name=str(spec.get("name", key)),
                value=int(spec.get("value", 0)),
                family=str(spec.get("family", "")),
                how=str(spec.get("how", "agent")),
            )
    sets = {str(k): dict(v) for k, v in (raw.get("set_bonuses") or {}).items()}
    return cat, sets


def load_items(role_hunt: Path) -> dict[str, dict[str, Any]]:
    p = role_hunt / "reference" / "pool-items.yaml"
    if not p.exists():
        return {}
    raw = load_yaml(p.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


# ————— the machine tier —————

_SUFFIX = {
    "inc",
    "corp",
    "corporation",
    "co",
    "ltd",
    "plc",
    "holdings",
    "holding",
    "group",
    "llc",
    "sa",
    "nv",
    "the",
    "company",
    "limited",
    "technologies",
    "technology",
    "incorporated",
    "global",
}


def _norm(name: str) -> str:
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if t not in _SUFFIX]
    return "-".join(toks)


def ticker_map() -> dict[str, str]:
    """Normalized company name → ticker from the bundled SEC filer phonebook (a US public filer =
    liquid, listed equity — the machine read of 'liquid equity')."""
    import harness.finance as fin

    p = Path(fin.__file__).parent / "config" / "company_tickers.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in raw.values():
        title, ticker = str(entry.get("title", "")), str(entry.get("ticker", ""))
        key = _norm(title)
        if key and key not in out:  # first listing wins (the primary class)
            out[key] = ticker
    return out


def bench_tickers(tracker: Path) -> set[str]:
    """Tickers with a research dir anywhere under finance/research (positions / candidates/<sector> /
    compares / themes) — 'researched on the bench'."""
    root = tracker / "finance" / "research"
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for p in root.rglob("*"):
        if p.is_dir() and re.fullmatch(r"[A-Z]{1,6}(\.[A-Z])?", p.name):
            out.add(p.name)
    return out


_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([kKmM])?")


def salary_max(text: str) -> int | None:
    vals = []
    for num, unit in _MONEY.findall(text or ""):
        n = float(num.replace(",", ""))
        if unit.lower() == "k":
            n *= 1_000
        elif unit.lower() == "m":
            n *= 1_000_000
        vals.append(int(n))
    return max(vals) if vals else None


def machine_modifiers(
    row: dict[str, Any],
    *,
    openings: list[Any],
    anchor: int,
    ticker: str,
    on_bench: bool,
    cat: dict[str, Modifier],
) -> list[Modifier]:
    """The verb's own reads of data on disk — never hand-entered. Each applied key must exist in
    the catalogue (its value/name/family come from there); a key the catalogue lacks is skipped."""
    hits: list[tuple[str, str]] = []
    locs = [(getattr(o, "location", "") or "").lower() for o in openings]
    if any(
        getattr(o, "remote", None) is True or "remote" in loc for o, loc in zip(openings, locs, strict=True)
    ):
        hits.append(("remote_first", "scan twin: a matched opening is remote"))
    home = home_area()
    if home and any(word_in(loc, home) for loc in locs):
        hits.append(("home_area_priority", "scan twin: a matched opening is in the home area"))
    staffplus = [
        o for o in openings if re.search(r"\b(staff|principal)\b", (getattr(o, "title", "") or "").lower())
    ]
    if len(staffplus) >= 2:
        hits.append(("level_latitude", f"scan twin: {len(staffplus)} Staff+ seats open"))
    posted = [salary_max(getattr(o, "salary", "") or "") for o in openings]
    if any(v and v >= anchor for v in posted):
        hits.append(("base_clears_anchor", "scan twin: a posted range reaches the anchor"))
    if ticker:
        hits.append(("liquid_equity", f"SEC filer map: {ticker}"))
    if on_bench:
        hits.append(("finance_researched", f"finance/research: {ticker}"))
    out = []
    for key, src in hits:
        tpl = cat.get(key)
        if tpl and tpl.how == "machine":
            out.append(tpl.model_copy(update={"source": src}))
    return out


def judged_modifiers(slug: str, items: dict[str, dict[str, Any]], cat: dict[str, Modifier]) -> list[Modifier]:
    entry = items.get(slug)
    if not entry:
        return []
    by = str(entry.get("by", "agent"))
    src = str(entry.get("source", "")) + (f" ({entry.get('as_of')})" if entry.get("as_of") else "")
    out = []
    for key in entry.get("modifiers") or []:
        tpl = cat.get(str(key))
        if tpl and tpl.how != "machine":
            out.append(tpl.model_copy(update={"how": by, "source": src}))
    return out


# ————— the arithmetic —————


def apply_item_model(
    row: PoolRow,
    *,
    anchor: int,
    bands: dict[str, float],
    mods: list[Modifier],
    sets: dict[str, dict[str, Any]],
) -> PoolRow:
    """Base ilvl → modifiers → set bonuses → the one-tier cap → rarity. Pure; pinned by tests."""
    base = round((row.median_tc / anchor - 1) * 100)
    row.base_ilvl = base
    row.base_rarity = classify(base, bands)
    row.modifiers = mods
    counts: dict[str, int] = {}
    for m in mods:
        if m.value > 0:
            counts[m.family] = counts.get(m.family, 0) + 1
    bonuses = []
    for fam, spec in sets.items():
        need = int(spec.get("count", 0))
        have = counts.get(fam, 0)
        if need and have >= need:
            bonuses.append(
                SetBonus(
                    family=fam,
                    name=str(spec.get("name", fam)),
                    count=need,
                    have=have,
                    bonus=int(spec.get("bonus", 0)),
                )
            )
    row.set_bonuses = bonuses
    row.modifier_sum = sum(m.value for m in mods) + sum(b.bonus for b in bonuses)
    final = base + row.modifier_sum
    bi = RARITY_ORDER.index(row.base_rarity)
    lo_tier, hi_tier = max(bi - 1, 0), min(bi + 1, len(RARITY_ORDER) - 1)
    # clamp: the lowest ilvl of the tier below is its floor; the highest of the tier above is one
    # below the NEXT tier's floor (an integer ilvl).
    lo_floor = tier_floor(RARITY_ORDER[lo_tier], bands)
    nxt = RARITY_ORDER[hi_tier + 1] if hi_tier + 1 < len(RARITY_ORDER) else None
    nxt_floor = tier_floor(nxt, bands) if nxt is not None else None
    hi_ceiling = int(nxt_floor) - 1 if nxt_floor is not None else None
    if lo_floor is not None and final < lo_floor:
        final, row.capped, row.capped_at = int(lo_floor), True, RARITY_ORDER[lo_tier]
    elif hi_ceiling is not None and final > hi_ceiling:
        final, row.capped, row.capped_at = int(hi_ceiling), True, RARITY_ORDER[hi_tier]
    row.final_ilvl = int(final)
    row.rarity = classify(final, bands)
    return row


# ————— the cut —————


def resolve_cut(
    family: str, level: str, location: str, index: dict[str, Any] | None
) -> tuple[str, str, str, str]:
    """(Family, Level, kind, Geo) in the site's URL grammar. Refuses loud when a form cannot be
    pinned (the index's leaderboard grammar is the authority when present)."""
    fam = "-".join(t.capitalize() for t in family.split("-"))
    lvl = LEVELS.get(level.lower())
    if not lvl:
        raise ProviderError(f"unknown --level {level!r}: {', '.join(LEVELS)}")
    loc = location.lower()
    aliases = locations()
    if loc in aliases:
        kind, geo = aliases[loc]
    elif "/" in location:
        kind, geo = location.split("/", 1)
    else:
        kind, geo = "", ""
        if index:
            lb = index.get("leaderboard", {}).get("geo", {})
            for k in ("region", "city", "country"):
                hit = next(
                    (g for g in lb.get(k, []) if g.lower() == loc or g.lower().replace("-", " ") == loc), None
                )
                if hit:
                    kind, geo = k, hit
                    break
        if not kind:
            raise ProviderError(
                f"cannot pin --location {location!r} to a leaderboard geo — use {' | '.join(aliases)}, "
                "a '<kind>/<Geo>' form (region/San-Francisco-Bay-Area), or a name from the index's "
                "leaderboard geos (named metros: career.comp.locations in the overlay)"
            )
    if index:
        lb = index.get("leaderboard", {})
        if lb.get("families") and fam not in lb["families"]:
            raise ProviderError(
                f"{fam!r} is not a leaderboard family in the index: {', '.join(lb['families'])}"
            )
        if lb.get("levels", {}).get(fam) and lvl not in lb["levels"][fam]:
            raise ProviderError(
                f"{lvl!r} is not a level the index lists for {fam}: {', '.join(lb['levels'][fam])}"
            )
    return fam, lvl, kind, geo


def cut_key(fam: str, lvl: str, kind: str, geo: str) -> str:
    return f"{fam}_{lvl}_{kind}_{geo}".lower()


def fetch_cut_html(url: str, key: str, *, refresh: bool = False, fetch: Any = None) -> tuple[str, bool]:
    """(html, from_cache) — the page is reused for 24h unless `refresh` (polite: one page per call)."""
    fetch = fetch or fetch_levels_html
    p = cache_file("levels", "leaderboard", f"{key}.html")
    if not refresh and p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
        return p.read_text(encoding="utf-8"), True
    html = fetch(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return html, False


class PoolContext:
    """Everything a pool row joins to, loaded ONCE per run: the anchor, the rarity bands, the screen
    flags, the modifier catalogue + applied items, the watchlist (name ↔ slug), the scan twin's
    openings per company, the SEC ticker map, the finance bench."""

    def __init__(self, tracker: Path, role_hunt: Path, *, index: dict[str, Any] | None) -> None:
        self.tracker, self.role_hunt, self.index = tracker, role_hunt, index
        self.anchor = anchor_tc()
        self.bands = rarity_bands(role_hunt)
        self.flags = pool_flags(role_hunt)
        self.screen_out = screen_out(role_hunt)
        self.cat, self.sets = load_catalogue(role_hunt)
        self.items = load_items(role_hunt)
        svc = CareerService(tracker, role_hunt_root=role_hunt)
        try:
            wl_names = [c.name for c in svc.load().companies]
        except ProviderError:
            wl_names = []
        self.wl_slug: dict[str, str] = {}
        for n in wl_names:
            hit = resolve_slug_in_index(index, n)[0] if index else None
            self.wl_slug[hit or slugify(n)] = n
        try:
            scans, _ = svc.latest_scan()
        except ProviderError:
            scans = []
        self.openings_by = {s.company: s.matched for s in scans}
        self.tickers = ticker_map()
        self.bench = bench_tickers(tracker)


def pool_context(tracker: Path, role_hunt: Path, *, index: dict[str, Any] | None = None) -> PoolContext:
    return PoolContext(tracker, role_hunt, index=index if index is not None else load_index(role_hunt))


def make_row(
    ctx: PoolContext,
    r: dict[str, Any],
    *,
    source: str,
    cut: str,
    level_label: str,
    basis: str = "",
    appearances: list[dict[str, Any]] | None = None,
    ranked: bool = True,
) -> PoolRow:
    """One pool item from a leaderboard row or a ladder band row: the corpus joins, the screen gate,
    then the item model (machine + judged modifiers, set bonuses, the cap, rarity). `ranked=False`
    = the base number is NOT the applicant's band (a thin ladder's all-levels median, a ladder with
    no band row, a leaderboard row at another level): the item is UNRANKED — the median shows as
    context, no ilvl, no rarity, no arithmetic. Unknown is not poor (otherwise thin ladders get
    classified off a number the engine itself labelled not-the-band)."""
    slug = r["slug"]
    wl_name = ctx.wl_slug.get(slug, "")
    ticker = ctx.tickers.get(_norm(r["company"]), "") or (
        ctx.tickers.get(_norm(wl_name), "") if wl_name else ""
    )
    flag = ctx.flags.get(slug, "none")
    row = PoolRow(
        doc=f"role-hunt/companies/{slug}.md"
        if (ctx.role_hunt / "companies" / f"{slug}.md").exists()
        else None,
        rank=int(r.get("rank") or 0),
        company=r["company"],
        slug=slug,
        label=level_label,
        median_tc=int(r["total"]),
        base=r.get("base"),
        stock=r.get("stock"),
        bonus=r.get("bonus"),
        on_watchlist=bool(wl_name),
        watchlist_name=wl_name,
        ticker=ticker,
        bench=bool(ticker and ticker in ctx.bench),
        screen_flag=flag,
        source=source,
        cut=cut,
        basis=basis,
        appearances=appearances or [],
    )
    if flag in ctx.screen_out:
        row.screened_out = True
        return row
    if not ranked:
        row.rarity = UNRANKED
        return row
    mods = machine_modifiers(
        r,
        openings=ctx.openings_by.get(wl_name, []),
        anchor=ctx.anchor,
        ticker=ticker,
        on_bench=row.bench,
        cat=ctx.cat,
    )
    mods += judged_modifiers(slug, ctx.items, ctx.cat)
    return apply_item_model(row, anchor=ctx.anchor, bands=ctx.bands, mods=mods, sets=ctx.sets)


def display_name(slug: str, raw: str, wl_name: str) -> str:
    """A ladder-only item's name: the watchlist row's spelling > the name on file when it is a real
    name > the slug titleized (a manually fetched ladder is cached under the slug it was fetched by —
    `temporal-technologies` read as the company before this rule)."""
    if wl_name:
        return wl_name
    slug_shaped = not raw or raw == slug or (raw == raw.lower() and " " not in raw)
    if not slug_shaped:
        return raw  # a real name — "Bank of America" slugifies to its slug and is still a name
    return " ".join(w.capitalize() for w in slug.split("-"))


def _short_geo(geo: str) -> str:
    """A cut's display label: `us`, else the first configured alias whose geo matches (or whose
    name appears in the geo slug), else the geo itself."""
    g = geo.lower()
    if "united-states" in g:
        return "us"
    for alias, (_kind, ageo) in locations().items():
        if alias != "us" and (ageo.lower() == g or alias.lower() in g):
            return alias
    return g


def merge_pool(tracker: Path, role_hunt: Path) -> dict[str, Any]:
    """The whole pool: the union of every persisted survey (the newest per cut) and
    every cached ladder, one item per company. The base number is the BEST for the applicant's
    band: a leaderboard row at the band level (the Staff cut) > a ladder's band row (mapped > title
    > survey > yoe > anchor-heuristic, > a leaderboard row at another level
    (flagged). Every appearance rides along; the item model runs unchanged. A read over persisted
    files only — no network."""
    from harness.career.comp import cached_ladders, ladders_contract

    index = load_index(role_hunt)
    ctx = pool_context(tracker, role_hunt, index=index)
    band_words = [str(b).lower() for b in (overlay_get_bands())]
    primary_band = band_words[0] if band_words else "staff"
    # (1) the newest survey per cut key
    newest: dict[str, Path] = {}
    for p in sorted(pool_dir(role_hunt).glob("*.json")):
        newest[p.stem.split("-", 3)[-1]] = p
    appearances: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    survey_files: list[str] = []
    attributions: list[str] = []  # the merged view's attribution follows its DATA (a demo pack is fiction)
    for p in newest.values():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        survey_files.append(p.name)
        attributions.append(str(d.get("attribution") or ""))
        level = str(d.get("cut", {}).get("level", "")).lower()
        band = next((b for b in ("staff", "principal", "senior") if b in level), level.split("-")[0] or "all")
        cut = _short_geo(str(d.get("cut", {}).get("geo", "")))
        for r in d.get("rows", []) + d.get("screen_gated", []):
            names.setdefault(r["slug"], r["company"])
            appearances.setdefault(r["slug"], []).append(
                {
                    "source": "leaderboard",
                    "cut": cut,
                    "level": band,
                    "label": r.get("label") or "",
                    "median_tc": r["median_tc"],
                    "rank": r.get("rank"),
                    "base": r.get("base"),
                    "stock": r.get("stock"),
                    "bonus": r.get("bonus"),
                    "file": p.name,
                    "basis": "leaderboard",
                }
            )
    # (2) every cached ladder's band row (the ladders contract carries the pick + basis)
    contract = ladders_contract(role_hunt, cached_ladders(role_hunt))
    for lad in contract["ladders"]:
        names.setdefault(lad["slug"], lad["company"])
        attributions.append(str(lad.get("attribution") or ""))
        for name, spec in (lad.get("bands") or {}).items():
            lv = next((x for x in lad["levels"] if x["level"] == spec["level"]), None)
            if not lv:
                continue
            appearances.setdefault(lad["slug"], []).append(
                {
                    "source": "ladder",
                    "cut": _short_geo(lad["location"]),
                    "level": name,
                    "label": lv["level"],
                    "median_tc": lv["median_tc"],
                    "rank": None,
                    "base": (lad.get("mix") or {}).get("base"),
                    "stock": (lad.get("mix") or {}).get("stock"),
                    "bonus": (lad.get("mix") or {}).get("bonus"),
                    "file": f"comp/{lad['slug']}.json",
                    "basis": spec["basis"],
                }
            )
        if not lad.get("bands") and lad.get("median_tc"):
            appearances.setdefault(lad["slug"], []).append(
                {
                    "source": "ladder",
                    "cut": _short_geo(lad["location"]),
                    "level": "all",
                    "label": "",
                    "median_tc": lad["median_tc"],
                    "rank": None,
                    "base": None,
                    "stock": None,
                    "bonus": None,
                    "file": f"comp/{lad['slug']}.json",
                    "basis": "ladder-median",
                }
            )

    # (3) one item per company: the best-for-band appearance
    def rank_of(a: dict[str, Any]) -> tuple[int, int]:
        if a["source"] == "leaderboard" and a["level"] == primary_band:
            return (0, 0 if a["cut"] == "us" else 1)
        if a["source"] == "ladder" and a["level"] == primary_band:
            return (
                1,
                ["mapped", "title", "survey", "yoe", "anchor-heuristic", "next-above-staff"].index(a["basis"])
                if a["basis"] in ("mapped", "title", "survey", "yoe", "anchor-heuristic", "next-above-staff")
                else 9,
            )
        if a["source"] == "leaderboard":
            return (2, 0)
        return (3, 0)

    rows: list[PoolRow] = []
    gated: list[PoolRow] = []
    for slug, apps in appearances.items():
        apps.sort(key=rank_of)
        best = apps[0]
        ranked = best["level"] == primary_band
        basis = (
            f"{best['source']} {best['level']}"
            + (f" ({best['basis']})" if best["source"] == "ladder" else "")
            + ("" if best["level"] == primary_band else f" — not the {primary_band} band")
        )
        r = {
            "slug": slug,
            "company": display_name(slug, names[slug], ctx.wl_slug.get(slug, "")),
            "rank": best["rank"] or 0,
            "total": best["median_tc"],
            "base": best["base"],
            "stock": best["stock"],
            "bonus": best["bonus"],
        }
        row = make_row(
            ctx,
            r,
            source=best["source"],
            cut=f"{best['level']}/{best['cut']}",
            level_label=best["label"],
            basis=basis,
            appearances=apps,
            ranked=ranked,
        )
        (gated if row.screened_out else rows).append(row)
    rows.sort(key=lambda x: (-(x.final_ilvl or -999), x.company))
    for i, x in enumerate(rows + gated, start=1):
        x.rank = i
    return {
        "cut": {
            "family": "software-engineer",
            "level": "all",
            "kind": "merged",
            "geo": "all",
            "url": "",
            "key": "merged",
            "page_geo": "",
            "page_level": "",
        },
        "merged": True,
        "sources": {"surveys": survey_files, "ladders": contract["count"], "band": primary_band},
        "fetched": "cache",
        "as_of": date.today().isoformat(),
        "anchor": ctx.anchor,
        "bands": ctx.bands,
        "rows": [x.model_dump() for x in rows],
        "screen_gated": [x.model_dump() for x in gated],
        "attribution": " · ".join(dict.fromkeys(a for a in attributions if a)) or ATTRIBUTION,
        "constitution": CONSTITUTION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


def overlay_get_bands() -> list[str]:
    from harness.settings import overlay_get

    b = overlay_get("career", "comp_anchor", "bands", default=["staff", "principal"])
    return [str(x) for x in (b if isinstance(b, list) else [b])]


def build_pool(
    tracker: Path,
    role_hunt: Path,
    *,
    family: str = "software-engineer",
    level: str = "staff",
    location: str = "us",
    refresh: bool = False,
    fetch: Any = None,
) -> dict[str, Any]:
    """The whole survey: fetch (or reuse) the cut, pin it, join every row to the corpus, run the item
    model, split the screen-gated pane. Returns the JSON contract the CLI / dashboard read."""
    index = load_index(role_hunt)
    fam, lvl, kind, geo = resolve_cut(family, level, location, index)
    url = leaderboard_url(fam, lvl, kind, geo)
    key = cut_key(fam, lvl, kind, geo)
    html, from_cache = fetch_cut_html(url, key, refresh=refresh, fetch=fetch)
    page = parse_leaderboard_html(html)
    want_geo = geo.replace("-", " ").lower()
    if page["canonical"] and page["canonical"].rstrip("/") != url.rstrip("/"):
        raise ProviderError(
            f"asked for {url} but the page's canonical cut is {page['canonical']} — refusing to relabel"
        )
    if page["geo"] and page["geo"].lower() != want_geo:
        raise ProviderError(f"asked for {geo!r} but the page says {page['geo']!r} — refusing to relabel")
    if not page["rows"]:
        raise ProviderError(f"no leaderboard rows parsed at {url} — the page shape may have changed")
    ctx = pool_context(tracker, role_hunt, index=index)
    rows: list[PoolRow] = []
    gated: list[PoolRow] = []
    for r in page["rows"]:
        row = make_row(ctx, r, source="leaderboard", cut=f"{lvl}/{geo}".lower(), level_label=r["label"])
        if row.screened_out:
            gated.append(row)
        else:
            rows.append(row)
    return {
        "cut": {
            "family": fam,
            "level": lvl,
            "kind": kind,
            "geo": geo,
            "url": url,
            "key": key,
            "page_geo": page["geo"],
            "page_level": page["level_label"],
        },
        "fetched": "cache" if from_cache else "live",
        "as_of": date.today().isoformat(),
        "anchor": ctx.anchor,
        "bands": ctx.bands,
        "rows": [x.model_dump() for x in rows],
        "screen_gated": [x.model_dump() for x in gated],
        "attribution": ATTRIBUTION,
        "constitution": CONSTITUTION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


# ————— persistence + the diff —————


def pool_dir(role_hunt: Path) -> Path:
    return role_hunt / "pool"


def prior_survey(role_hunt: Path, key: str, *, before: str | None = None) -> dict[str, Any] | None:
    files = sorted(pool_dir(role_hunt).glob(f"*-{key}.json"))
    if before:
        files = [f for f in files if f.name < f"{before}-{key}.json"]
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def latest_survey(role_hunt: Path, key: str | None = None) -> dict[str, Any]:
    """The newest persisted survey (optionally for one cut key) — the DASHBOARD's read: no network,
    the file `--write` produced. A named refusal when none exists (the Pool tab then says so)."""
    files = sorted(pool_dir(role_hunt).glob(f"*-{key}.json" if key else "*.json"))
    if not files:
        raise ProviderError(
            "no persisted pool survey in role-hunt/pool/ — run `hn career pool --write` (a cut is a survey "
            "only once written; the dashboard never fetches)"
        )
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProviderError(f"pool survey {files[-1]} is unreadable: {e}") from e
    if not isinstance(data, dict):
        raise ProviderError(f"pool survey {files[-1]} is not an object")
    data["survey_file"] = files[-1].name
    return data


def diff_surveys(new: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any] | None:
    if not old:
        return None
    n = {r["slug"]: r for r in new["rows"] + new["screen_gated"]}
    o = {r["slug"]: r for r in old.get("rows", []) + old.get("screen_gated", [])}
    return {
        "prior_as_of": old.get("as_of"),
        "new_entrants": [n[s]["company"] for s in n if s not in o],
        "dropped": [o[s]["company"] for s in o if s not in n],
        "rank_moves": [
            {"company": n[s]["company"], "from": o[s]["rank"], "to": n[s]["rank"]}
            for s in n
            if s in o and o[s]["rank"] != n[s]["rank"]
        ],
        "rarity_changes": [
            {"company": n[s]["company"], "from": o[s].get("rarity"), "to": n[s].get("rarity")}
            for s in n
            if s in o and o[s].get("rarity") != n[s].get("rarity")
        ],
    }


def survey_markdown(survey: dict[str, Any], diff: dict[str, Any] | None) -> str:
    c = survey["cut"]
    lines = [
        "---",
        "tags: [career, role-hunt, pool, levels-fyi, survey]",
        f"cut: {c['family']} / {c['level']} / {c['kind']} / {c['geo']}",
        f"as_of: {survey['as_of']}",
        "---",
        "",
        "# Pool — "
        + " · ".join(c[k].replace("-", " ") for k in ("family", "level", "geo"))
        + f" · {survey['as_of']}",
        "",
        f"> {survey['constitution']} Base ilvl = median TC vs the anchor (${survey['anchor']:,}); "
        "modifiers from `reference/pool-enchants.yaml` + `pool-items.yaml`; the final rarity sits at most "
        "one "
        "tier from the base (capped where it would not).",
        "",
        "| # | Company | Level | Median TC | Base | Mods | Final | Rarity | Watchlist | Bench | Flag |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in survey["rows"]:
        mods = ", ".join(f"{m['name']} {m['value']:+d}" for m in r["modifiers"]) or "—"
        sets = "".join(f", {b['name']} ({b['have']}/{b['count']}) +{b['bonus']}" for b in r["set_bonuses"])
        cap = " · capped" if r["capped"] else ""
        lines.append(
            f"| {r['rank']} | {r['company']} | {r['label'] or '—'} | ${r['median_tc']:,} | "
            f"{r['base_ilvl']:+d} | {mods}{sets} | {r['final_ilvl']:+d}{cap} | **{r['rarity']}** | "
            f"{'yes' if r['on_watchlist'] else '—'} | "
            f"{r['ticker'] if r['bench'] else '—'} | {r['screen_flag']} |"
        )
    if survey["screen_gated"]:
        lines += [
            "",
            "## Screen-gated (visible, marked, no arithmetic)",
            "",
            "| # | Company | Median TC | Flag |",
            "|---|---|---|---|",
        ]
        for r in survey["screen_gated"]:
            lines.append(f"| {r['rank']} | {r['company']} | ${r['median_tc']:,} | {r['screen_flag']} |")
    if diff:
        lines += ["", f"## Delta vs {diff['prior_as_of']}", ""]
        lines.append(f"- New entrants: {', '.join(diff['new_entrants']) or '—'}")
        lines.append(f"- Dropped: {', '.join(diff['dropped']) or '—'}")
        lines.append(
            "- Rank moves: "
            + (", ".join(f"{m['company']} {m['from']}→{m['to']}" for m in diff["rank_moves"]) or "—")
        )
        lines.append(
            "- Rarity changes: "
            + (", ".join(f"{m['company']} {m['from']}→{m['to']}" for m in diff["rarity_changes"]) or "—")
        )
    lines += ["", f"_Agency: {AGENCY_LINE} (never a modifier). {survey['attribution']} · {c['url']}_", ""]
    return "\n".join(lines)


def write_survey(role_hunt: Path, survey: dict[str, Any], diff: dict[str, Any] | None) -> tuple[Path, Path]:
    d = pool_dir(role_hunt)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{survey['as_of']}-{survey['cut']['key']}"
    jp, mp = d / f"{stem}.json", d / f"{stem}.md"
    jp.write_text(json.dumps(survey, indent=2), encoding="utf-8")
    mp.write_text(survey_markdown(survey, diff), encoding="utf-8")
    return mp, jp
