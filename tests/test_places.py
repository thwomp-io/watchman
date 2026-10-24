"""`hn places` — the priced dry run, the near point (Google-resolved once and cached for good; lat,lon
free), nearest-first results with Google's open-now line and today's hours, `show` from the cache for
free, and the MCP twin's no-spend default. The provider's network seam is faked; no quota, no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from harness.places import mcp_server
from harness.places.service import PlacesService, haversine_km
from harness.travel.providers.base import ProviderError

Svc = tuple[PlacesService, list[dict[str, Any]]]

NEAR = {"latitude": 40.90, "longitude": -100.20}
WED = date(2026, 10, 21)


def row(title: str, lat: float, lon: float, **extra: Any) -> dict[str, Any]:
    return {"title": title, "place_id": f"pid-{title.split()[0].lower()}", "address": f"{title} St",
            "gps_coordinates": {"latitude": lat, "longitude": lon}, **extra}


ANCHOR_RAW = {
    "place_results": {"title": "12345", "address": "Springfield, ST 12345", "gps_coordinates": NEAR}
}
SEARCH_RAW = {"local_results": [
    row("Faraway Recycling", 40.99, -100.20, rating=4.1, reviews=12),
    row("Nearby E-Cycle", 40.91, -100.20, rating=4.7, reviews=88, phone="555-0100",
        open_state="Open · Closes 5 PM", website="https://nearby.example",
        operating_hours={"wednesday": "9 AM–5 PM", "thursday": "9 AM–5 PM"}),
]}


@pytest.fixture
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Svc:
    s = PlacesService("key", cache_dir=tmp_path / "places")
    calls: list[dict[str, Any]] = []

    def fake(provider: Any) -> Any:
        def raw(params: dict[str, Any]) -> dict[str, Any]:
            calls.append(params)
            provider.search_count += 1
            if "place_id" in params:
                return {"place_results": row("Paged Place", 40.95, -100.21, hours=[{"monday": "8 AM–4 PM"}])}
            return ANCHOR_RAW if "ll" not in params else SEARCH_RAW
        return raw

    monkeypatch.setattr(s.results, "_raw_search", fake(s.results))
    monkeypatch.setattr(s.anchors, "_raw_search", fake(s.anchors))
    return s, calls


def test_a_first_run_is_a_priced_dry_run(svc: Svc) -> None:
    s, calls = svc
    res = s.search("electronics recycling", "12345")
    assert res.plan.cost == 2 and not res.plan.anchor_cached and res.places == [] and res.anchor is None
    assert calls == []  # nothing spent


def test_spending_resolves_the_near_point_then_searches_nearest_first(
    svc: Svc,
) -> None:
    s, calls = svc
    res = s.search("electronics recycling", "12345", spend=True, by_distance=True, today=WED)
    assert res.spent == 2 and [c.get("q") for c in calls] == ["12345", "electronics recycling"]
    assert calls[1]["ll"] == "@40.9,-100.2,13z"
    assert res.anchor is not None and res.anchor.label == "Springfield, ST 12345"
    first = res.places[0]
    assert first.name == "Nearby E-Cycle"  # nearest first when asked, whatever Google's order
    assert first.distance_mi == pytest.approx(0.7, abs=0.05)
    assert first.open_state == "Open · Closes 5 PM"  # Google's narrow no-break spaces cleaned
    assert first.today_hours == "9 AM–5 PM" and first.phone == "555-0100"


def test_a_repeat_is_free_and_the_near_point_stays_cached(svc: Svc) -> None:
    s, calls = svc
    s.search("electronics recycling", "12345", spend=True)
    again = s.search("electronics recycling", "12345")
    assert again.spent == 0 and again.from_cache and len(again.places) == 2
    # a new query near the same ZIP: the near point costs nothing
    assert s.plan("hardware store", "12345").cost == 1


def test_coordinates_are_a_free_near_point(svc: Svc) -> None:
    s, calls = svc
    assert s.plan("hardware store", "40.9, -100.2").cost == 1
    res = s.search("hardware store", "40.9,-100.2", spend=True)
    assert res.spent == 1 and all("ll" in c for c in calls)


def test_googles_relevance_order_is_the_default(svc: Svc) -> None:
    s, _ = svc
    res = s.search("electronics recycling", "12345", spend=True)
    assert [p.name for p in res.places] == ["Faraway Recycling", "Nearby E-Cycle"]


def test_show_is_free_from_the_cache_and_priced_otherwise(svc: Svc) -> None:
    s, calls = svc
    s.search("electronics recycling", "12345", spend=True)
    n = len(calls)
    place, spent = s.show("pid-nearby")
    assert place is not None and place.website == "https://nearby.example" and spent == 0 and len(calls) == n
    assert s.show("pid-unknown") == (None, 0) and len(calls) == n  # not cached, not spent
    paged, spent = s.show("pid-unknown", spend=True)
    assert paged is not None and paged.hours == {"monday": "8 AM–4 PM"} and spent == 1


def test_an_unplaceable_near_is_an_honest_error(
    svc: Svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, _ = svc
    monkeypatch.setattr(s.anchors, "_raw_search", lambda params: {"local_results": []})
    with pytest.raises(ProviderError, match="couldn't place"):
        s.search("x", "nowhere at all", spend=True)


def test_haversine_one_degree_of_latitude() -> None:
    assert haversine_km(40.0, -100.0, 41.0, -100.0) == pytest.approx(111.2, abs=0.2)


def test_the_mcp_twin_never_spends_by_default(
    svc: Svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, calls = svc
    monkeypatch.setattr(mcp_server, "_svc", lambda: s)
    out = mcp_server.places_search("electronics recycling", "12345")
    assert out["plan"]["cost"] == 2 and out["places"] == [] and calls == []
    assert mcp_server.places_show("pid-x") == {"place": None, "spent": 0}
