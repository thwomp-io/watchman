"""Open-Meteo weather provider — KEYLESS, free, global.

The harness's weather *sense*: a live daily forecast for a window, so recommendations reason about
the *actual* conditions on the trip dates rather than climate-averages alone (an average is a prior,
never a write-off). Two keyless endpoints:
  - geocoding-api.open-meteo.com/v1/search  → city name → lat/lon
  - api.open-meteo.com/v1/forecast          → daily forecast for lat/lon over a date window

Forecast horizon is ~16 days ahead; windows beyond that raise ProviderError (the climate-normals
fallback is a documented fast-follow). Read-only.
"""

from __future__ import annotations

from datetime import date

import httpx

from harness._http import get_with_retry
from harness.travel.models import CurrentConditions, DailyWeather, GeoLocation, HourlyWeather
from harness.travel.providers.base import ProviderError
from harness.travel.providers.geocoding import geocode as _geocode

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_UA = "harness/0.1 (personal travel-planning harness)"

# Daily fields requested (parallel columnar arrays in the response).
# precipitation_hours + snowfall_sum ride the same call (free) — the conditions-watch wet_day
# (rain by DURATION, not probability) + snow flags read them.
# v1.5 (the cutting-room-floor audit): apparent temps (heat is a FEELS question),
# UV max, gust max, sunrise/sunset — all same-call, zero extra quota.
_DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum,precipitation_hours,snowfall_sum,"
    "apparent_temperature_max,apparent_temperature_min,uv_index_max,wind_gusts_10m_max,"
    "sunrise,sunset"
)

# Hourly fields for the outdoor-windows solver (tier B) — the five that decide
# "is this a good hour to be outside". A separate fetch by design: hourly rides only when the
# solver runs (the daily surface stays one cheap call for every other consumer).
_HOURLY = "temperature_2m,apparent_temperature,precipitation_probability,uv_index,wind_gusts_10m"

# Current-conditions vars (tier C, the tile row's "now" half) — rides the hourly call via
# `current=` (same endpoint, same fetch — the full-response-surface rule: no third request).
_CURRENT = "temperature_2m,apparent_temperature,uv_index,wind_gusts_10m,weather_code"

# WMO weather interpretation codes → human-readable condition.
_WMO: dict[int, str] = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
}


def describe_wmo(code: int) -> str:
    """WMO weather code → label (unknown codes degrade gracefully to the raw code)."""
    return _WMO.get(code, f"Code {code}")


