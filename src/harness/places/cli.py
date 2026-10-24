"""`hn places` — ad-hoc local place search near a ZIP, a place name, or lat,lon (logic: service.py).

Quota discipline as `hn travel`: a run that needs a live SerpAPI search is a dry run unless `--yes`
— it prints the cost and what the cache covers. Cache hits are free and say so."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from harness.places.service import PlacesService

app = typer.Typer(
    no_args_is_help=True, help="Local place search near a ZIP / place / lat,lon (Google Maps via SerpAPI)."
)
console = Console()


def _svc() -> PlacesService:
    from harness.places.service import PlacesService
    from harness.travel.config.settings import get_settings

    return PlacesService(get_settings().serpapi_key)


def _quota_line() -> str:
    """This month's SerpAPI standing (the account endpoint is free); empty when unavailable."""
    try:
        from harness.travel.config.settings import get_settings
        from harness.travel.providers.serpapi_explore_provider import account_quota

        q = account_quota(get_settings().serpapi_key)
        return f"{q.get('plan_searches_left')} searches left this month"
    except Exception:  # best-effort: pricing never fails on the account read
        return ""


@app.command()
def search(
    query: str = typer.Argument(..., help='What to find: "electronics recycling", "hardware store"'),
    near: str = typer.Option(..., "--near", help="A ZIP, a place (\"Old Town, Springfield\"), or lat,lon"),
    limit: int = typer.Option(10, "--limit", "-n"),
    yes: bool = typer.Option(False, "--yes", help="Spend the live searches (without it: a priced dry run)"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-run the search even if cached (spends one)"),
    nearest: bool = typer.Option(False, "--nearest", help="Nearest first, not Google's relevance order"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Places matching QUERY near --near, in Google's relevance order (--nearest to sort by
    distance): name · distance · open now / today's hours · rating, then address · phone, then
    website · place_id (for `hn places show`)."""
    from harness.travel.providers.base import ProviderError

    svc = _svc()
    try:
        res = svc.search(query, near, limit=limit, spend=yes, refresh=refresh, by_distance=nearest)
    except ProviderError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(code=1) from e
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    if res.anchor is None:
        p = res.plan
        parts = [
            "near point " + ("cached" if p.anchor_cached else "1 search (first time only)"),
            "results " + ("cached" if p.search_cached else "1 search"),
        ]
        console.print(f"[yellow]dry run[/yellow] — {p.cost} live search(es): {' · '.join(parts)}"
                      + (f" · {q}" if (q := _quota_line()) else ""))
        console.print("[dim]pass --yes to run it[/dim]")
        return
    spent = f"{res.spent} live search(es)" if res.spent else "cached — 0 quota"
    head = f"[bold]{escape(query)}[/bold] near {escape(res.anchor.label)}"
    console.print(f"{head} · {len(res.places)} · {spent}")
    for i, pl in enumerate(res.places, 1):
        bits = [f"{pl.distance_mi:g} mi" if pl.distance_mi is not None else "",
                pl.open_state or pl.today_hours,
                f"★{pl.rating:g} ({pl.reviews})" if pl.rating is not None else ""]
        console.print(f"[bold]{i:>2}. {escape(pl.name)}[/bold]  " + escape(" · ".join(b for b in bits if b)))
        line2 = " · ".join(x for x in (pl.address, pl.phone, pl.type) if x)
        line3 = " · ".join(x for x in (_domain(pl.website), pl.place_id) if x)
        for line in (line2, line3):
            if line:
                console.print(f"    [dim]{escape(line)}[/dim]")


def _domain(url: str) -> str:
    """'https://www.example.com/a?utm=…' → 'example.com/a' (the full URL stays in --json)."""
    from urllib.parse import urlparse

    if not url:
        return ""
    u = urlparse(url)
    path = u.path.rstrip("/")
    return (u.netloc.removeprefix("www.") + (path if len(path) <= 24 else path[:24] + "…")) or url


@app.command()
def show(
    place_id: str = typer.Argument(..., help="A place_id from `hn places search`"),
    yes: bool = typer.Option(False, "--yes", help="Spend one search if the place isn't cached"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One place in full: every day's hours, type, phone, website, rating. Free when a cached search
    returned it; otherwise one live place-page lookup (with --yes)."""
    from harness.travel.providers.base import ProviderError

    try:
        place, spent = _svc().show(place_id, spend=yes)
    except ProviderError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(code=1) from e
    if place is None:
        console.print("[yellow]dry run[/yellow] — not in the cache: 1 live search · pass --yes")
        return
    if as_json:
        typer.echo(json.dumps({**place.model_dump(), "spent": spent}, indent=2))
        return
    console.print(f"[bold]{escape(place.name)}[/bold]  {escape(place.type)}"
                  + (f"  · {place.rating:g} ({place.reviews})" if place.rating is not None else ""))
    for label, val in (("address", place.address), ("phone", place.phone), ("website", place.website),
                       ("now", place.open_state), ("about", place.description)):
        if val:
            console.print(f"  {label:8} {escape(val)}")
    for day, hrs in place.hours.items():
        console.print(f"  {day.capitalize():9} {escape(hrs)}")
    console.print(f"  [dim]{'1 live search' if spent else 'cached — 0 quota'} · {place.place_id}[/dim]")
