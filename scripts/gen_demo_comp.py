"""Maintainer tool — generate the comp-sense fixtures for every sample pack.

The Career ▸ Pool and Career ▸ Ladders tabs read `hn career pool --survey --json` and
`hn career comp --all --json` — both pure reads over the role-hunt corpus (`pool/` surveys,
`comp/` ladders, the modifier catalogue, the overlay's comp anchor). A demo persona that lacks
those rails renders two honest-empty tabs, so every bundled pack ships:

  career/reference/pool-enchants.yaml   the modifier catalogue (shared machine keys + per-persona extras)
  career/reference/pool-items.yaml      judged modifiers for the fixture cast (fictional companies)
  career/comp/<slug>.json               one fictional ladder per watchlist company, `source: sample-pack`
  career/pool/<as_of>-<cut>.{json,md}   one Staff/US survey built OFFLINE from the test fixture page
  config/harness.yaml                   the persona's overlay with `career.comp_anchor.anchor_tc`
  watchlist.yml                         `pool_flags` for the fixture's screened-out name (the gated pane)

Everything is FICTIONAL and says so: the ladders carry `source: "sample-pack"` and a fiction
attribution (the Ladders contract never marks a sample-pack ladder stale, the merged pool's
attribution follows the data), the survey is the fixture leaderboard (Acme Cloud, Thin Co…) run
through the real pool verb with the network replaced by the fixture, stamps are fixed so a regen
is byte-identical (the publish determinism guard). Re-runnable; overwrites only what it owns.

Run:  uv run python scripts/gen_demo_comp.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from harness.career import comp as compmod
from harness.career.pool import build_pool, pool_dir, write_survey
from harness.career.service import slugify
from harness.career.watchlist import load_watchlist

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "samples" / "packs"
FIXTURE_HTML = ROOT / "tests" / "career" / "fixtures" / "levels-leaderboard.html"

ATTRIBUTION = "Fictional sample data (demo pack) — not from levels.fyi"
SOURCE = "sample-pack"
FETCHED_AT = "2026-09-08T12:00:00+00:00"
AS_OF = "2026-09-08"

# The persona's comp anchor (the number the item model measures a median against) — sized to the
# persona's story, never a real figure. college-grad = a new-grad base; early-retiree = the last
# senior-IC package; demo-investor / demo-growth = a mid-career senior anchor.
ANCHORS = {
    "demo-investor": 320_000,
    "demo-growth": 380_000,
    "early-retiree": 260_000,
    "college-grad": 150_000,
}

# The modifier catalogue — the SAME keys the engine's machine tier emits (`pool.machine_modifiers`)
# plus a neutral judged set; values are small signed ints. No personal nouns.
CATALOGUE = """\
# The pool's modifier catalogue (sample pack — fictional, illustrative values). Every entry: key ·
# display name · value (small signed int) · family (for set bonuses) · how (machine = the verb
# computes it from data on disk; agent = written into pool-items.yaml with a source pointer;
# user = the operator's stamp). Enchantments are positive, debuffs negative.
enchantments:
  remote_first:        {{name: "Remote-first",              value: 3, family: presence, how: machine}}
  home_area_priority:  {{name: "Home-area seat",            value: 2, family: presence, how: machine}}
  liquid_equity:       {{name: "Liquid equity",             value: 2, family: equity,   how: machine}}
  level_latitude:      {{name: "Level latitude",            value: 2, family: fit,      how: machine}}
  base_clears_anchor:  {{name: "Posted base clears anchor", value: 3, family: comp,     how: machine}}
  finance_researched:  {{name: "Researched on the bench",   value: 1, family: edge,     how: machine}}
  builds_own_tools:    {{name: "Team builds its own tools", value: 3, family: fit,      how: agent}}
  ai_forward_org:      {{name: "AI-forward org",            value: 2, family: fit,      how: agent}}
  warm_path:           {{name: "Warm path in",              value: 2, family: edge,     how: agent}}
{extra_enchantments}debuffs:
  onsite_five_day:     {{name: "Five days on-site",         value: {onsite}, family: presence,  how: agent}}
  illiquid_equity:     {{name: "Illiquid equity",           value: -2, family: equity,    how: agent}}
  layoff_cadence:      {{name: "Layoff cadence",            value: -2, family: stability, how: agent}}
  consultancy_shape:   {{name: "Consultancy shape",         value: -4, family: shape,     how: agent}}
{extra_debuffs}# Set bonuses: when at least `count` modifiers of a family apply, the bonus is its own line.
set_bonuses:
  fit:      {{name: "Strong-fit set", count: 3, bonus: 2}}
  presence: {{name: "Presence set",   count: 2, bonus: 1}}

