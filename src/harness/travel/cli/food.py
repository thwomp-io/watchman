"""Food discovery near a place, and photo galleries for named finalists."""

from __future__ import annotations

import json

import typer
from rich.table import Table

from harness.travel.cli.common import console
from harness.travel.service import TravelService


def food(
    near: str = typer.Option(..., "--near", help="Place name, e.g. 'Anytown, Colorado'"),
    radius: int = typer.Option(1500, "--radius", help="Search radius meters"),
    live_ratings: bool = typer.Option(
        False, "--live-ratings", help="QUOTA: merge Google ratings (1 SerpAPI search, cached; confirm first)"
    ),
    yelp: bool = typer.Option(
        False, "--yelp", help="QUOTA: merge the Yelp texture layer — snippets/neighborhoods/Yelp "
        "ratings (1 SerpAPI search, cached; confirm first)"
    ),
    yelp_query: str = typer.Option(
        "restaurants", "--yelp-query",
        help="Yelp's find_desc lens ('brunch', 'cocktail bars', 'Best dinner restaurants')",
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the ratings cache (re-spends quota)"),
    hero_images: bool = typer.Option(
        False, "--hero-images", help="KEYLESS: scrape each place's own-website og:image hero"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Eateries near a place — multi-tier food discovery (what exists + what's good + what
    locals say). Default =
    OSM Overpass enumeration (KEYLESS, free): what exists, from data not memory. --live-ratings
    layers Google ratings/price on top (1 quota search, day-cached). --yelp layers the Yelp
    texture tier — review snippets, neighborhoods, Yelp's own ratings (1 quota search, day-cached;
    --yelp-query drives the lens). --hero-images lifts og:image heroes from the places' own
    websites (keyless GETs, no quota)."""
    rep = TravelService().find_food(
        near, radius_m=radius, live_ratings=live_ratings, yelp=yelp, yelp_query=yelp_query,
        refresh=refresh, hero_images=hero_images,
    )

    if as_json:
        console.print_json(json.dumps(rep.model_dump()))
        return

    console.print(
        f"\n[bold]{rep.location}[/bold]  [dim]{rep.count} eateries within {rep.radius_m}m"
        f"{' · ratings merged' if rep.live_ratings else ''}[/dim]"
    )
    if not rep.eateries:
        console.print("[yellow]No mapped eateries — try a bigger --radius (small towns map sparse).[/yellow]")
        return
    table = Table()
    for col in ("Name", "Kind", "Cuisine", "Rating", "Price", "Hours / address", "Src"):
        table.add_column(col)
    for ea in rep.eateries:
        rating = "—" if ea.rating is None else f"{ea.rating:.1f} ({ea.reviews or 0})"
        if ea.yelp_rating is not None:
            # Both lenses shown side-by-side — cross-source divergence is signal, not noise.
            rating = f"{rating} · Y {ea.yelp_rating:.1f} ({ea.yelp_reviews or 0})"
        table.add_row(
            ea.name,
            ea.category.replace("_", " "),
            ea.cuisine or "—",
            rating,
            ea.price or "—",
            ea.opening_hours or ea.address or "—",
            "+".join(ea.sources),
        )
    console.print(table)
    for n in rep.notes:
        console.print(f"[dim]  • {n}[/dim]")


def food_photos(
    near: str = typer.Option(..., "--near", help="Same place the live-ratings sweep ran against"),
    names: str = typer.Option(
        ..., "--names", help="Comma-separated finalist names (as they appear in the sweep)"
    ),
    radius: int = typer.Option(1500, "--radius", help="Match the sweep's radius"),
    limit_per: int = typer.Option(12, "--limit-per", help="Max gallery photos per place"),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the per-place photo cache"),
    archive: str = typer.Option(
        "", "--archive",
        help="Vault-relative dir under travel/ to store the first N photos per place through the "
        "size cap (e.g. destinations/x/food/2026-09-assets) — the tool for the gallery-archive "
        "case; never curl originals into the vault",
    ),
    per_place: int = typer.Option(2, "--per-place", help="Photos stored per place with --archive"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """QUOTA (1 search PER NAME): full Google-Maps photo galleries for named finalists —
    deliberate-spend, finalists only (thumbnails already ride the live-ratings sweep free).
    Requires the sweep to have run (names resolve → data_id via its cache; the read costs 0)."""
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    svc = TravelService()
    galleries = svc.food_photos(
        near, name_list, radius_m=radius, limit_per=limit_per, refresh=refresh
    )
    stored = svc.archive_food_photos(galleries, archive, per_place=per_place) if archive else {}
    if as_json:
        console.print_json(json.dumps({"galleries": galleries, "archived": stored}))
        return
    for name, photos in galleries.items():
        console.print(f"\n[bold]{name}[/bold]  [dim]{len(photos)} photos[/dim]")
        for u in photos:
            console.print(f"  {u}")
        if not photos:
            console.print("  [yellow]no match in the sweep (name spelling? sweep not run?)[/yellow]")
        for rel in stored.get(name, []):
            console.print(f"  [green]archived[/green] {rel}")
