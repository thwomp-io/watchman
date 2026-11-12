"""The levels.fyi sitemap index and its lookups: job families, company slugs, industries."""

from __future__ import annotations

import json

import typer
from rich.table import Table

from harness.career.cli.common import console
from harness.career.config import get_settings, role_hunt_root
from harness.career.service import CareerService
from harness.errors import HarnessError, UsageError


def levels_index(
    refresh: bool = typer.Option(
        False, "--refresh", help="(Re)build from the sitemaps — ~104 files, minutes"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine output (counts + stamps)"),
) -> None:
    """The levels.fyi slug / job-family / industry / leaderboard index, built from the sitemaps
    (streamed, one file per request, paced) into role-hunt/reference/levels-index.json. Without
    --refresh: the current index's status. `families` / `slugs` / `industries` query it."""
    from harness.career import levels_index as lix

    root = role_hunt_root(get_settings())
    if refresh:
        console.print("[dim]streaming the levels.fyi sitemaps (paced; ~2.5 GB over the wire)…[/dim]")
        index = lix.build_index(root, progress=lambda u: console.print(f"[dim]  {u}[/dim]"))
    else:
        loaded = lix.load_index(root)
        if loaded is None:
            raise HarnessError(f"no index at {lix.index_path(root)} — run `hn career levels-index --refresh`")
        index = loaded
    status = {
        "path": str(lix.index_path(root)),
        "fetched_at": index.get("fetched_at"),
        "sitemap_lastmod": index.get("sitemap_lastmod"),
        "counts": index.get("counts"),
    }
    if as_json:
        console.print_json(json.dumps(status))
        return
    c = index.get("counts", {})
    console.print(
        f"levels index · fetched {index.get('fetched_at', '?')[:10]} · sitemap lastmod "
        f"{index.get('sitemap_lastmod', '?')[:10]} · {c.get('companies', 0):,} companies · "
        f"{c.get('families', 0)} families · {c.get('industries', 0)} industries · "
        f"{c.get('files', 0)} files / {c.get('urls', 0):,} urls"
    )
    console.print(f"[dim]{status['path']}[/dim]")


def _load_levels_index() -> dict[str, object]:
    from harness.career import levels_index as lix

    root = role_hunt_root(get_settings())
    index = lix.load_index(root)
    if index is None:
        raise HarnessError("no levels index yet — run `hn career levels-index --refresh` once")
    return index


def families(
    grep: str = typer.Option(None, "--grep", help="Substring filter on the family slug"),
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """levels.fyi job-family slugs from the index, with the sub-path forms each carries
    (locations/ geo slugs, focus/ areas). `--grep engineer` narrows. The family slug is what
    `comp --family` and the ladder route take; a focus is a filter on the site, not a route."""
    from harness.career import levels_index as lix

    index = _load_levels_index()
    rows = lix.families(index, grep)
    if as_json:
        console.print_json(json.dumps(dict(rows)))
        return
    if not rows:
        console.print(f"[yellow]no family matches {grep!r}[/yellow]")
        return
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    t.add_column("Family slug")
    t.add_column("Locations", justify="right")
    t.add_column("Focus areas")
    t.add_column("Example location")
    for slug, v in rows:
        locs = v.get("locations", [])
        focus = v.get("focus", [])
        t.add_row(
            slug,
            str(len(locs)),
            ", ".join(focus[:8]) + (" …" if len(focus) > 8 else ""),
            locs[0] if locs else "—",
        )
    console.print(t)
    console.print(
        f"[dim]{len(rows)} families · route: /t/<family>[.md] · /t/<family>/locations/<geo>[.md][/dim]"
    )


def slugs(
    name: str = typer.Argument(None, help="Company name to resolve (e.g. 'Example Co')"),
    watchlist: bool = typer.Option(False, "--watchlist", help="Resolve every watchlist company"),
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """Company name → levels.fyi slug from the sitemap index (exact slugified hit, else the
    nearest candidates). `--watchlist` resolves every role-hunt/watchlist.yml company and says
    'no levels page' where nothing matches."""
    from harness.career import levels_index as lix

    index = _load_levels_index()
    names: list[str] = []
    if watchlist:
        svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
        names = [c.name for c in svc.load().companies]
    elif name:
        names = [name]
    else:
        raise UsageError("give a company name or --watchlist")
    out = []
    for n in names:
        hit, cands = lix.resolve_slug_in_index(index, n)
        out.append({"name": n, "slug": hit, "candidates": cands})
    if as_json:
        console.print_json(json.dumps(out))
        return
    for r in out:
        if r["slug"]:
            console.print(f"  {r['name']} → [bold]{r['slug']}[/bold]")
        elif r["candidates"]:
            near = ", ".join(r["candidates"][:6])
            console.print(f"  {r['name']} → [yellow]no exact slug[/yellow]; nearest: {near}")
        else:
            console.print(f"  {r['name']} → [red]no levels page[/red]")


def industries(
    as_json: bool = typer.Option(False, "--json", help="Machine output"),
) -> None:
    """levels.fyi industry pages from the index — the pond indices by sector (gaming, fintech,
    semiconductors, …) with their geo sub-pages. Browse: https://www.levels.fyi/industry/<slug>."""
    from harness.career import levels_index as lix
    from harness.career.providers.levels import BASE

    index = _load_levels_index()
    rows = lix.industries(index)
    if as_json:
        console.print_json(
            json.dumps(
                [
                    {"slug": s, "url": f"{BASE}/industry/{s}", "locations": v.get("locations", [])}
                    for s, v in rows
                ]
            )
        )
        return
    for s, v in rows:
        console.print(f"  {s:<28} {BASE}/industry/{s}   [dim]{len(v.get('locations', []))} geo pages[/dim]")
    console.print(f"[dim]{len(rows)} industries[/dim]")
