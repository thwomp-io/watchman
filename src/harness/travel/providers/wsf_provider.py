"""Washington State Ferries (WSF) — the ferry slice of the WSDOT WA-regional surface.

A sibling of the traffic provider (wsdot_provider) under the same WSDOT umbrella: same free
AccessCode (passed as `apiaccesscode` on the ferries host), read-only, .NET `/Date()/` timestamps.
Bundles the three day-of-useful ferry capabilities for the WA-region routes (each a
'Departing-Arriving' terminal pair, e.g. 'Seattle-Bainbridge Island'):

- **schedule** — today's (or a date's) sailing times for a route (scheduletoday).
- **sailing space** — live drive-up space per upcoming departure per terminal ("is it full").
- **vessel locations** — live vessel GPS + ETA + in-service/cancellation ("where's the boat").

Route names resolve to WSF terminal IDs via `terminalbasics` (name/abbrev, case-insensitive).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.travel.models import (
    FerrySailing,
    FerrySpaceDeparture,
    FerryTerminalSpace,
    FerryVessel,
)
from harness.travel.providers.wsdot_provider import _parse_dotnet_date  # shared WSDOT .NET-date parser

_BASE = "https://www.wsdot.wa.gov/ferries/api"
_TERMINAL_BASICS = f"{_BASE}/terminals/rest/terminalbasics"
_SAILING_SPACE = f"{_BASE}/terminals/rest/terminalsailingspace"
_VESSEL_LOCATIONS = f"{_BASE}/vessels/rest/vessellocations"
_SCHEDULE_TODAY = f"{_BASE}/schedule/rest/scheduletoday/{{dep}}/{{arr}}/{{only_remaining}}"

# Split a "DEP-ARR" route on the first of - / > or the word 'to' (so 'Seattle-Bainbridge Island'
# and 'Seattle to Bainbridge Island' both parse, keeping multi-word arrival names intact).
_ROUTE_SPLIT = re.compile(r"\s*(?:-|/|>|\bto\b)\s*", re.IGNORECASE)


class WsfFerryProvider:
    name = "wsf"

    def __init__(
        self, api_key: str | None, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout
        self._terminals_cache: list[dict[str, Any]] | None = None

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderError(
                "WSDOT_API_KEY is not set — cannot query WSF ferries. Register a free AccessCode at "
                "https://wsdot.wa.gov/traffic/api/ and add it to .env (WSF reuses the same code)."
            )

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _get_json(self, url: str) -> Any:
        try:
            resp = get_with_retry(
                url, params={"apiaccesscode": self._api_key or ""},
                client=self._client, timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"wsf request failed: {e}") from e
        return resp.json()

    # ---- terminal id<->name resolution ----
    def _terminals(self) -> list[dict[str, Any]]:
        if self._terminals_cache is None:
            raw = self._get_json(_TERMINAL_BASICS)
            self._terminals_cache = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
        return self._terminals_cache

    def resolve_route(self, route: str) -> tuple[int, int, str, str]:
        """Resolve a route → (dep_id, arr_id, dep_name, arr_name). Accepts 'Seattle-Bainbridge Island'
        / 'Seattle to Bainbridge Island' / 'SEA-BAI' — each side matched against terminal name/abbrev
        (case-insensitive, substring). Raises on a bad format or an unresolvable terminal."""
        self._require_key()
        parts = _ROUTE_SPLIT.split(route.strip(), maxsplit=1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ProviderError(f"route {route!r} must be 'Departing-Arriving' (e.g. 'Seattle-Bainbridge')")
        dep_id, dep_name = self._match_terminal(parts[0])
        arr_id, arr_name = self._match_terminal(parts[1])
        return dep_id, arr_id, dep_name, arr_name

    def _match_terminal(self, token: str) -> tuple[int, str]:
        t = token.strip().lower()
        for term in self._terminals():
            name = str(term.get("TerminalName", ""))
            abbrev = str(term.get("TerminalAbbrev", ""))
            if t == abbrev.lower() or t == name.lower() or t in name.lower():
                return int(term.get("TerminalID", 0)), name
        raise ProviderError(f"no WSF terminal matches {token!r}")

    # ---- schedule ----
    def schedule_today(
        self, dep_id: int, arr_id: int, *, only_remaining: bool = True
    ) -> list[FerrySailing]:
        """Today's sailing times for a route (WSF scheduletoday). `only_remaining` drops past sailings."""
        self._require_key()
        url = _SCHEDULE_TODAY.format(dep=dep_id, arr=arr_id, only_remaining=str(only_remaining).lower())
        raw = self._get_json(url)
        if not isinstance(raw, dict):
            raise ProviderError("wsf schedule returned an unexpected shape (expected an object)")
        sailings: list[FerrySailing] = []
        for combo in raw.get("TerminalCombos") or []:
            if not isinstance(combo, dict):
                continue
            for tm in combo.get("Times") or []:
                if not isinstance(tm, dict):
                    continue
                sailings.append(
                    FerrySailing(
                        departing_time=_parse_dotnet_date(tm.get("DepartingTime")),
                        arriving_time=_parse_dotnet_date(tm.get("ArrivingTime")),
                        vessel_name=str(tm.get("VesselName") or ""),
                        vessel_id=_int(tm.get("VesselID")),
                    )
                )
        return sailings

    # ---- live drive-up space ----
    def sailing_space(self, *, terminal: str | None = None) -> list[FerryTerminalSpace]:
        """Live drive-up space per upcoming departure per terminal. `terminal` filters by name/abbrev."""
        self._require_key()
        raw = self._get_json(_SAILING_SPACE)
        if not isinstance(raw, list):
            raise ProviderError("wsf sailing-space returned an unexpected shape (expected a list)")
        want = terminal.strip().lower() if terminal else None
        out: list[FerryTerminalSpace] = []
        for term in raw:
            if not isinstance(term, dict):
                continue
            name = str(term.get("TerminalName", ""))
            abbrev = str(term.get("TerminalAbbrev", ""))
            if want and want not in name.lower() and want != abbrev.lower():
                continue
            out.append(self._parse_terminal_space(term, name, abbrev))
        return out

    @classmethod
    def _parse_terminal_space(
        cls, term: dict[str, Any], name: str, abbrev: str
    ) -> FerryTerminalSpace:
        departures: list[FerrySpaceDeparture] = []
        for dep in term.get("DepartingSpaces") or []:
            if not isinstance(dep, dict):
                continue
            spaces = dep.get("SpaceForArrivalTerminals") or []
            space0 = spaces[0] if spaces and isinstance(spaces[0], dict) else {}
            departures.append(
                FerrySpaceDeparture(
                    departure=_parse_dotnet_date(dep.get("Departure")),
                    vessel_name=str(dep.get("VesselName") or ""),
                    is_cancelled=bool(dep.get("IsCancelled", False)),
                    max_space=_int(dep.get("MaxSpaceCount")),
                    drive_up_available=_int(space0.get("DriveUpSpaceCount")),
                    reservable_available=_int(space0.get("ReservableSpaceCount")),
                    arrival_terminal=str(space0.get("TerminalName") or ""),
                )
            )
        return FerryTerminalSpace(
            terminal_name=name, terminal_abbrev=abbrev, departures=departures
        )

    # ---- live vessel positions ----
    def vessel_locations(self, *, route: str | None = None) -> list[FerryVessel]:
        """Live vessel positions + ETA + in-service/cancellation. `route` filters by OpRouteAbbrev
        substring (e.g. 'edm-king') or a departing/arriving terminal name."""
        self._require_key()
        raw = self._get_json(_VESSEL_LOCATIONS)
        if not isinstance(raw, list):
            raise ProviderError("wsf vessel-locations returned an unexpected shape (expected a list)")
        want = route.strip().lower() if route else None
        out: list[FerryVessel] = []
        for v in raw:
            if not isinstance(v, dict):
                continue
            vessel = self._parse_vessel(v)
            if want and want not in (
                " ".join([*vessel.route, vessel.departing_terminal, vessel.arriving_terminal]).lower()
            ):
                continue
            out.append(vessel)
        return out

    @staticmethod
    def _parse_vessel(v: dict[str, Any]) -> FerryVessel:
        routes = v.get("OpRouteAbbrev") or []
        return FerryVessel(
            name=str(v.get("VesselName") or ""),
            departing_terminal=str(v.get("DepartingTerminalName") or ""),
            arriving_terminal=str(v.get("ArrivingTerminalName") or ""),
            latitude=_float(v.get("Latitude")),
            longitude=_float(v.get("Longitude")),
            speed=_float(v.get("Speed")),
            heading=_float(v.get("Heading")),
            in_service=bool(v.get("InService", True)),
            at_dock=bool(v.get("AtDock", False)),
            eta=_parse_dotnet_date(v.get("Eta")),
            left_dock=_parse_dotnet_date(v.get("LeftDock")),
            route=[str(r) for r in routes if isinstance(r, str)],
        )


def _int(v: object) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _float(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def build_wsf_ferry_provider(api_key: str | None) -> WsfFerryProvider:
    return WsfFerryProvider(api_key=api_key)
