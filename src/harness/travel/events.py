"""What is on: live ticketed events for a city and window, the corpus's own almanac of dated
entries, and the two merged into the calendar's per-day contract."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from harness.travel.config.settings import get_settings
from harness.travel.corpus.reference import read_reference
from harness.travel.models import CityEvents, EventResult
from harness.travel.providers import get_event_provider
from harness.travel.providers.base import ProviderError
from harness.travel.providers.geocoding import geocode

if TYPE_CHECKING:
    from harness.travel.service import TravelService


def find_events(svc: TravelService, city: str,
    start: date,
    end: date,
    *,
    classification: str | None = None,
    provider_name: str = "ticketmaster",
) -> list[EventResult]:
    """Live events in `city` within [start, end]. Raw + classified; tier with event_tier()."""
    provider = get_event_provider(provider_name)
    return provider.search_events(city, start, end, classification=classification)


def event_tier(svc: TravelService, event: EventResult) -> str:
    """'centerpiece' (NBA/NFL — trip-around-able) or 'perk', per the corpus EventWeights."""
    return svc.weights.events.tier_for(event.subgenre)


def dedupe_events(events: list[EventResult]) -> list[EventResult]:
    """Drop repeat *listings* of the same event. Tier-aware: residencies/tours (music/arts) fold
    to a single listing by name (TM lists each night separately — one 'it's in town' signal is
    enough), but SPORTS keep one-per-date — a series shares a name yet each game is a distinct
    attend-decision, and folding them hid a team's home dates. Keys on the name before any ' - '
    qualifier; sports additionally key on local_date. Order-preserving."""
    seen: set[tuple[str, str]] = set()
    out: list[EventResult] = []
    for e in events:
        base = e.name.split(" - ")[0].strip().lower()
        if not base:
            out.append(e)  # unnamed — never fold
            continue
        per_date = (e.segment or "").lower() == "sports"
        key = (base, e.local_date if per_date else "")
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def calendar_data(
    svc: TravelService, start: date, end: date, *, city: str | None = None, variant: str = "grid"
) -> dict[str, object]:
    """The interactive-calendar viz contract: the reference almanac + live
    ticketed events near home, merged into PER-DAY buckets over [start, end].

    Shape: {title, subtitle, from, to, days:[{date, items:[{label, kind, segment, tier,
    venue, time, url, source}]}], months:[...]} — `days`+`from`+`to` is the sniff signature
    (all three sniffer surfaces); `months` is derived so the SAME JSON also drives the static
    `calendar` SVG renderer (the two-consumer contract). Live events are best-effort: no
    Ticketmaster key / a transient outage yields an almanac-only calendar, never an error —
    the almanac is corpus-resident and always renders."""
    pairs: list[tuple[EventResult, str]] = [
        (e, svc.event_tier(e)) for e in svc.scan_reference(start, end)
    ]
    live_note = ""
    home = city or svc.weights.conditions.home
    try:
        if home:
            for ce in svc.scan_events([home], start, end):
                pairs += [(ev, svc.event_tier(ev)) for ev in ce.events]
    except ProviderError as live_err:
        live_note = f"live events unavailable ({live_err}) — almanac only"

    def kind_of(e: EventResult, tier: str) -> str:
        seg = (e.segment or "").lower()
        if "mega" in seg:
            return "mega-event"
        if "art" in seg or "theatre" in seg or "theater" in seg:
            return "arts"
        if seg in ("sports", "holiday", "personal", "music"):
            return seg
        return "misc"

    # Calendar-local dedupe: (base-name, date) for EVERYTHING — a calendar is a per-day
    # surface, so a residency keeps each night (unlike dedupe_events' tour-folding), and an
    # almanac entry that also appears as an identically-named live listing folds once.
    seen: set[tuple[str, str]] = set()
    by_day: dict[str, list[dict[str, object]]] = {}
    for e, tier in pairs:
        base = e.name.split(" - ")[0].strip().lower()
        key = (base, e.local_date)
        if base and key in seen:
            continue
        seen.add(key)
        by_day.setdefault(e.local_date, []).append({
            "label": e.name, "kind": kind_of(e, tier), "segment": e.segment, "tier": tier,
            "venue": e.venue, "time": e.local_time, "url": e.url, "source": e.source,
        })
    def _time_key(i: dict[str, object]) -> str:
        return str(i.get("time") or "")

    days: list[dict[str, object]] = [
        {"date": day, "items": sorted(items, key=_time_key)}
        for day, items in sorted(by_day.items())
    ]

    # months[] for the static renderer (name + {label, kind} + score = item count)
    month_items: dict[str, list[dict[str, object]]] = {}
    for day, items in sorted(by_day.items()):
        month_items.setdefault(day[:7], []).extend(
            {"label": i["label"], "kind": i["kind"]} for i in items
        )
    months = [
        {
            "name": date.fromisoformat(f"{mkey}-01").strftime("%b %Y"),
            "items": mitems,
            "score": len(mitems),
        }
        for mkey, mitems in sorted(month_items.items())
    ]

    subtitle = f"{home or 'almanac'} · {start.isoformat()} → {end.isoformat()}"
    if live_note:
        subtitle += f" · {live_note}"
    out: dict[str, object] = {
        "title": "Calendar — almanac + ticketed events",
        "subtitle": subtitle,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days,
        "months": months,
    }
    if variant == "big":
        # The sniff marker for the BIG month-board twin (one month at a time, wall-display
        # cells) — same shape otherwise, so both twins ride one emitter.
        out["variant"] = "big"
    return out


def scan_events(svc: TravelService, cities: list[str],
    start: date,
    end: date,
    *,
    classification: str | None = None,
    provider_name: str = "ticketmaster",
    radius_mi: int = 0,
) -> list[CityEvents]:
    """Scan multiple cities for events in a window — the carved multi-city path (replaces
    ad-hoc scanning). Fails fast on a missing key (a config error, not per-city), but a per-city
    transient error yields that city empty rather than killing the scan. Deduped per city.
    `radius_mi` > 0 searches a radius around each place's coordinates instead of matching the
    city name (a suburb home base has no venues of its own); a place that won't geocode
    falls back to the name match."""
    if not get_settings().ticketmaster_key:
        raise ProviderError(
            "TICKETMASTER_KEY is not set — cannot scan events. Add it to .env "
            "(Consumer Key from developer.ticketmaster.com)."
        )
    provider = get_event_provider(provider_name)
    out: list[CityEvents] = []
    for city in cities:
        near = None
        if radius_mi > 0:
            loc = geocode(city)
            if loc is not None:
                near = (loc.latitude, loc.longitude, radius_mi)
        try:
            found = provider.search_events(city, start, end, classification=classification,
                                           near=near)
        except (ProviderError, RuntimeError):
            found = []  # per-city transient — keep the rest of the scan
        out.append(CityEvents(city=city, events=dedupe_events(found)))
    return out


def scan_trip_events(svc: TravelService, trip_slug: str,
    *,
    classification: str | None = None,
    provider_name: str = "ticketmaster",
) -> list[CityEvents]:
    """Trip-scoped events — read the trip's candidate destinations from the corpus, map each to
    its event-city (weights.destination_cities), and scan that set for the trip window. The
    events twin of `rank_trip`. Candidates lacking a city mapping are skipped."""
    plan = svc.reader.build_trip_plan(trip_slug)
    end = plan.return_ or plan.depart
    cities = [
        svc.weights.destination_cities[c.slug]
        for c in plan.candidates
        if c.slug in svc.weights.destination_cities
    ]
    return svc.scan_events(
        cities, plan.depart, end, classification=classification, provider_name=provider_name
    )


def scan_reference(svc: TravelService, start: date, end: date, *, city: str | None = None
) -> list[EventResult]:
    """Static-almanac entries (source='reference') in [start, end] — the PROACTIVE layer that
    merges with the reactive live `scan_events`. Reads corpus/travel/reference/*.md
    (centerpiece games + holidays + mega-events). `city` keeps located entries matching that city
    plus all location-agnostic ones (holidays/personal dates); None returns everything in-window.
    Sorted by date. Tier with `event_tier()` like any EventResult (NFL games → centerpiece)."""
    out: list[EventResult] = []
    for e in read_reference(svc.reader._root, svc.weights.events.followed_teams):
        try:
            d = date.fromisoformat(e.local_date)
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        if city and e.city and city.lower() not in e.city.lower() and e.city.lower() not in city.lower():
            continue
        out.append(e)
    return sorted(out, key=lambda e: e.local_date)
