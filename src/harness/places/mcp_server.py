"""MCP twin of `hn places` (mounted as places_* on the root server). Same quota rule: without
`spend`, a call that needs a live search returns its plan instead of spending."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from harness.places.service import PlacesService

mcp = FastMCP("places")


def _svc() -> PlacesService:
    from harness.places.service import PlacesService
    from harness.travel.config.settings import get_settings

    return PlacesService(get_settings().serpapi_key)


@mcp.tool()
def places_search(
    query: str, near: str, limit: int = 10, spend: bool = False, nearest: bool = False
) -> dict[str, Any]:
    """Places matching `query` near `near` (a ZIP, a place name, or "lat,lon"), in Google's relevance
    order (`nearest=True` sorts by distance): name,
    address, distance, phone, open-now / today's hours, rating, website, place_id. A run that needs a
    live SerpAPI search returns `plan` (its cost) and no places unless `spend=True`."""
    out: dict[str, Any] = _svc().search(
        query, near, limit=limit, spend=spend, by_distance=nearest
    ).model_dump()
    return out


@mcp.tool()
def places_show(place_id: str, spend: bool = False) -> dict[str, Any]:
    """One place in full (every day's hours, type, phone, website). Free when a cached search returned
    it; otherwise one live lookup, only with `spend=True` ({"place": None} until then)."""
    place, spent = _svc().show(place_id, spend=spend)
    return {"place": place.model_dump() if place else None, "spent": spent}
