"""Open-Meteo Air-Quality provider — KEYLESS. The "is there wildfire smoke?" sense.

Sibling of the weather provider (same Open-Meteo family, same columnar shape). The air-quality API
is hourly-only, so we aggregate hourly US AQI / PM2.5 to a per-day max over the window — the
trip-planning-relevant summary ("how bad is the air on these dates"). Regional wildfire-smoke seasons
are the motivating gap. Read-only; geocoding is shared (providers/geocoding.py).
"""

from __future__ import annotations

from datetime import date

import httpx

from harness._http import get_with_retry
from harness.travel.models import DailyAirQuality
from harness.travel.providers.base import ProviderError

_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_UA = "harness/0.1 (personal travel-planning harness)"


def aqi_category(aqi: int | None) -> str:
    """US AQI value → EPA band label."""
    if aqi is None:
        return ""
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy (sensitive)"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"


class OpenMeteoAirQualityProvider:
    """Keyless Open-Meteo air-quality client. `name` participates in the AirQualityProvider Protocol."""

    name = "open-meteo-aq"

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout

    def daily_air_quality(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> tuple[list[DailyAirQuality], str, int | None]:
        """Aggregate hourly US AQI / PM2.5 to a per-day max over [start_date, end_date].

        Returns (days, resolved_timezone, current_us_aqi). Air-quality forecast horizon is shorter
        than weather (~5 days); days outside coverage simply don't come back.
        """
        params: dict[str, str | int] = {
            "latitude": str(round(latitude, 4)),
            "longitude": str(round(longitude, 4)),
            "hourly": "us_aqi,pm2_5",
            "current": "us_aqi",
            "timezone": "auto",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        try:
            resp = get_with_retry(
                _URL, params=params, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
            payload = resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"Open-Meteo air-quality failed ({start_date}→{end_date}): {e}") from e
        except ValueError as e:
            raise ProviderError(f"Open-Meteo air-quality returned non-JSON: {e}") from e

        hourly = payload.get("hourly") or {}
        times: list[str] = hourly.get("time") or []
        aqi = hourly.get("us_aqi") or []
        pm = hourly.get("pm2_5") or []

        # Group hourly samples by local date, keep the day's worst.
        by_day: dict[str, dict[str, float]] = {}
        for i, ts in enumerate(times):
            day = ts[:10]
            slot = by_day.setdefault(day, {})
            a = _num(aqi, i)
            p = _num(pm, i)
            if a is not None:
                slot["aqi"] = max(slot.get("aqi", a), a)
            if p is not None:
                slot["pm"] = max(slot.get("pm", p), p)

        days: list[DailyAirQuality] = []
        for day in sorted(by_day):
            a_max = by_day[day].get("aqi")
            aqi_int = int(a_max) if a_max is not None else None
            days.append(
                DailyAirQuality(
                    date=day,
                    us_aqi_max=aqi_int,
                    pm2_5_max=by_day[day].get("pm"),
                    category=aqi_category(aqi_int),
                )
            )

        current = payload.get("current") or {}
        cur_aqi = current.get("us_aqi")
        return days, str(payload.get("timezone") or ""), int(cur_aqi) if cur_aqi is not None else None


def _num(arr: list[object], i: int) -> float | None:
    v = arr[i] if i < len(arr) else None
    return float(v) if isinstance(v, (int, float)) else None


def build_air_quality_provider() -> OpenMeteoAirQualityProvider:
    """Factory mirror of the other providers (keyless — no settings needed)."""
    return OpenMeteoAirQualityProvider()
