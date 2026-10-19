"""The levels.fyi slug + family + industry index — built from the sitemaps, queried locally.

Every levels.fyi verb needs the company's EXACT slug
(`example-games`, not `example`), the real job-family slugs (`/t/site-reliability-engineer` does not
exist — SRE is a focus under `software-engineer`, or nothing), and the geo slugs the pinned
ladder route accepts. None of those are guessable; the sitemaps publish all of them.

Ingest (`hn career levels-index --refresh`, one-time then quarterly): stream `/sitemap.xml` →
its five children → their ~104 files (companies ×80, job-family ×20, industry ×3, base; the
community sitemap is forum threads and is skipped) and keep only the path grammar:

    /companies/{slug}[/salaries|/benefits]                       → companies
    /t/{family}[/{locations|focus}/{value}]                      → families + their sub-forms
    /industry/{slug}[/locations/{geo}]                           → industries
    /leaderboard/{Family}/{Level}[/{city|region|country}/{Geo}]  → the leaderboard grammar
    /h1b/{title}/{city}.html · /h1b/city/{c}.html · /h1b/company/{slug}.html

Output: `role-hunt/reference/levels-index.json` (pack-aware root) with `fetched_at` + the max
`lastmod` seen. ~130k company slugs → a few MB; the raw sitemaps (~2.5 GB, uncompressed on the
wire) are never persisted. The companies sitemap carries slugs only — no display names — so
name → slug resolution is slugify + a candidate search over the slug list.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.career.providers.levels import SITEMAP_INDEX, iter_sitemap_locs
from harness.career.service import slugify
from harness.errors import ProviderError

INDEX_REL = ("reference", "levels-index.json")

# Name tokens too generic to stand alone in the loose candidate tier.
_GENERIC = frozenset({"labs", "games", "group", "global", "technologies", "systems", "software",
                      "america", "north", "holdings", "corporation", "company", "studios", "networks"})

# The sitemap children this index reads. `community` (forum threads) is deliberately out.
_WANTED = ("base-sitemap", "companies-sitemap", "job-family-sitemap", "industry-sitemap")

_LOCALE = re.compile(r"^/[a-z]{2}(?:-[a-z]{2})?/")  # /en-gb/… hreflang copies in the base sitemap

Fetcher = Callable[[str], Iterable[tuple[str, str]]]


def index_path(role_hunt: Path) -> Path:
    return role_hunt.joinpath(*INDEX_REL)


def load_index(role_hunt: Path) -> dict[str, Any] | None:
    p = index_path(role_hunt)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProviderError(f"levels index {p} is unreadable: {e} — rebuild with --refresh") from e
    return raw if isinstance(raw, dict) else None


def _empty() -> dict[str, Any]:
    return {
        "companies": set(),
        "families": {},  # slug → {"locations": set, "focus": set, "other": set}
        "industries": {},  # slug → {"locations": set}
        "leaderboard": {"families": set(), "levels": {}, "geo": {"city": set(), "region": set(),
                                                                  "country": set()}},
        "h1b": {"titles": set(), "cities": set(), "companies": set()},
        "lastmod": "",
        "files": 0,
        "urls": 0,
    }


def absorb(acc: dict[str, Any], loc: str, lastmod: str = "") -> None:
    """Fold one sitemap URL into the accumulator by its path grammar. Unknown shapes are
    ignored (the sitemaps carry benefits/internships/services routes this lane has no use for)."""
    path = urlparse(loc).path
    if _LOCALE.match(path):
        return
    parts = [p for p in path.split("/") if p]
    if not parts:
        return
    acc["urls"] += 1
    if lastmod and lastmod > acc["lastmod"]:
        acc["lastmod"] = lastmod
    head = parts[0]
    if head == "companies" and len(parts) >= 2:
        acc["companies"].add(parts[1])
    elif head == "t" and len(parts) >= 2:
        fam = acc["families"].setdefault(parts[1], {"locations": set(), "focus": set(), "other": set()})
        if len(parts) >= 4:
            kind = parts[2]
            (fam[kind] if kind in ("locations", "focus") else fam["other"]).add(
                parts[3] if kind in ("locations", "focus") else "/".join(parts[2:])
            )
    elif head == "industry" and len(parts) >= 2:
        ind = acc["industries"].setdefault(parts[1], {"locations": set()})
        if len(parts) >= 4 and parts[2] == "locations":
            ind["locations"].add(parts[3])
    elif head == "leaderboard" and len(parts) >= 2:
        lb = acc["leaderboard"]
        lb["families"].add(parts[1])
        if len(parts) >= 3:
            lb["levels"].setdefault(parts[1], set()).add(parts[2])
        if len(parts) >= 5 and parts[3] in lb["geo"]:
            lb["geo"][parts[3]].add(parts[4])
    elif head == "h1b" and len(parts) >= 3:
        leaf = parts[2].removesuffix(".html")
        if parts[1] == "city":
            acc["h1b"]["cities"].add(leaf)
        elif parts[1] == "company":
            acc["h1b"]["companies"].add(leaf)
        elif parts[1] not in ("map", "rankings", "pages"):
            acc["h1b"]["titles"].add(parts[1])
            acc["h1b"]["cities"].add(leaf)


def _finalize(acc: dict[str, Any]) -> dict[str, Any]:
    def s(x: Iterable[str]) -> list[str]:
        return sorted(x)

    return {
        "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sitemap_lastmod": acc["lastmod"],
        "counts": {
            "files": acc["files"], "urls": acc["urls"], "companies": len(acc["companies"]),
            "families": len(acc["families"]), "industries": len(acc["industries"]),
        },
        "companies": s(acc["companies"]),
        "families": {
            k: {"locations": s(v["locations"]), "focus": s(v["focus"]), "other": s(v["other"])}
            for k, v in sorted(acc["families"].items())
        },
        "industries": {k: {"locations": s(v["locations"])} for k, v in sorted(acc["industries"].items())},
        "leaderboard": {
            "families": s(acc["leaderboard"]["families"]),
            "levels": {k: s(v) for k, v in sorted(acc["leaderboard"]["levels"].items())},
            "geo": {k: s(v) for k, v in acc["leaderboard"]["geo"].items()},
        },
        "h1b": {k: s(v) for k, v in acc["h1b"].items()},
    }


def _wanted(loc: str) -> bool:
    return any(Path(urlparse(loc).path).name.startswith(w) for w in _WANTED)


def build_index(
    role_hunt: Path,
    *,
    fetch: Fetcher | None = None,
    progress: Callable[[str], None] | None = None,
    index_url: str = SITEMAP_INDEX,
) -> dict[str, Any]:
    """Stream every wanted sitemap file into the accumulator and write the index. One file per
    request, paced by the provider; nothing raw is kept. `fetch` resolves at call time (a test
    swaps the module's `iter_sitemap_locs`; a default-argument binding would not see it)."""
    fetch = fetch or iter_sitemap_locs
    acc = _empty()

    def read(url: str) -> None:
        if progress:
            progress(url)
        for loc, lastmod in fetch(url):
            absorb(acc, loc, lastmod)
        acc["files"] += 1

    for child, _ in fetch(index_url):
        if not _wanted(child):
            continue
        # A child is either a second-level INDEX (companies/job-family/industry → -0..-N files;
        # every entry ends in .xml) or itself a urlset (base-sitemap). Names can't tell them
        # apart; the entries can.
        entries = list(fetch(child))
        if entries and all(u.endswith(".xml") for u, _ in entries):
            for leaf, _ in entries:
                read(leaf)
        else:
            if progress:
                progress(child)
            for loc, lastmod in entries:
                absorb(acc, loc, lastmod)
            acc["files"] += 1
    if acc["files"] == 0:
        raise ProviderError("no sitemap files were read — the sitemap index shape changed?")
    index = _finalize(acc)
    p = index_path(role_hunt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


# ————— queries —————


def resolve_slug_in_index(index: dict[str, Any], name: str) -> tuple[str | None, list[str]]:
    """(exact slug or None, candidates). Exact = the slugified name is a listed company. Candidates
    (when no exact hit) = slugs starting with the slugified name, then slugs containing every
    token of the name — ranked short-first so 'Example' offers example-games before example-games-korea."""
    slugs: list[str] = index.get("companies", [])
    guess = slugify(name)
    have = set(slugs)
    if guess in have:
        return guess, []
    tokens = [t for t in guess.split("-") if t]
    starts = [s for s in slugs if s.startswith(guess)]
    contains = [s for s in slugs if s not in starts and all(t in s.split("-") for t in tokens)]
    # Third tier: a distinctive token on its own ('Grafana Labs' is listed as `grafana`, 'Acme
    # Games Studio' as `acme-games`). Short/generic tokens (labs, games, of, inc) never qualify.
    seen = set(starts) | set(contains)
    strong = [t for t in tokens if len(t) >= 5 and t not in _GENERIC]
    loose = [s for s in slugs if s not in seen and any(s == t or s.startswith(t + "-") for t in strong)]
    cands = sorted(starts, key=len) + sorted(contains, key=len) + sorted(loose, key=len)
    return None, cands[:12]


def families(index: dict[str, Any], grep: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    fams: dict[str, Any] = index.get("families", {})
    items = sorted(fams.items())
    if grep:
        g = grep.lower()
        items = [(k, v) for k, v in items if g in k]
    return items


def industries(index: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(index.get("industries", {}).items())
