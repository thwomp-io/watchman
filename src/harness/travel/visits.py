"""A visit's (or trip's) bank of outings and restaurants and its day-by-day schedule: the reads
behind the Visits and Trips tabs, and the two enrichers that fill a bank in."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from harness.travel import bank as _bank
from harness.travel import bank_enrich as _be
from harness.travel import bank_photos as _bp
from harness.travel import schedule as _sched
from harness.travel.bank import VisitBank
from harness.travel.config.settings import get_settings
from harness.travel.media import _download_bytes
from harness.travel.providers.base import ProviderError
from harness.travel.providers.food_provider import SerpApiLocalFoodProvider
from harness.travel.schedule import VisitSchedule

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def _auto(slug: str | None) -> str | None:
    """The console's picker sends "auto" (or nothing) for 'the default visit/trip'."""
    return None if slug is None or slug.strip() in ("", "auto") else slug.strip()


def visit_bank(svc: TravelService, slug: str | None = None, kind: str | None = None, *, scope: str = "visit"
) -> VisitBank:
    """The Visits/Trips tab's tile source: the soonest live visit (or trip, by `scope`) whose
    folder carries an outings and/or restaurant bank — or `slug` — merged into one item
    contract (`travel.bank`). Corpus-driven: no slug lives in dashboard config; the console's
    picker passes one, or "auto" for the default."""
    slug = _auto(slug)
    vault_root = svc.reader._root.parent
    trip = _bank.resolve_visit(svc.reader.scan_trips(), vault_root, slug, scope)
    if trip is None:
        if slug:
            raise ValueError(f"no trip or visit named {slug!r}")
        return _bank.empty_bank()
    return _bank.build_bank(trip, vault_root, _bank.bank_kind(kind))


def visit_bank_enrich(
    svc: TravelService,
    slug: str | None = None,
    *,
    scope: str = "visit",
    dry_run: bool = True,
    refresh: bool = False,
    cached_only: bool = False,
) -> dict[str, Any]:
    """Hours, booking/order links, phone, coordinates and place ids for a bank's places
    (`travel.bank_enrich`) — from the cache for free, one search per uncached place."""

    bank = svc.visit_bank(slug, scope=scope)
    if not bank.visit:
        raise ValueError(f"no live {scope} carries a bank")
    trip = next(t for t in svc.reader.scan_trips() if t.slug == bank.visit)
    place = svc.weights.conditions.home if trip.kind == "visit" else trip.destination
    loc = svc.geocode_place(place) if place else None
    center = (loc.latitude, loc.longitude) if loc else (0.0, 0.0)
    provider = SerpApiLocalFoodProvider(get_settings().serpapi_key)
    run = _be.enrich(
        bank, svc.reader._root.parent, provider, center, dry_run=dry_run, refresh=refresh,
        cached_only=cached_only,
    )
    return run.as_dict()


def visit_bank_photos(svc: TravelService, slug: str | None = None,
    kind: str | None = None,
    *,
    ids: set[str] | None = None,
    per_item: int = 8,
    refresh: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch per-item photo galleries for a visit's bank (`travel.bank_photos`): QUOTA — up to
    2 SerpAPI searches per item (place lookup + gallery), fewer when the place is already in
    the sidecar or the provider cache. `dry_run` prices it without spending."""

    from harness.travel.providers.food_provider import SerpApiLocalFoodProvider

    bank = svc.visit_bank(slug, kind)
    if not bank.visit:
        raise ValueError("no live visit carries a bank")
    home_locale = svc.weights.conditions.home
    loc = svc.geocode_place(home_locale) if home_locale else None
    if loc is None:
        raise ValueError("conditions.home does not geocode — the place search needs a center")
    provider = SerpApiLocalFoodProvider(get_settings().serpapi_key)
    vault_root = svc.reader._root.parent
    with httpx.Client(follow_redirects=True) as client:
        run = _bp.plan_and_fetch(
            bank, vault_root, provider, (loc.latitude, loc.longitude),
            lambda url: _download_bytes(url, "", client, 30.0),
            kind=_bank.bank_kind(kind), ids=ids, per_item=per_item, refresh=refresh,
            dry_run=dry_run,
        )
    return run.as_dict()


def visit_schedule(
    svc: TravelService,
    slug: str | None = None,
    *,
    weather: bool = True,
    today: date | None = None,
    scope: str = "visit",
) -> VisitSchedule:
    """The Visits tab's timeline source: the soonest live visit whose folder carries a
    schedule.json (or `slug`), one column per date with items, markers, availability, the
    home-locale forecast (clamped to the ~16-day horizon) and the visit's bank."""


    slug = _auto(slug)
    vault_root = svc.reader._root.parent
    trip = _sched.resolve_visit(svc.reader.scan_trips(), vault_root, slug, scope)
    if trip is None:
        if slug:
            raise ValueError(f"no trip or visit named {slug!r}")
        return _sched.empty_schedule(_bank.empty_bank())
    bank = _bank.build_bank(trip, vault_root)
    raw = _sched.read_schedule((vault_root / trip.doc).parent) or {}
    start = _sched._date(raw.get("start")) or trip.start
    end = _sched._date(raw.get("end")) or trip.end or start
    today = today or date.today()
    # a visit happens at home; a trip's weather is its destination's
    home = svc.weights.conditions.home if trip.kind == "visit" else (trip.destination or "")
    forecast: dict[str, Any] = {}
    note, place, punit, tunit = "", home, "inch", "°F"
    if not weather or not home or start is None or end is None:
        note = "" if not weather else "no forecast place (home locale / trip destination)"
    else:
        lo, hi = max(start, today), min(end, today + timedelta(days=15))
        if lo > hi:
            note = "past the ~16-day forecast horizon" if start > today else f"{trip.kind} is over"
        else:
            try:
                fc = svc.get_weather(home, lo, hi)
                forecast = {d.date: d for d in fc.days}
                place, punit, tunit = fc.location, fc.precipitation_unit, fc.temperature_unit
                if hi < end:
                    note = f"forecast through {hi.isoformat()} — later days are past the ~16-day horizon"
            except ProviderError as err:
                note = f"forecast unavailable ({err})"
    return _sched.build_schedule(
        trip, vault_root, bank, forecast=forecast, weather_place=place, weather_note=note,
        thresholds=svc.weights.conditions.thresholds, precip_unit=punit, temp_unit=tunit,
    )