# Role modifiers — the posting-level layer under the company item (`hn career roles`); machine-applied.
role_modifiers:
  level_in_band: {{name: "Staff+ seat (in band)", value: 8, family: role_level, how: machine}}
  level_lead: {{name: "Leadership seat", value: 0, family: role_level, how: machine}}
  level_below: {{name: "Below the band", value: -8, family: role_level, how: machine}}
  shape_in_lane: {{name: "In-lane shape", value: 6, family: role_shape, how: machine}}
  shape_off_lane: {{name: "Off-lane shape", value: -25, family: role_shape, how: machine}}
  loc_home: {{name: "Home-area seat", value: 4, family: role_presence, how: machine}}
  loc_remote_us: {{name: "Remote (US)", value: 4, family: role_presence, how: machine}}
  loc_relocation: {{name: "Relocation", value: -4, family: role_presence, how: machine}}
  loc_outside_us: {{name: "Outside the US", value: -15, family: role_presence, how: machine}}
  posted_clears_anchor: {{name: "Posted range reaches the anchor", value: 3, family: role_comp, how: machine}}
  fresh: {{name: "Updated within 14 days", value: 1, family: role_time, how: machine}}
  new: {{name: "New since the earlier scans", value: 1, family: role_time, how: machine}}
"""

# Per-persona variations on the shared catalogue: one or two criteria that persona would weigh.
PERSONA_CATALOGUE: dict[str, dict[str, Any]] = {
    "demo-growth": {
        "onsite": -3,
        "enchantments": [("fast_promotion", "Fast promotion track", 2, "fit")],
        "debuffs": [],
    },
    "demo-investor": {
        "onsite": -4,
        "enchantments": [],
        "debuffs": [("long_commute", "Long commute", -2, "presence")],
    },
    "early-retiree": {
        "onsite": -5,
        "enchantments": [("part_time_ok", "Part-time welcome", 3, "presence")],
        "debuffs": [("full_time_only", "Full-time only", -3, "presence")],
    },
    "college-grad": {
        "onsite": -1,
        "enchantments": [("mentorship_program", "Formal mentorship", 2, "fit")],
        "debuffs": [("no_new_grad_track", "No new-grad track", -2, "fit")],
    },
}


def _mod_lines(mods: list[tuple[str, str, int, str]]) -> str:
    return "".join(
        f'  {key + ":":<20} {{name: "{name}", value: {value}, family: {family}, how: agent}}\n'
        for key, name, value, family in mods
    )


def catalogue_for(pack: str) -> str:
    spec = PERSONA_CATALOGUE[pack]
    return CATALOGUE.format(
        onsite=spec["onsite"],
        extra_enchantments=_mod_lines(spec["enchantments"]),
        extra_debuffs=_mod_lines(spec["debuffs"]),
    )


# Judged modifiers for the fixture leaderboard's cast (all fictional companies).
ITEMS = """\
# Per-company APPLIED modifiers for the judged tiers (sample pack — fictional). Keys are company
# slugs; entry: slug → {by: agent|user, source: <pointer>, as_of: YYYY-MM-DD, modifiers: [key…]}.
acme-cloud:
  by: agent
  source: companies/acme-cloud.md (fictional)
  as_of: 2026-09-08
  modifiers: [builds_own_tools, ai_forward_org, warm_path]
thin-co:
  by: agent
  source: companies/thin-co.md (fictional)
  as_of: 2026-09-08
  modifiers: [ai_forward_org, illiquid_equity]
delta-games:
  by: user
  source: the operator's call (fictional)
  modifiers: [onsite_five_day, consultancy_shape]
"""

POOL_FLAGS = """
# Values-screen flags for pool companies (sample pack — fictional): a flag in the screen-out set
# moves the company to the Pool tab's screen-gated pane (no arithmetic); gray flags stay in play.
pool_flags:
  gamma-defense: defense
  beta-systems: gray-adtech
"""

OVERLAY_CAREER = """
career:
  global_settings:
    # The comp anchor the pool's item model + the reports rail measure a median against — the
    # anchor this persona negotiates from (FICTIONAL sample value).
    comp_anchor:
      anchor_tc: {anchor}
      bands: [staff, principal]
