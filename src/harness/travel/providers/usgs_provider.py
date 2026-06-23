"""USGS earthquake provider — KEYLESS (FDSN event web service, GeoJSON).

The geological-screen *sense*: recent real seismic activity near a place, so the harness can calibrate
the geological risk screen with real seismic data (the no-enabling data-pushback) instead of
categorical priors.
Read-only. Volcano alert levels are a planned fast-follow.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from harness._http import get_with_retry
from harness.travel.models import Earthquake
from harness.travel.providers.base import ProviderError

_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_UA = "harness/0.1 (personal travel-planning harness)"


class UsgsEarthquakeProvider:
    """Keyless USGS FDSN client. `name` participates in the EarthquakeProvider Protocol."""

    name = "usgs"

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout

    def recent_quakes(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_km: int = 300,
        since: date,
        min_magnitude: float = 2.5,
        limit: int = 20,
    ) -> list[Earthquake]:
        """Earthquakes within radius_km of (lat, lon) since `since`, ordered magnitude-desc."""
        params: dict[str, str | int] = {
            "format": "geojson",
            "latitude": str(round(latitude, 4)),
            "longitude": str(round(longitude, 4)),
            "maxradiuskm": radius_km,
            "starttime": since.isoformat(),
            "minmagnitude": str(min_magnitude),
            "limit": limit,
            "orderby": "magnitude",
        }
        try:
            resp = get_with_retry(
                _URL, params=params, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
            features = resp.json().get("features") or []
        except httpx.HTTPError as e:
            raise ProviderError(f"USGS query failed near ({latitude},{longitude}): {e}") from e
        except ValueError as e:
            raise ProviderError(f"USGS returned non-JSON: {e}") from e

        quakes: list[Earthquake] = []
        for feat in features:
            props = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            depth = float(coords[2]) if len(coords) >= 3 and coords[2] is not None else None
            quakes.append(
                Earthquake(
                    magnitude=props.get("mag"),
                    place=props.get("place") or "",
                    date=_epoch_ms_to_date(props.get("time")),
                    depth_km=depth,
                    url=props.get("url") or "",
                )
            )
        return quakes


def _epoch_ms_to_date(ms: object) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def build_usgs_provider() -> UsgsEarthquakeProvider:
    """Factory mirror of the other providers (keyless — no settings needed)."""
    return UsgsEarthquakeProvider()
