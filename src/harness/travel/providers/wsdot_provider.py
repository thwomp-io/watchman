"""WSDOT Traveler Information API — WA-regional traffic provider (keyed-free).

Washington State DOT exposes a generous keyed-free REST/JSON API (free AccessCode by email reg;
the code is notify-only, not shared). WA-government open data → reliable, no ToS-gray, PII-free at
call time. This provider bundles the *traffic* slice — live Travel Times (congestion deltas on
instrumented metro corridors) + Highway Alerts (construction / closure / incident) —
behind the shared AccessCode and a single `_get_json` seam (the TripPrepProvider shape). Ferries /
borders / passes are separate slices of the same WA-regional surface.

Timestamps come back as Microsoft .NET `/Date(epoch_ms±hhmm)/` — `_parse_dotnet_date` normalizes
them to ISO-8601 UTC. https://wsdot.wa.gov/traffic/api/
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.travel.models import HighwayAlert, RoadwayLocation, TravelTime

_TRAVEL_TIMES = "https://wsdot.wa.gov/Traffic/api/TravelTimes/TravelTimesREST.svc/GetTravelTimesAsJson"
_ALERTS = "https://wsdot.wa.gov/Traffic/api/HighwayAlerts/HighwayAlertsREST.svc/GetAlertsAsJson"

_DOTNET_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")
# Strip a friendly highway prefix so 'I-5' / 'US-2' / 'SR 520' reduce to the bare number we zero-pad
# to WSDOT's RoadName code ('005' / '002' / '520'). Interstates/US routes are 3-digit zero-padded.
_ROAD_PREFIX = re.compile(r"^(?:I|US|SR|WA|HWY)[\s-]*", re.IGNORECASE)


def _parse_dotnet_date(value: object) -> str | None:
    """`/Date(1780721400000-0700)/` → ISO-8601 UTC. The number is epoch-ms (UTC); the ±hhmm is
    WSDOT's local offset (informational — we canonicalize to UTC). None / unparseable → None."""
    if not value:
        return None
    m = _DOTNET_DATE.search(str(value))
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def normalize_road(road: str) -> str:
    """User-friendly highway token → WSDOT RoadName code. 'I-5'/'i5'/'5' → '005'; 'US-2'/'2' → '002';
    '405' → '405'; '522' → '522'; 'SR 520' → '520'. Non-numeric (e.g. 'Airports') → uppercased as-is."""
    s = _ROAD_PREFIX.sub("", road.strip()).strip()
    return s.zfill(3) if s.isdigit() else road.strip().upper()


def _as_float(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _as_int(v: object) -> int | None:
    # WSDOT returns numerics as JSON numbers; truncate floats (e.g. a stray decimal) to int.
    return int(v) if isinstance(v, (int, float)) else None


class WsdotTrafficProvider:
    name = "wsdot"

    def __init__(
        self, api_key: str | None, client: httpx.Client | None = None, timeout: float = 20.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderError(
                "WSDOT_API_KEY is not set — cannot query WSDOT. Register a free AccessCode at "
                "https://wsdot.wa.gov/traffic/api/ and add it to .env."
            )

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _get_json(self, url: str) -> Any:
        try:
            resp = get_with_retry(
                url, params={"AccessCode": self._api_key or ""},
                client=self._client, timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"wsdot request failed: {e}") from e
        return resp.json()

    def travel_times(self, *, congestion_threshold: int = 5) -> list[TravelTime]:
        """Every instrumented route's live vs typical drive time. `congestion_threshold` (minutes over
        average) flags a route congested. Raises ProviderError on a missing key or unexpected shape."""
        self._require_key()
        raw = self._get_json(_TRAVEL_TIMES)
        if not isinstance(raw, list):
            raise ProviderError("wsdot travel-times returned an unexpected shape (expected a list)")
        return [
            self._parse_travel_time(r, congestion_threshold) for r in raw if isinstance(r, dict)
        ]

    def highway_alerts(self) -> list[HighwayAlert]:
        """Every active highway alert (construction / closure / incident / maintenance)."""
        self._require_key()
        raw = self._get_json(_ALERTS)
        if not isinstance(raw, list):
            raise ProviderError("wsdot highway-alerts returned an unexpected shape (expected a list)")
        return [self._parse_alert(r) for r in raw if isinstance(r, dict)]

    @staticmethod
    def _loc(d: object) -> RoadwayLocation | None:
        if not isinstance(d, dict):
            return None
        return RoadwayLocation(
            description=str(d.get("Description") or ""),
            direction=str(d.get("Direction") or ""),
            latitude=_as_float(d.get("Latitude")),
            longitude=_as_float(d.get("Longitude")),
            milepost=_as_float(d.get("MilePost")),
            road_name=str(d.get("RoadName") or ""),
        )

    @classmethod
    def _parse_travel_time(cls, r: dict[str, Any], threshold: int) -> TravelTime:
        avg, cur = _as_int(r.get("AverageTime")), _as_int(r.get("CurrentTime"))
        # WSDOT uses 0 (and occasionally negatives) for "no current reading" — treat as no-delay-data.
        delay = (cur - avg) if (avg and cur and avg > 0 and cur > 0) else None
        return TravelTime(
            route_id=_as_int(r.get("TravelTimeID")) or 0,
            name=str(r.get("Name") or ""),
            description=str(r.get("Description") or ""),
            distance_miles=_as_float(r.get("Distance")),
            average_minutes=avg,
            current_minutes=cur,
            delay_minutes=delay,
            congested=delay is not None and delay >= threshold,
            start_point=cls._loc(r.get("StartPoint")),
            end_point=cls._loc(r.get("EndPoint")),
            updated=_parse_dotnet_date(r.get("TimeUpdated")),
        )

    @classmethod
    def _parse_alert(cls, r: dict[str, Any]) -> HighwayAlert:
        return HighwayAlert(
            alert_id=_as_int(r.get("AlertID")) or 0,
            category=str(r.get("EventCategory") or ""),
            status=str(r.get("EventStatus") or ""),
            priority=str(r.get("Priority") or ""),
            region=str(r.get("Region") or ""),
            county=str(r["County"]) if r.get("County") else None,
            headline=str(r.get("HeadlineDescription") or ""),
            extended_description=str(r.get("ExtendedDescription") or ""),
            start_location=cls._loc(r.get("StartRoadwayLocation")),
            end_location=cls._loc(r.get("EndRoadwayLocation")),
            start_time=_parse_dotnet_date(r.get("StartTime")),
            end_time=_parse_dotnet_date(r.get("EndTime")),
            last_updated=_parse_dotnet_date(r.get("LastUpdatedTime")),
        )


def build_wsdot_traffic_provider(api_key: str | None) -> WsdotTrafficProvider:
    return WsdotTrafficProvider(api_key=api_key)