class OpenMeteoWeatherProvider:
    """Keyless Open-Meteo client. `name` participates in the WeatherProvider Protocol."""

    name = "open-meteo"

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout

    def geocode(self, query: str) -> GeoLocation | None:
        """Resolve a place name to its top geocoding match (delegates to the shared geocoder)."""
        return _geocode(query, client=self._client, timeout=self._timeout)

    def daily_forecast(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        *,
        fahrenheit: bool = True,
    ) -> tuple[list[DailyWeather], str]:
        """Daily forecast for a lat/lon over [start_date, end_date]. Returns (days, resolved_tz).

        Units default to Fahrenheit / inch (US-default; configurable); pass fahrenheit=False for metric.
        """
        params: dict[str, str | int] = {
            "latitude": str(round(latitude, 4)),
            "longitude": str(round(longitude, 4)),
            "daily": _DAILY,
            "timezone": "auto",
            "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
            "precipitation_unit": "inch" if fahrenheit else "mm",
            "wind_speed_unit": "mph" if fahrenheit else "kmh",  # gusts in the user's unit system
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        try:
            resp = get_with_retry(
                _FORECAST_URL, params=params, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
            payload = resp.json()
        except httpx.HTTPError as e:
            # Open-Meteo 400s a window past the ~16-day forecast horizon (with a JSON `reason`).
            reason = _extract_reason(e)
            raise ProviderError(
                f"Open-Meteo forecast failed ({start_date}→{end_date}): {reason or e}. "
                "Note: the forecast horizon is ~16 days; longer-range needs climate normals (planned)."
            ) from e
        except ValueError as e:
            raise ProviderError(f"Open-Meteo returned non-JSON: {e}") from e

        daily = payload.get("daily") or {}
        times: list[str] = daily.get("time") or []
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        precip_prob = daily.get("precipitation_probability_max") or []
        precip_sum = daily.get("precipitation_sum") or []
        precip_hours = daily.get("precipitation_hours") or []
        snowfall_sum = daily.get("snowfall_sum") or []
        feels_max = daily.get("apparent_temperature_max") or []
        feels_min = daily.get("apparent_temperature_min") or []
        uv_max = daily.get("uv_index_max") or []
        gusts_max = daily.get("wind_gusts_10m_max") or []
        sunrise = daily.get("sunrise") or []
        sunset = daily.get("sunset") or []

        days: list[DailyWeather] = []
        for i, day in enumerate(times):
            code = int(codes[i]) if i < len(codes) and codes[i] is not None else -1
            days.append(
                DailyWeather(
                    date=day,
                    weather_code=code,
                    condition=describe_wmo(code) if code >= 0 else "Unknown",
                    temp_max=_at(highs, i),
                    temp_min=_at(lows, i),
                    precip_prob=_int_at(precip_prob, i),
                    precip_sum=_at(precip_sum, i),
                    precip_hours=_at(precip_hours, i),
                    snowfall_sum=_at(snowfall_sum, i),
                    feels_max=_at(feels_max, i),
                    feels_min=_at(feels_min, i),
                    uv_index_max=_at(uv_max, i),
                    wind_gusts_max=_at(gusts_max, i),
                    sunrise=_str_at(sunrise, i),
                    sunset=_str_at(sunset, i),
                )
            )
        return days, str(payload.get("timezone") or "")

    def hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        *,
        fahrenheit: bool = True,
    ) -> tuple[list[HourlyWeather], CurrentConditions | None]:
        """Hourly forecast over [start_date, end_date] + the CURRENT read — the outdoor-windows
        solver's substrate + the tile row's "now" half (tiers B+C). One fetch: the
        `current=` vars ride the hourly call (full-response-surface — no third request). Same
        units contract as the daily surface. Current degrades to None, never fails the hours."""
        params: dict[str, str | int] = {
            "latitude": str(round(latitude, 4)),
            "longitude": str(round(longitude, 4)),
            "hourly": _HOURLY,
            "current": _CURRENT,
            "timezone": "auto",
            "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
            "wind_speed_unit": "mph" if fahrenheit else "kmh",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        try:
            resp = get_with_retry(
                _FORECAST_URL, params=params, headers={"User-Agent": _UA},
                client=self._client, timeout=self._timeout,
            )
            payload = resp.json()
        except httpx.HTTPError as e:
            reason = _extract_reason(e)
            raise ProviderError(
                f"Open-Meteo hourly failed ({start_date}→{end_date}): {reason or e}"
            ) from e
        except ValueError as e:
            raise ProviderError(f"Open-Meteo returned non-JSON: {e}") from e

        hourly = payload.get("hourly") or {}
        times: list[str] = hourly.get("time") or []
        temp = hourly.get("temperature_2m") or []
        feels = hourly.get("apparent_temperature") or []
        prob = hourly.get("precipitation_probability") or []
        uv = hourly.get("uv_index") or []
        gusts = hourly.get("wind_gusts_10m") or []
        hours = [
            HourlyWeather(
                time=t,
                temp=_at(temp, i),
                feels=_at(feels, i),
                precip_prob=_int_at(prob, i),
                uv=_at(uv, i),
                gusts=_at(gusts, i),
            )
            for i, t in enumerate(times)
        ]
        return hours, _parse_current(payload.get("current"))


def _at(arr: list[object], i: int) -> float | None:
    v = arr[i] if i < len(arr) else None
    return float(v) if isinstance(v, (int, float)) else None


def _parse_current(cur: object) -> CurrentConditions | None:
    """The `current=` block → model; anything unexpected degrades to None (best-effort by design)."""
    if not isinstance(cur, dict):
        return None
    code = cur.get("weather_code")
    icode = int(code) if isinstance(code, (int, float)) else -1

    def num(key: str) -> float | None:
        v = cur.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    return CurrentConditions(
        time=str(cur.get("time") or ""),
        temp=num("temperature_2m"),
        feels=num("apparent_temperature"),
        uv=num("uv_index"),
        gusts=num("wind_gusts_10m"),
        weather_code=icode,
        condition=describe_wmo(icode) if icode >= 0 else "",
    )


def _str_at(arr: list[object], i: int) -> str:
    v = arr[i] if i < len(arr) else None
    return str(v) if isinstance(v, str) else ""


def _int_at(arr: list[object], i: int) -> int | None:
    v = arr[i] if i < len(arr) else None
    return int(v) if isinstance(v, (int, float)) else None


def _extract_reason(e: httpx.HTTPError) -> str | None:
    resp = getattr(e, "response", None)
    if resp is None:
        return None
    try:
        return str(resp.json().get("reason") or "")
    except ValueError:
        return None


def build_open_meteo_provider() -> OpenMeteoWeatherProvider:
    """Factory mirror of the other providers (keyless — no settings needed)."""
    return OpenMeteoWeatherProvider()
