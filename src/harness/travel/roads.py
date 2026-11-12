"""Washington roads and ferries, live from WSDOT: travel-time delays, highway alerts, sailing
times, drive-up space and vessel positions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.travel.models import FerryReport, HighwayAlert, RoadwayLocation, TrafficReport, TravelTime
from harness.travel.providers import get_ferry_provider, get_traffic_provider
from harness.travel.providers.wsdot_provider import normalize_road

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def _loc_on_road(loc: RoadwayLocation | None, road_code: str) -> bool:
    return loc is not None and loc.road_name == road_code


def _tt_matches(
    t: TravelTime, near: str | None, road_code: str | None, congested_only: bool
) -> bool:
    if congested_only and not t.congested:
        return False
    if road_code and not (_loc_on_road(t.start_point, road_code) or _loc_on_road(t.end_point, road_code)):
        return False
    if near and near.lower() not in f"{t.name} {t.description}".lower():
        return False
    return True


def _alert_matches(
    a: HighwayAlert, near: str | None, road_code: str | None, category: str | None
) -> bool:
    if category and category.lower() not in a.category.lower():
        return False
    if road_code and not (
        _loc_on_road(a.start_location, road_code) or _loc_on_road(a.end_location, road_code)
    ):
        return False
    if near:
        parts = [a.headline, a.extended_description]
        for loc in (a.start_location, a.end_location):
            if loc:
                parts.append(loc.description)
        if near.lower() not in " ".join(parts).lower():
            return False
    return True


def get_traffic(svc: TravelService, *,
    near: str | None = None,
    road: str | None = None,
    category: str | None = None,
    congested_only: bool = False,
    include_times: bool = True,
    include_alerts: bool = True,
    congestion_threshold: int = 5,
    provider_name: str = "wsdot",
) -> TrafficReport:
    """Live WA traffic from WSDOT (keyed-free): travel-time congestion deltas + highway alerts,
    filtered for the configured corridors. `near` = case-insensitive substring over route
    name/description (times) + headline/location (alerts); `road` = a friendly highway token
    ('I-5' / '405' / 'US-2', normalized to WSDOT's code); `category` filters alert EventCategory
    (e.g. 'Construction'). `congested_only` keeps only routes delayed >= the threshold."""
    provider = get_traffic_provider(provider_name)
    road_code = normalize_road(road) if road else None
    times: list[TravelTime] = []
    alerts: list[HighwayAlert] = []
    if include_times:
        times = [
            t
            for t in provider.travel_times(congestion_threshold=congestion_threshold)
            if _tt_matches(t, near, road_code, congested_only)
        ]
    if include_alerts:
        alerts = [
            a
            for a in provider.highway_alerts()
            if _alert_matches(a, near, road_code, category)
        ]
    applied = {
        "near": near,
        "road": road,
        "category": category,
        "congested_only": "true" if congested_only else None,
    }
    return TrafficReport(
        travel_times=times,
        alerts=alerts,
        filters={k: v for k, v in applied.items() if v},
    )


def get_ferry(svc: TravelService, *,
    route: str | None = None,
    space: bool = False,
    vessels: bool = False,
    only_remaining: bool = True,
    provider_name: str = "wsf",
) -> FerryReport:
    """WSF ferry snapshot. With `route` ('Edmonds-Kingston'): today's sailing times,
    plus (if `space`) the departing terminal's live drive-up space and (if `vessels`) the vessels
    serving it. Without `route`: `space` returns all terminals' live space, `vessels` returns all
    vessels. `route` alone implies schedule; pass `space`/`vessels` to enrich it."""
    provider = get_ferry_provider(provider_name)
    report = FerryReport(route=route)
    if route:
        dep_id, arr_id, dep_name, arr_name = provider.resolve_route(route)
        report.sailings = provider.schedule_today(dep_id, arr_id, only_remaining=only_remaining)
        if space:
            report.space = provider.sailing_space(terminal=dep_name)
        if vessels:
            report.vessels = provider.vessel_locations(route=dep_name)
    else:
        if space:
            report.space = provider.sailing_space()
        if vessels:
            report.vessels = provider.vessel_locations()
    return report
