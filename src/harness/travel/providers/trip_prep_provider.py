"""Trip-prep enrichers — a bundle of small KEYLESS senses for trip/destination prep.

Four read-only public-data GETs, no key + no new library (the API-over-library opsec posture —
e.g. sunrise via the sunrise-sunset.org *API*, not the `astral` lib; country facts
via REST Countries, not a vendored dataset):

- **FX** (open.er-api.com) — USD-base exchange rates (also finance-useful). [frankfurter was the
  first pick but its Cloudflare host was 5xx/redirecting; open.er-api.com is the keyless USD-base swap.]
- **Public holidays** (date.nager.at) — crowds/closures timing.
- **Sun** (sunrise-sunset.org) — sunrise/sunset/twilight + golden-hour approximations.
- **Country facts** (restcountries.com) — currency / language / region / driving side / timezones.

One provider, one `_get_json` seam (monkeypatched in tests → no network).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.travel.models import CountryFacts, FxRates, Holiday, Holidays, SunTimes

_FX_URL = "https://open.er-api.com/v6/latest/{base}"
_HOLIDAYS_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
_SUN_URL = "https://api.sunrise-sunset.org/json"
_COUNTRY_URL = "https://restcountries.com/v3.1/name/{name}"
_COUNTRY_FIELDS = "name,currencies,languages,region,subregion,capital,car,timezones"


class TripPrepProvider:
    name = "trip_prep"

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _get_json(self, url: str, params: dict[str, str | int] | None = None) -> Any:
        return get_with_retry(url, params=params).json()

    def fx_rates(self, to: list[str] | None = None, *, base: str = "USD") -> FxRates:
        raw = self._get_json(_FX_URL.format(base=base.upper()))
        if not isinstance(raw, dict) or raw.get("result") != "success":
            raise ProviderError(f"FX lookup failed for base {base!r} (provider returned no rates)")
        rates = {k: float(v) for k, v in (raw.get("rates") or {}).items()}
        if to:
            wanted = {c.upper() for c in to}
            rates = {k: v for k, v in rates.items() if k in wanted}
        return FxRates(
            base=str(raw.get("base_code", base.upper())),
            date=str(raw.get("time_last_update_utc", ""))[:16],
            rates=rates,
        )

    def public_holidays(self, country: str, year: int) -> Holidays:
        raw = self._get_json(_HOLIDAYS_URL.format(year=year, country=country.upper()))
        if not isinstance(raw, list):
            raise ProviderError(f"holiday lookup failed for {country!r} {year}")
        holidays = [
            Holiday(
                date=str(h.get("date", "")),
                local_name=str(h.get("localName", "")),
                name=str(h.get("name", "")),
                nationwide=bool(h.get("global", True)),
                types=[str(t) for t in (h.get("types") or [])],
            )
            for h in raw
            if isinstance(h, dict)
        ]
        return Holidays(country=country.upper(), year=year, holidays=holidays)

    def sun_times(self, latitude: float, longitude: float, date_str: str) -> SunTimes:
        raw = self._get_json(
            _SUN_URL,
            {"lat": str(latitude), "lng": str(longitude), "date": date_str, "formatted": 0},
        )
        if not isinstance(raw, dict) or raw.get("status") != "OK":
            status = raw.get("status") if isinstance(raw, dict) else "no response"
            raise ProviderError(f"sun lookup failed ({status}) for ({latitude},{longitude}) {date_str}")
        r = raw.get("results") or {}
        sunrise, sunset = r.get("sunrise"), r.get("sunset")
        return SunTimes(
            date=date_str,
            latitude=latitude,
            longitude=longitude,
            sunrise=sunrise,
            sunset=sunset,
            solar_noon=r.get("solar_noon"),
            day_length_seconds=int(r["day_length"]) if r.get("day_length") is not None else None,
            civil_twilight_begin=r.get("civil_twilight_begin"),
            civil_twilight_end=r.get("civil_twilight_end"),
            golden_hour_morning_end=_shift_iso(sunrise, minutes=60),
            golden_hour_evening_begin=_shift_iso(sunset, minutes=-60),
        )

    def country_facts(self, name: str) -> CountryFacts:
        raw = self._get_json(_COUNTRY_URL.format(name=name), {"fields": _COUNTRY_FIELDS})
        if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict):
            raise ProviderError(f"country lookup failed for {name!r} (no match)")
        c = raw[0]
        nm = c.get("name") or {}
        currencies = {
            code: f"{cur.get('name', '')} ({cur.get('symbol', '')})".strip()
            for code, cur in (c.get("currencies") or {}).items()
            if isinstance(cur, dict)
        }
        return CountryFacts(
            name=str(nm.get("common", name)),
            official_name=str(nm.get("official", "")),
            currencies=currencies,
            languages=[str(v) for v in (c.get("languages") or {}).values()],
            region=str(c.get("region", "")),
            subregion=str(c.get("subregion", "")),
            capital=[str(x) for x in (c.get("capital") or [])],
            driving_side=str((c.get("car") or {}).get("side", "")),
            timezones=[str(t) for t in (c.get("timezones") or [])],
        )


def _shift_iso(iso: str | None, *, minutes: int) -> str | None:
    """Shift an ISO-8601 timestamp by `minutes` (for golden-hour approximations). None passes through."""
    if not iso:
        return None
    try:
        return (datetime.fromisoformat(iso) + timedelta(minutes=minutes)).isoformat()
    except ValueError:
        return None


def build_trip_prep_provider() -> TripPrepProvider:
    return TripPrepProvider()
