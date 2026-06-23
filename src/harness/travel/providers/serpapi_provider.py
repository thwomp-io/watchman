"""SerpAPI / Google Flights flight provider — the v0 primary.

Full carrier coverage, price insights, mirrors the configured browse tool.
Quota-aware: counts searches; the raw-search seam (`_raw_search`) is monkeypatched in tests
so unit tests never burn the free 250/mo quota.
"""

from __future__ import annotations

import re
from typing import Any

from harness.travel.models import (
    FlightItinerary,
    FlightLayover,
    FlightLeg,
    FlightOffer,
    FlightQuery,
    PriceInsight,
)
from harness.travel.providers.base import ProviderError

_FLIGHT_NUM_IATA = re.compile(r"^([A-Z0-9]{2})\s")

# google_flights travel_class: 1=Economy 2=Premium economy 3=Business 4=First.
_CABIN_CODE = {"economy": 1, "premium": 2, "business": 3, "first": 4}


def _carrier_iata(flight_number: str | None) -> str | None:
    if not flight_number:
        return None
    m = _FLIGHT_NUM_IATA.match(flight_number)
    return m.group(1) if m else None


class SerpApiFlightProvider:
    name = "serpapi"

    def __init__(self, api_key: str | None, avoid_iata: list[str] | None = None) -> None:
        self._api_key = api_key
        self._avoid_iata = avoid_iata or []
        self.search_count = 0
        self.last_insight: PriceInsight | None = None  # from the most recent search (economy = "is it high")

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _raw_search(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError(
                "SERPAPI_KEY is not set — cannot run a live Google Flights search."
            )
        import serpapi  # imported lazily so unit tests need no network/SDK auth

        client = serpapi.Client(api_key=self._api_key)
        self.search_count += 1
        return dict(client.search(params))

    def _params(
        self, origin: str, query: FlightQuery, *, travel_class: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": query.destination,
            "outbound_date": query.depart.isoformat(),
            "currency": "USD",
            "deep_search": True,
            "stops": _stops_param(query.max_stops),
        }
        if query.return_:
            params["type"] = 1  # round trip
            params["return_date"] = query.return_.isoformat()
        else:
            params["type"] = 2  # one way
        if self._avoid_iata:
            params["exclude_airlines"] = ",".join(self._avoid_iata)
        if travel_class is not None:
            params["travel_class"] = travel_class
        return params

    def search_flights(self, query: FlightQuery) -> list[FlightOffer]:
        offers: list[FlightOffer] = []
        for origin in query.origins:
            raw = self._raw_search(self._params(origin, query))
            offer = _best_offer(raw, origin, query.destination)
            if offer is not None:
                offers.append(offer)
        return offers

    def search_itineraries(
        self, query: FlightQuery, *, cabin: str = "economy", limit: int = 3
    ) -> list[FlightItinerary]:
        """Rich, single-cabin flight options for the FIRST origin in `query` — the report-grade parse
        (per-leg times/airline/aircraft/legroom + layovers + the full top-`limit` option pool, not just
        the single best). `cabin` is economy|premium|business|first. Side effect: sets `last_insight`
        from the response's price_insights (the 'is this fare high/low' context for the report)."""
        if not query.origins:
            raise ProviderError("search_itineraries needs an origin")
        origin = query.origins[0]
        code = _CABIN_CODE.get(cabin.lower(), 1)
        raw = self._raw_search(self._params(origin, query, travel_class=code))
        self.last_insight = _parse_insight(raw)
        pool = (raw.get("best_flights") or []) + (raw.get("other_flights") or [])
        out: list[FlightItinerary] = []
        for entry in pool[:limit]:
            if isinstance(entry, dict) and entry.get("flights"):
                out.append(_parse_itinerary(entry, origin, query.destination, cabin))
        return out


def _stops_param(max_stops: int) -> int:
    # google_flights "stops": 0=any, 1=nonstop, 2=<=1 stop, 3=<=2 stops
    return {0: 1, 1: 2, 2: 3}.get(max_stops, 0)


def _parse_insight(raw: dict[str, Any]) -> PriceInsight | None:
    pi = raw.get("price_insights")
    if not isinstance(pi, dict):
        return None
    typ = pi.get("typical_price_range") or [None, None]
    return PriceInsight(
        lowest_price=pi.get("lowest_price"),
        price_level=pi.get("price_level"),
        typical_low=typ[0] if len(typ) > 0 else None,
        typical_high=typ[1] if len(typ) > 1 else None,
    )


def _best_offer(raw: dict[str, Any], origin: str, dest: str) -> FlightOffer | None:
    pool = raw.get("best_flights") or raw.get("other_flights") or []
    if not pool:
        return None
    # The first entry is Google's best-ranked option; prices are total (RT total when RT).
    entry = pool[0]
    legs = entry.get("flights") or []
    if not legs:
        return None
    first_leg = legs[0]
    layovers = entry.get("layovers") or []
    return FlightOffer(
        carrier=first_leg.get("airline", "Unknown"),
        carrier_iata=_carrier_iata(first_leg.get("flight_number")),
        origin_iata=origin,
        dest_iata=dest,
        stops=len(layovers),
        duration_minutes=int(entry.get("total_duration") or 0),
        price_usd=float(entry.get("price") or 0.0),
        deep_link=entry.get("departure_token"),
        price_insight=_parse_insight(raw),
    )


def _int_or_none(v: object) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _parse_leg(leg: dict[str, Any]) -> FlightLeg:
    dep = leg.get("departure_airport") or {}
    arr = leg.get("arrival_airport") or {}
    return FlightLeg(
        airline=str(leg.get("airline") or ""),
        flight_number=str(leg.get("flight_number") or ""),
        airplane=str(leg.get("airplane") or ""),
        depart_airport=str(dep.get("id") or ""),
        depart_name=str(dep.get("name") or ""),
        depart_time=str(dep.get("time") or ""),
        arrive_airport=str(arr.get("id") or ""),
        arrive_name=str(arr.get("name") or ""),
        arrive_time=str(arr.get("time") or ""),
        duration_minutes=_int_or_none(leg.get("duration")),
        travel_class=str(leg.get("travel_class") or ""),
        legroom=str(leg["legroom"]) if leg.get("legroom") else None,
    )


def _parse_layover(lay: dict[str, Any]) -> FlightLayover:
    return FlightLayover(
        airport=str(lay.get("id") or ""),
        name=str(lay.get("name") or ""),
        duration_minutes=_int_or_none(lay.get("duration")),
    )


def _parse_itinerary(
    entry: dict[str, Any], origin: str, dest: str, cabin: str
) -> FlightItinerary:
    legs = [_parse_leg(leg) for leg in entry.get("flights") or [] if isinstance(leg, dict)]
    layovers = [_parse_layover(lay) for lay in entry.get("layovers") or [] if isinstance(lay, dict)]
    carbon = entry.get("carbon_emissions") or {}
    grams = carbon.get("this_flight") if isinstance(carbon, dict) else None
    # Prefer the cabin Google actually returned (last leg's class) over the requested label.
    actual_cabin = legs[-1].travel_class if legs and legs[-1].travel_class else cabin
    return FlightItinerary(
        origin_iata=origin,
        dest_iata=dest,
        price_usd=float(entry.get("price") or 0.0),
        cabin=actual_cabin,
        total_duration_minutes=_int_or_none(entry.get("total_duration")),
        stops=len(layovers),
        legs=legs,
        layovers=layovers,
        booking_token=entry.get("departure_token"),
        carbon_kg=round(grams / 1000) if isinstance(grams, (int, float)) else None,
    )


def build_serpapi_provider(
    api_key: str | None, avoid_iata: list[str] | None = None
) -> SerpApiFlightProvider:
    return SerpApiFlightProvider(api_key=api_key, avoid_iata=avoid_iata)
