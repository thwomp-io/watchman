"""Places to eat near a point: what exists (OpenStreetMap, keyless), what is rated and what locals
say (two opt-in searches that spend quota), and photo galleries for named finalists."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from harness.travel.config.settings import get_settings
from harness.travel.media import archive_photo
from harness.travel.models import Eatery, FoodReport
from harness.travel.providers.base import ProviderError
from harness.travel.providers.food_provider import (
    OverpassFoodProvider,
    SerpApiLocalFoodProvider,
    merge_eateries,
    merge_yelp_eateries,
    normalize_name,
)
from harness.travel.providers.geocoding import geocode, geocode_label

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def find_food(svc: TravelService, place: str,
    *,
    radius_m: int = 1500,
    live_ratings: bool = False,
    yelp: bool = False,
    yelp_query: str | None = None,
    refresh: bool = False,
    hero_images: bool = False,
) -> FoodReport:
    """Eateries near `place` — multi-tier (what exists + what's good +
    what locals say). Tier 1 (default,
    KEYLESS): OSM Overpass enumeration — what exists, from data instead of memory. Tier 2
    (`live_ratings=True`, QUOTA — confirm first): SerpAPI google_maps ratings merged on,
    ~1 search, cached. Tier 3 (`yelp=True`, QUOTA — confirm first): the Yelp texture layer —
    review snippets + neighborhoods + Yelp's own ratings, ~1 search, cached; `yelp_query`
    drives Yelp's lens ("brunch", "cocktail bars"; default "restaurants")."""
    from harness.travel.providers.food_provider import (
        SerpApiLocalFoodProvider,
    )

    loc = geocode(place)
    if loc is None:
        raise ProviderError(f"no location match for {place!r}")
    eateries: list[Eatery] = OverpassFoodProvider().eateries_near(
        loc.latitude, loc.longitude, radius_m=radius_m
    )
    notes = [f"OSM Overpass enumeration (keyless): {len(eateries)} mapped eateries"]
    provider: SerpApiLocalFoodProvider | None = None
    if live_ratings or yelp:
        provider = SerpApiLocalFoodProvider(get_settings().serpapi_key)
    if live_ratings and provider is not None:
        rated = provider.rated_eateries(
            geocode_label(loc), loc.latitude, loc.longitude, refresh=refresh
        )
        eateries = merge_eateries(eateries, rated)
        spent = "cache (0 quota)" if provider.cache_hits else f"{provider.search_count} search"
        notes.append(
            f"Google local ratings merged: {len(rated)} rated rows · {spent}. "
            "Unrated OSM rows are below the ~20-row local-pack fold, not bad signs."
        )
    else:
        notes.append("ratings tier NOT spent (opt-in: --live-ratings) — enumeration only")
    if yelp and provider is not None:
        before = provider.search_count
        yelp_rows = provider.yelp_eateries(
            geocode_label(loc), find_desc=yelp_query or "restaurants", refresh=refresh
        )
        eateries = merge_yelp_eateries(eateries, yelp_rows)
        y_spent = "cache (0 quota)" if provider.search_count == before else "1 search"
        notes.append(
            f"Yelp texture merged ({yelp_query or 'restaurants'!s}): {len(yelp_rows)} rows · "
            f"{y_spent}. Snippets/neighborhoods ride the rows; un-snippeted rows fell below "
            "Yelp's ~10-row organic fold, not bad signs."
        )
    if hero_images:
        from harness.travel.providers.food_provider import enrich_hero_images

        hits = enrich_hero_images(eateries)
        notes.append(f"og:image heroes (keyless, own-website GETs): {hits} landed")
    return FoodReport(
        location=geocode_label(loc),
        latitude=loc.latitude,
        longitude=loc.longitude,
        radius_m=radius_m,
        count=len(eateries),
        live_ratings=live_ratings,
        eateries=eateries,
        notes=notes,
    )


def food_photos(
    svc: TravelService,
    place: str,
    names: list[str],
    *,
    radius_m: int = 1500,
    limit_per: int = 12,
    refresh: bool = False,
) -> dict[str, list[str]]:
    """Full photo galleries for NAMED finalists near `place` (google_maps_photos — 1 quota
    search PER NAME; deliberate-spend, finalists only). Resolves each name → data_id via the
    cached live-ratings sweep (run `find_food(..., live_ratings=True)` first — the sweep must
    exist or the name can't resolve; a cached sweep read costs zero)."""
    from harness.travel.providers.food_provider import (
        SerpApiLocalFoodProvider,
    )

    rep = svc.find_food(place, radius_m=radius_m, live_ratings=True, refresh=False)
    by_key = {normalize_name(e.name): e for e in rep.eateries if e.data_id}
    provider = SerpApiLocalFoodProvider(get_settings().serpapi_key)
    out: dict[str, list[str]] = {}
    for name in names:
        hit = by_key.get(normalize_name(name))
        if hit is None:
            # Sweep miss → targeted per-name lookup: the local pack favors
            # Google's prominent tier; institutions live below its fold. +1 quota search.
            hit = provider.targeted_place(name, rep.latitude, rep.longitude, refresh=refresh)
        if hit is None or not hit.data_id:
            out[name] = []  # honest miss — never fabricated
            continue
        out[name] = provider.place_photos(hit.data_id, refresh=refresh, limit=limit_per)
    return out


def archive_food_photos(
    svc: TravelService,
    galleries: dict[str, list[str]],
    rel_dir: str,
    *,
    per_place: int = 2,
    vault_root: Path | None = None,
) -> dict[str, list[str]]:
    """Store up to `per_place` gallery photos per place under `travel/{rel_dir}/` through the
    size cap; returns vault-relative paths per place (embed-ready). A dead URL is skipped."""
    root = vault_root or get_settings().tracker_path
    out: dict[str, list[str]] = {}
    for name, urls in galleries.items():
        saved: list[str] = []
        for i, url in enumerate(urls[:per_place], start=1):
            try:
                p = archive_photo(url, root, rel_dir, f"{name}-{i}")
            except (RuntimeError, OSError, ValueError):
                continue
            saved.append(p.relative_to(root).as_posix())
        out[name] = saved
    return out
