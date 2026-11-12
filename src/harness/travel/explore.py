"""Where can we fly, and for how much: the Explore search from each origin, merged per
destination."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from harness.travel.config.settings import get_settings
from harness.travel.models import ExploreDestination, ExploreQuery, ExploreSearch
from harness.travel.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def explore_destinations(svc: TravelService, *,
    origins: list[str] | None = None,
    outbound: date | None = None,
    return_: date | None = None,
    month: int = 0,
    duration: str = "week",
    travel_class: str = "economy",
    stops: str = "any",
    max_price: int | None = None,
    to: str | None = None,
    limit: int = 25,
    refresh: bool = False,
) -> ExploreSearch:
    """Where can we fly, and for how much: Google Travel Explore from each origin (default the
    home airport + the hub), merged per destination, cheapest first. Fares are per person;
    the avoid list is excluded in the request. Destinations with a profile in the corpus carry
    its slug (matched by airport). 1 search per origin; a 24h cache makes re-views free."""
    from harness.travel.providers.serpapi_explore_provider import build_serpapi_explore_provider

    fw = svc.weights.flight
    origins = [o.upper() for o in (origins or [fw.home_airport, fw.comparison_airport]) if o]
    if not origins:
        raise ProviderError("no origin: pass --from or set flight.home_airport in weights.yaml")
    provider = build_serpapi_explore_provider(get_settings().serpapi_key)
    by_id: dict[str, ExploreDestination] = {}
    notes: list[str] = []
    unpriced = 0
    for org in origins:
        q = ExploreQuery(
            origin=org, outbound_date=outbound, return_date=return_, month=month,
            duration=duration, travel_class=travel_class, stops=stops, max_price=max_price,
            to=to, exclude_airlines=list(fw.airline_avoid_iata),
        )
        try:
            rows = provider.explore(q, refresh=refresh)
        except ProviderError as e:
            notes.append(str(e))
            continue
        for d in rows:
            if d.fares[0].price_usd is None:
                unpriced += 1  # Google lists the place with no fare for these dates
                continue
            key = d.destination_id or d.name
            if key in by_id:
                by_id[key].fares.extend(d.fares)
            else:
                by_id[key] = d
    airports: dict[str, list[str]] = {}
    for slug, iata in svc.weights.destination_airports.items():
        airports.setdefault(str(iata).upper(), []).append(slug)
    for d in by_id.values():
        d.fares.sort(key=lambda f: (f.price_usd or 0, origins.index(f.origin)))
        d.corpus = sorted({s for f in d.fares for s in airports.get(f.airport.upper(), [])})
    dests = sorted(by_id.values(), key=lambda d: (d.best.price_usd or 0.0) if d.best else 0.0)
    if unpriced:
        notes.append(f"{unpriced} listed place(s) across the origins had no fare for these dates "
                     "(left out)")
    if outbound:
        dates = f"{outbound} → {return_}" if return_ else f"{outbound} one way"
    else:
        dates = f"{date(2000, month, 1):%b} · {duration}" if month else f"next 6 months · {duration}"
    return ExploreSearch(
        origins=origins, dates=dates, searches=provider.search_count,
        cache_hits=provider.cache_hits, destinations=dests[:limit] if limit else dests,
        notes=notes,
    )
