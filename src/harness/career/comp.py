"""The comp sense — a company's engineering pay ladder from levels.fyi, cached in the vault.

`hn career comp <company>`. Comp figures on every
research entry are DIRECTIONAL until verified live; this turns that hand web pass into a
deterministic sense the operator polls like the openings scan — and
the CAREER dashboard / the company profiles / the packet evaluation read the CACHE off disk,
never the network (the determinism contract).

Cache: `role-hunt/comp/<slug>.json` (pack-aware: `<pack>/career/comp/` under a weight pack) —
one file per company, one ladder per `{family}/{location}` key, each stamped `fetched_at`. The
default TTL is a week (levels moves on a submissions timescale; a daily poll of a few dozen companies
would only spend the site's pacing budget); `--refresh` forces a fetch.

Every render carries the attribution line the `.md` pages require. The provider docstring
(`harness.career.providers.levels`) holds the surface facts + gotchas.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from harness.career.providers.levels import (
    ATTRIBUTION,
    currency_code,
    fetch_levels_page,
    ladder_url,
    parse_ladder_md,
)
from harness.career.service import slugify
from harness.errors import ProviderError
from harness.yamlio import load_yaml

DEFAULT_FAMILY = "software-engineer"
DEFAULT_LOCATION = "united-states"
TTL_DAYS = 7


class CompLevel(BaseModel):
    level: str  # the SITE's level code (IC5, P60, L5…) — never a title unless the site states one
    median_tc: int
    yoe: str | None = None  # "8–9" as the FAQ states it
    title: str | None = None  # only when the page itself maps the level to a title


class CompLadder(BaseModel):
    company: str
    slug: str
    family: str
    location: str
    currency: str
    url: str
    generated: str = ""  # the page's own stamp
    last_updated: str = ""  # "September 8, 2026" as the page states it
    median_tc: int | None = None
    high_tc: int | None = None
    levels: list[CompLevel] = Field(default_factory=list)
    mix: dict[str, int] | None = None  # median base / stock / bonus
    note: str | None = None  # e.g. the thin-page line
    fetched_at: str
    source: str = "levels.fyi"
    attribution: str = ATTRIBUTION

    @property
    def key(self) -> str:
        return f"{self.family}/{self.location}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def comp_dir(role_hunt: Path) -> Path:
    return role_hunt / "comp"


def cache_path(role_hunt: Path, slug: str) -> Path:
    return comp_dir(role_hunt) / f"{slug}.json"


def load_cache(role_hunt: Path, slug: str) -> dict[str, Any]:
    p = cache_path(role_hunt, slug)
    if not p.exists():
        return {"slug": slug, "ladders": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProviderError(f"comp cache {p} is unreadable: {e} — delete it or pass --refresh") from e
    if not isinstance(raw, dict):
        raise ProviderError(f"comp cache {p} is not an object — delete it or pass --refresh")
    raw.setdefault("ladders", {})
    return dict(raw)


def cached_ladder(role_hunt: Path, slug: str, family: str, location: str) -> CompLadder | None:
    """The cached ladder for one key, or None. The READ side every surface uses (profiles,
    dashboard, evaluation) — no network here, ever."""
    entry = load_cache(role_hunt, slug)["ladders"].get(f"{family}/{location}")
    return CompLadder.model_validate(entry) if entry else None


def cached_ladders(role_hunt: Path) -> list[CompLadder]:
    """Every cached ladder in the vault (the dashboard's tile source)."""
    out: list[CompLadder] = []
    for p in sorted(comp_dir(role_hunt).glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in raw.get("ladders", {}).values():
            try:
                out.append(CompLadder.model_validate(entry))
            except ValueError:
                continue
    return out


def _fresh(stamp: str, ttl_days: int) -> bool:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (datetime.now(UTC) - then).days < ttl_days


def resolve_slug(role_hunt: Path, company: str) -> str:
    """Company name → levels.fyi slug. The sitemap index is the authority when it
    exists — an exact slugified hit, else its candidate list names the spelling; without an
    index the slugified name is tried as-is (`example-co` from 'Example Co' works; a miss is the
    provider's 404, which points at `hn career slugs`)."""
    from harness.career.levels_index import load_index, resolve_slug_in_index

    guess = slugify(company)
    index = load_index(role_hunt)
    if index is None:
        return guess
    hit, candidates = resolve_slug_in_index(index, company)
    if hit:
        return hit
    if candidates:
        raise ProviderError(
            f"{company!r} is not a levels.fyi slug; nearest: {', '.join(candidates[:6])} — "
            "pass the slug as the company argument"
        )
    raise ProviderError(f"{company!r} has no levels.fyi company page in the index (slug {guess!r})")


def ladder_from_page(
    text: str, *, company: str, slug: str, family: str, location: str, url: str
) -> CompLadder:
    """Parse + validate one fetched page into a ladder. Refuses loud on a currency other than
    USD or a page whose Location line is not the pin asked for (the geo-localization gotcha) —
    the refusal is the whole point of pinning."""
    raw = parse_ladder_md(text)
    hdr = raw["header"]
    # The page names the company properly ("Levels.fyi – OpenAI Software Engineer Salaries …"); when
    # the caller passed a slug-shaped name (`hn career comp openai`), take the page's spelling.
    fam_words = re.escape(family.replace("-", " "))
    m_title = re.match(rf"^Levels\.fyi\s*[–-]\s*(.+?)\s+{fam_words}\s+Salar", raw.get("title", ""), re.I)
    if m_title and company.lower().replace(" ", "-") == slug:
        company = m_title.group(1).strip() or company
    cur = currency_code(hdr.get("currency", ""))
    if cur != "USD":
        raise ProviderError(
            f"levels.fyi served {company} in {cur or 'an unstated currency'} for location "
            f"{location!r} (page says {hdr.get('location', '?')!r}) — refusing to print a non-USD "
            "ladder; pin a US geography (--location united-states)"
        )
    page_loc = slugify(hdr.get("location", ""))
    if page_loc and page_loc != location and location not in page_loc and page_loc not in location:
        raise ProviderError(
            f"asked levels.fyi for {location!r} but the page is scoped to {hdr.get('location')!r} — "
            "refusing to relabel a slice"
        )
    agg = raw["aggregates"]
    median = agg.get("Median Total Compensation")
    levels = [
        CompLevel(
            level=row["level"],
            median_tc=int(row["median_tc"] or 0),
            yoe=raw["yoe"].get(row["level"]),
            title=raw["titles"].get(row["level"]),
        )
        for row in raw["levels"]
        if row.get("median_tc")
    ]
    note = None
    if not levels:
        note = "no per-level data — the page carries aggregate figures only (a thin page)"
    return CompLadder(
        company=company,
        slug=slug,
        family=family,
        location=location,
        currency=cur,
        url=url,
        generated=hdr.get("generated", ""),
        last_updated=str(agg.get("Last Updated", "")),
        median_tc=median if isinstance(median, int) else None,
        high_tc=raw.get("high_tc"),
        levels=levels,
        mix=raw.get("mix"),
        note=note,
        fetched_at=_now(),
    )


def get_ladder(
    role_hunt: Path,
    company: str,
    *,
    family: str = DEFAULT_FAMILY,
    location: str = DEFAULT_LOCATION,
    refresh: bool = False,
    ttl_days: int = TTL_DAYS,
    fetch: Callable[[str], str] | None = None,
    slug: str | None = None,
) -> tuple[CompLadder, bool]:
    """(ladder, from_cache). Serves the vault copy inside the TTL unless `refresh`; otherwise one
    paced fetch of the pinned page, parsed, validated, written to the cache. `fetch` resolves at
    CALL time (the module's `fetch_levels_page`) so a test can replace the provider without a
    default-argument binding smuggling the real network in."""
    fetch = fetch or fetch_levels_page
    slug = slug or resolve_slug(role_hunt, company)
    key = f"{family}/{location}"
    cache = load_cache(role_hunt, slug)
    entry = cache["ladders"].get(key)
    if entry and not refresh and _fresh(str(entry.get("fetched_at", "")), ttl_days):
        return CompLadder.model_validate(entry), True
    url = ladder_url(slug, family, location)
    text = fetch(url)
    ladder = ladder_from_page(text, company=company, slug=slug, family=family, location=location, url=url)
    cache["company"] = company
    cache["ladders"][key] = ladder.model_dump()
    cache["updated_at"] = ladder.fetched_at
    p = cache_path(role_hunt, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return ladder, False


def fmt_money(n: int | None) -> str:
    return f"${n:,.0f}" if n is not None else "—"


def ladder_markdown(ladder: CompLadder, *, heading: str = "##") -> str:
    """The Comp block (used verbatim by the company-profile generator + the MCP twin). The
    attribution line is part of the block, not optional."""
    fam = ladder.family.replace("-", " ").title()
    loc = ladder.location.replace("-", " ").title()
    lines = [f"{heading} Comp (levels.fyi) — {fam}, {loc}", ""]
    lines.append(
        f"Median TC **{fmt_money(ladder.median_tc)}**"
        + (f" · high {fmt_money(ladder.high_tc)}" if ladder.high_tc else "")
        + (f" · site updated {ladder.last_updated}" if ladder.last_updated else "")
        + f" · fetched {ladder.fetched_at[:10]}"
    )
    if ladder.mix:
        m = ladder.mix
        lines.append(
            f"Median mix: base {fmt_money(m.get('base'))} · stock {fmt_money(m.get('stock'))} · "
            f"bonus {fmt_money(m.get('bonus'))}"
        )
    lines.append("")
    if ladder.levels:
        lines += ["| Level | Median TC | YOE | Title (site-stated) |", "|---|---|---|---|"]
        for lv in ladder.levels:
            lines.append(f"| {lv.level} | {fmt_money(lv.median_tc)} | {lv.yoe or '—'} | {lv.title or '—'} |")
    else:
        lines.append(f"_{ladder.note}_")
    lines += ["", f"_{ladder.attribution} · {ladder.url}_"]
    return "\n".join(lines)


def blind_refusal() -> str:
    return (
        "Blind is declined as a comp source: its data sits behind a login with no sanctioned "
        "agent surface (no llms.txt, no public API), and its terms forbid automated access — "
        "levels.fyi's keyless markdown routes are the sanctioned rail (`hn career comp <company>`)."
    )


def anchor_tc() -> int:
    """The overlay's comp anchor (`career.comp_anchor.anchor_tc`) — the anchor you negotiate from,
    shared by the pool's item model and the reports rail's anchor read. A missing anchor is a named
    refusal."""
    from harness.settings import overlay_get

    raw = overlay_get("career", "comp_anchor", "anchor_tc")
    try:
        n = int(float(str(raw)))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        raise ProviderError(
            "no comp anchor configured — set career.global_settings.comp_anchor.anchor_tc in the "
            "tracker overlay (config/harness.yaml)"
        )
    return n


def ladders_contract(
    role_hunt: Path, ladders: list[CompLadder] | None = None, *, ttl_days: int = TTL_DAYS
) -> dict[str, Any]:
    """`hn career comp --all --json` — every cached ladder as one contract for the Ladders tab: the
    ladder fields + `stale` (past the TTL) + the anchor + the applicant's band rows flagged
    (`career.comp_anchor.bands`, default Staff/Principal spellings) + watchlist/bench chips from
    the corpus. No network — refresh is the operator's `hn career comp <name> --refresh`."""
    from harness.career.levels_index import load_index, resolve_slug_in_index
    from harness.career.pool import _norm, bench_tickers, classify, rarity_bands, ticker_map
    from harness.career.service import CareerService
    from harness.settings import overlay_get

    ladders = cached_ladders(role_hunt) if ladders is None else ladders
    try:
        anchor: int | None = anchor_tc()
    except ProviderError:
        anchor = None
    bands = overlay_get("career", "comp_anchor", "bands", default=["staff", "principal"])
    band_words = [str(b).lower() for b in (bands if isinstance(bands, list) else [bands])]
    svc = CareerService(role_hunt.parent, role_hunt_root=role_hunt)
    try:
        wl = {c.name: c for c in svc.load().companies}
    except ProviderError:
        wl = {}
    index = load_index(role_hunt)
    wl_by_slug: dict[str, Any] = {}
    for name, c in wl.items():
        hit = resolve_slug_in_index(index, name)[0] if index else None
        wl_by_slug[hit or slugify(name)] = c
    tickers = ticker_map()
    bench = bench_tickers(role_hunt.parent)
    bands = rarity_bands(role_hunt)
    levels_map = load_levels_map(role_hunt)
    survey_labels = survey_band_labels(role_hunt)
    out = []
    for lad in ladders:
        d = lad.model_dump()
        # Staleness is a property of a LIVE source: a sample-pack ladder (`source: sample-pack`,
        # the demo personas) has no freshness to lose, so it never wears the STALE chip.
        d["stale"] = lad.source == "levels.fyi" and not _fresh(lad.fetched_at, ttl_days)
        wc = wl_by_slug.get(lad.slug)
        d["on_watchlist"] = wc is not None
        d["tier"] = getattr(wc, "tier", "") if wc else ""
        ticker = tickers.get(_norm(lad.company), "")
        d["ticker"] = ticker
        d["bench"] = bool(ticker and ticker in bench)
        d["doc"] = (
            f"role-hunt/companies/{lad.slug}.md"
            if (role_hunt / "companies" / f"{lad.slug}.md").exists()
            else None
        )
        bands_for = band_rows(
            d["levels"],
            band_words=band_words,
            mapped=levels_map.get(lad.slug),
            survey=survey_labels.get(lad.slug),
            anchor=anchor,
        )
        band_of = {v["level"]: (k, v["basis"]) for k, v in bands_for.items()}
        d["bands"] = bands_for
        for lv in d["levels"]:
            bhit = band_of.get(lv["level"])
            lv["band"] = bhit is not None
            lv["band_name"] = bhit[0] if bhit else None
            lv["band_basis"] = bhit[1] if bhit else None
            lv["vs_anchor_pct"] = round((lv["median_tc"] / anchor - 1) * 100) if anchor else None
            # Per-rung rarity through the POOL's classifier + bands: the rung's ilvl is
            # the same arithmetic as a pool row's base ilvl, so a ladder rung and a pool tile at the
            # same TC read the same color. Engine-side — the widget never classifies.
            lv["ilvl"] = lv["vs_anchor_pct"]
            lv["rarity"] = classify(lv["ilvl"], bands) if lv["ilvl"] is not None else None
        # The card's tint + number: the applicant's FIRST band (staff) row —
        # else the principal row, else nothing (the basis rides along so a heuristic reads as one).
        pick = None
        for name in band_words:
            lv_hit = next((lv for lv in d["levels"] if lv.get("band_name") == name), None)
            if lv_hit:
                pick = lv_hit
                break
        d["card_rarity"] = pick["rarity"] if pick else None
        d["card_ilvl"] = pick["ilvl"] if pick else None
        d["card_level"] = pick["level"] if pick else None
        d["band_basis"] = pick["band_basis"] if pick else None
        out.append(d)
    tier_rank = {t: i for i, t in enumerate(getattr(svc.load(), "tier_order", []) if wl else [])}
    out.sort(key=lambda d: (not d["on_watchlist"], tier_rank.get(d["tier"], 99), -(d["median_tc"] or 0)))
    # The Board's comp tile rows: one per ladder — the applicant's band level (the highest level
    # the band words match, else the highest level at or under the anchor's next step, else the
    # median) and its distance to the anchor.
    tiles = []
    for d in out:
        pick = next((lv for lv in d["levels"] if lv["level"] == d.get("card_level")), None)
        tiles.append(
            {
                "company": d["company"],
                "slug": d["slug"],
                "tier": d["tier"],
                "on_watchlist": d["on_watchlist"],
                "level": pick["level"] if pick else None,
                "median_tc": pick["median_tc"] if pick else d["median_tc"],
                "vs_anchor_pct": (
                    pick["vs_anchor_pct"]
                    if pick
                    else (round((d["median_tc"] / anchor - 1) * 100) if anchor and d["median_tc"] else None)
                ),
                "basis": d.get("band_basis") or "median",
                "stale": d["stale"],
                "doc": d["doc"],
            }
        )
    report = None
    ref_dir = role_hunt / "reference"
    twins = sorted(ref_dir.glob("pay-report-*.json")) if ref_dir.is_dir() else []
    if twins:
        try:
            rj = json.loads(twins[-1].read_text(encoding="utf-8"))
            report = {
                "year": rj.get("year"),
                "anchor_read": rj.get("anchor_read", []),
                "metro": {k: rj.get("metro", {}).get(k) for k in ("location", "median", "p25", "p75", "p90")},
                "us": {k: rj.get("us", {}).get(k) for k in ("median", "p25", "p75", "p90")},
                "levels": rj.get("report", {}).get("levels", {}),
                "fetched_at": rj.get("fetched_at", ""),
            }
        except (OSError, ValueError):
            report = None
    return {
        "ladders": out,
        "tiles": tiles,
        "report": report,
        "bands": bands,
        "anchor": anchor,
        "band_words": band_words,
        "count": len(out),
        "stale": sum(1 for d in out if d["stale"]),
        # The tab footer's attribution follows the DATA: the levels.fyi line on the real corpus, the
        # fiction line under a demo pack (a sample-pack ladder must never be footed as levels.fyi).
        "attribution": " · ".join(
            dict.fromkeys(str(d.get("attribution") or "") for d in out if d.get("attribution"))
        )
        or ATTRIBUTION,
    }


def benchmark_note(role_hunt: Path, company: str) -> str | None:
    """One line for a packet / evaluation: the cached ladder's median + the applicant's band rows
    vs the anchor — from the vault only (no network); None when nothing is cached."""
    from harness.career.levels_index import load_index, resolve_slug_in_index

    index = load_index(role_hunt)
    slug = (resolve_slug_in_index(index, company)[0] if index else None) or slugify(company)
    lad = cached_ladder(role_hunt, slug, DEFAULT_FAMILY, DEFAULT_LOCATION)
    if not lad:
        return None
    try:
        anchor: int | None = anchor_tc()
    except ProviderError:
        anchor = None
    parts = [f"median TC {fmt_money(lad.median_tc)}"]
    for lv in lad.levels:
        title = (lv.title or "").lower()
        if "staff" in title or "principal" in title or "staff" in lv.level.lower():
            vs = f" ({(lv.median_tc / anchor - 1) * 100:+.0f}% vs anchor)" if anchor else ""
            parts.append(f"{lv.level} {fmt_money(lv.median_tc)}{vs}")
    parts.append(f"fetched {lad.fetched_at[:10]} · {lad.attribution}")
    return " · ".join(parts)


# ————— the band rows —————

BAND_NAMES = ("staff", "principal")


def load_levels_map(role_hunt: Path) -> dict[str, dict[str, str]]:
    """`role-hunt/reference/levels-map.yaml`: slug → {staff: <level code>, principal: <level code>}
    — the operator-authored override the band heuristics defer to."""
    import yaml

    p = role_hunt / "reference" / "levels-map.yaml"
    if not p.exists():
        return {}
    try:
        raw = load_yaml(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for slug, spec in raw.items():
        if isinstance(spec, dict):
            out[str(slug)] = {str(k).lower(): str(v) for k, v in spec.items() if v}
    return out


def survey_band_labels(role_hunt: Path) -> dict[str, dict[str, str]]:
    """slug → {staff: label, principal: label} from the NEWEST persisted pool survey per cut — the
    leaderboard names each company's level at the cut's band (e.g. Example Co's 'L6' on the Staff cut)."""
    pool_dir = role_hunt / "pool"
    if not pool_dir.is_dir():
        return {}
    newest: dict[str, Path] = {}
    for p in sorted(pool_dir.glob("*.json")):
        key = p.stem.split("-", 3)[-1] if p.stem.count("-") >= 3 else p.stem
        newest[key] = p  # sorted → the last write per cut key wins
    out: dict[str, dict[str, str]] = {}
    for p in newest.values():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        level = str(d.get("cut", {}).get("level", "")).lower()
        band = next((b for b in BAND_NAMES if b in level), None)
        if not band:
            continue
        for r in d.get("rows", []) + d.get("screen_gated", []):
            if r.get("label"):
                out.setdefault(r["slug"], {})[band] = str(r["label"])
    return out


def _yoe_low(yoe: str | None) -> int | None:
    if not yoe:
        return None
    m = re.match(r"\s*(\d+)", yoe)
    return int(m.group(1)) if m else None


def band_rows(
    levels: list[dict[str, Any]],
    *,
    band_words: list[str],
    mapped: dict[str, str] | None,
    survey: dict[str, str] | None,
    anchor: int | None,
) -> dict[str, dict[str, Any]]:
    """{band: {level, basis}} for staff/principal. Precedence: mapped (levels-map.yaml) → the
    site-stated title → the pool surveys' label → the rung's own name → YOE (staff = the first level
    with 10+ years, principal = the first with 15+ above it) → the anchor crossing (staff = the first
    level at/above the anchor, principal = the next). The basis is part of the answer — a heuristic
    band is shown as one, never as a fact."""
    out: dict[str, dict[str, Any]] = {}
    codes = [str(lv["level"]) for lv in levels]
    lower = [c.lower() for c in codes]

    def find_code(code: str) -> int | None:
        c = code.lower()
        return lower.index(c) if c in lower else None

    used: set[int] = set()
    for band in band_words:
        band = band.lower()
        idx: int | None = None
        basis = ""
        if mapped and mapped.get(band) and (idx := find_code(mapped[band])) is not None:
            basis = "mapped"
        if idx is None:
            for i, lv in enumerate(levels):
                t = (lv.get("title") or "").lower()
                if band in t and i not in used:
                    idx, basis = i, "title"
                    break
        if idx is None and survey and survey.get(band) and (i2 := find_code(survey[band])) is not None:
            idx, basis = i2, "survey"
        if idx is None:
            # the rung's own name ("Staff Software Engineer", "Principal Software Engineer") — many
            # ladders name their levels instead of coding them; the lowest rung naming the band wins
            # (so "Staff" before "Senior Staff")
            for i, code in enumerate(lower):
                if re.search(rf"\b{band}\b", code) and i not in used:
                    idx, basis = i, "level name"
                    break
        if idx is None:
            # YOE: a level the site itself titles "Senior …" is never the staff pick (e.g. a P3 at
            # 10–11 years titled Senior, where P4 is the staff seat)
            need = 10 if band == "staff" else 15
            for i, lv in enumerate(levels):
                lo = _yoe_low(lv.get("yoe"))
                titled_senior = "senior" in (lv.get("title") or "").lower()
                if lo is not None and lo >= need and i not in used and not titled_senior:
                    idx, basis = i, "yoe"
                    break
        if idx is None and anchor:
            above = [i for i, lv in enumerate(levels) if lv["median_tc"] >= anchor and i not in used]
            if above:
                idx, basis = above[0], "anchor-heuristic"
        if idx is None and band == "principal" and "staff" in out:
            si = codes.index(out["staff"]["level"])
            if si + 1 < len(levels):
                idx, basis = si + 1, "next-above-staff"
        if idx is not None:
            used.add(idx)
            out[band] = {"level": codes[idx], "basis": basis}
    # the bands are ordered: a principal pick at/below the staff pick is a heuristic misfire — take
    # the next level above staff instead (named as such)
    if "staff" in out and "principal" in out:
        si, pi = codes.index(out["staff"]["level"]), codes.index(out["principal"]["level"])
        if pi <= si:
            if si + 1 < len(codes):
                out["principal"] = {"level": codes[si + 1], "basis": "next-above-staff"}
            else:
                del out["principal"]
    return out