"""

OVERLAY_NEW = """\
# {name}'s user overlay — FICTIONAL sample data (the pack-precedence demo: an active pack's
# config/harness.yaml wins over the corpus-resident file). Only the blocks the demo needs.
""" + OVERLAY_CAREER.lstrip("\n")


def _h(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def ladder_for(company: str, slug: str, anchor: int) -> dict[str, object]:
    """A plausible fictional ladder: five level codes with a title on the three the band picker
    reads, medians stepping up from ~60% of the anchor, a mix, YOE, a high package."""
    seed = _h(slug)
    base = int(anchor * (0.55 + (seed % 20) / 100))  # 55–74% of the anchor at L3
    steps = [1.0, 1.35, 1.8, 2.4, 3.1]
    codes = ["L3", "L4", "L5", "L6", "L7"]
    titles = {
        "L4": "Software Engineer II",
        "L5": "Senior Software Engineer",
        "L6": "Staff Software Engineer",
        "L7": "Principal Software Engineer",
    }
    yoe = {"L3": "0–2", "L4": "2–5", "L5": "5–8", "L6": "8–12", "L7": "12+"}
    levels = [
        {"level": c, "median_tc": int(round(base * s, -3)), "yoe": yoe[c], "title": titles.get(c)}
        for c, s in zip(codes, steps, strict=True)
    ]
    median = levels[2]["median_tc"]
    assert isinstance(median, int)
    return {
        "company": company,
        "slug": slug,
        "family": compmod.DEFAULT_FAMILY,
        "location": compmod.DEFAULT_LOCATION,
        "currency": "USD",
        "url": f"https://example.invalid/sample-pack/{slug}",
        "generated": FETCHED_AT,
        "last_updated": "September 8, 2026",
        "median_tc": median,
        "high_tc": int(round(levels[-1]["median_tc"] * 1.6, -3)),  # type: ignore[operator]
        "levels": levels,
        "mix": {"base": int(median * 0.55), "stock": int(median * 0.35), "bonus": int(median * 0.10)},
        "note": None,
        "fetched_at": FETCHED_AT,
        "source": SOURCE,
        "attribution": ATTRIBUTION,
    }


def write_ladders(pack: Path, anchor: int) -> list[str]:
    career = pack / "career"
    wl = load_watchlist(pack, root=career)
    comp = career / "comp"
    if comp.exists():
        shutil.rmtree(comp)
    comp.mkdir(parents=True)
    slugs = []
    for c in wl.companies:
        slug = slugify(c.name)
        lad = ladder_for(c.name, slug, anchor)
        key = f"{compmod.DEFAULT_FAMILY}/{compmod.DEFAULT_LOCATION}"
        cache = {"slug": slug, "company": c.name, "ladders": {key: lad}, "updated_at": FETCHED_AT}
        (comp / f"{slug}.json").write_text(json.dumps(cache, indent=2) + "\n")
        slugs.append(slug)
    return slugs


def ensure_overlay(pack: Path, anchor: int) -> None:
    p = pack / "config" / "harness.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(OVERLAY_NEW.format(name=pack.name, anchor=anchor))
        return
    text = p.read_text()
    loaded = yaml.safe_load(text) or {}
    if (loaded.get("career") or {}).get("global_settings", {}).get("comp_anchor"):
        return
    if "career:" in text:
        raise SystemExit(f"{p}: has a career block without comp_anchor — add it by hand")
    p.write_text(text.rstrip("\n") + "\n" + OVERLAY_CAREER.format(anchor=anchor))


def ensure_catalogue(pack: Path) -> None:
    ref = pack / "career" / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "pool-enchants.yaml").write_text(catalogue_for(pack.name))
    (ref / "pool-items.yaml").write_text(ITEMS)
    wl = pack / "career" / "watchlist.yml"
    text = wl.read_text()
    if "pool_flags" not in text:
        wl.write_text(text.rstrip("\n") + "\n" + POOL_FLAGS)


def write_fixture_survey(pack: Path) -> Path:
    """The fixture leaderboard through the REAL pool verb under the pack's own env (overlay anchor,
    watchlist, scan twin, catalogue), the network replaced by the fixture page."""
    html = FIXTURE_HTML.read_text(encoding="utf-8")
    career = pack / "career"
    for old in pool_dir(career).glob("*"):
        old.unlink()
    with tempfile.TemporaryDirectory() as scratch:
        saved = {k: os.environ.get(k) for k in ("TRACKER_PATH", "WEIGHTS_PACK", "HARNESS_CACHE_DIR")}
        os.environ.update(TRACKER_PATH=str(pack), WEIGHTS_PACK=str(pack), HARNESS_CACHE_DIR=scratch)
        try:
            survey = build_pool(pack, career, level="staff", location="us", fetch=lambda _u: html)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    survey["fetched"] = "fixture"
    survey["as_of"] = AS_OF
    survey["generated_at"] = FETCHED_AT
    survey["attribution"] = ATTRIBUTION
    survey["cut"]["url"] = "https://example.invalid/sample-pack/leaderboard"
    _md, jp = write_survey(career, survey, None)
    return jp


def main() -> None:
    for pack in sorted(p for p in PACKS.iterdir() if (p / "pack.yaml").exists()):
        anchor = ANCHORS.get(pack.name)
        if anchor is None:
            raise SystemExit(f"no anchor configured for pack {pack.name} — add it to ANCHORS")
        ensure_overlay(pack, anchor)
        ensure_catalogue(pack)
        slugs = write_ladders(pack, anchor)
        jp = write_fixture_survey(pack)
        print(f"{pack.name}: anchor ${anchor:,} · ladders {', '.join(slugs)} · survey {jp.name}")


if __name__ == "__main__":
    main()
