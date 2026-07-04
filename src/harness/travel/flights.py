"""Write a `{dest}/flights/` report from a FlightSearch — the deepen-after-pick artifact for flights.

The flights twin of lodging.py: turns live cabin-compared Google-Flights results into a corpus doc —
economy vs first side-by-side with real departure/arrival times, carriers, aircraft, layovers, legroom,
a fare-context line, the local-vs-hub airport read, and a first-class-upgrade verdict tuned to the
configured calibration. Airport identities come from the FlightSearch (config-driven) — no hardcoded codes.
Non-destructive: refuses to overwrite a human-edited report without `force`.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, datetime
from pathlib import Path

from harness.travel.media import dest_dir_parts
from harness.travel.models import FlightItinerary, FlightSearch

# First-class-upgrade calibration. The mechanism values legroom, but the preference is
# **duration-dependent**: first really matters on LONG flights (~4h+, cross-country); on SHORT hops
# it's a nice-to-have only for a modest upcharge. The bands below are tunable defaults, not laws.
_LONG_HAUL_MIN = 240  # minutes (~4h) — at/over this, first is a high preference (the broad band below)
# LONG-haul band: first ≤ ~2× economy / within a few hundred $ = worth it; ≥3× or ≥$1500 = rational-pause.
_UPGRADE_MAX_ABS = 1050.0
_UPGRADE_MAX_MULT = 2.2
_UPGRADE_DELTA_OK = 500.0
_PAUSE_MIN_ABS = 1500.0
_PAUSE_MIN_MULT = 3.0
# SHORT-hop band: only a modest upcharge justifies first (a modest upcharge yes, a large one no).
_SHORT_DELTA_OK = 200.0  # absolute delta at/under this = worth it on a short hop
_SHORT_MULT_OK = 1.5  # …or within ~1.5× economy


def fmt_time(raw: str) -> str:
    """'2026-06-19 14:25' (local airport clock) → 'Fri Jun 19, 2:25 PM'. Unparseable passes through."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M").strftime("%a %b %-d, %-I:%M %p")
    except (ValueError, TypeError):
        return raw or "—"


def fmt_dur(minutes: int | None) -> str:
    if not minutes:
        return "—"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _gflights_url(s: FlightSearch, origin: str, cabin: str) -> str:
    q = f"flights from {origin} to {s.dest_iata} on {s.depart.isoformat()}"
    if s.return_:
        q += f" returning {s.return_.isoformat()}"
    q += f" {cabin}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def _clock(iso: str) -> str:
    """'2026-06-19 14:25' → '2:25 PM' (time only; the date lives in the report header)."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d %H:%M").strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return "—"


def _dedup(seq: list[str]) -> list[str]:
    out: list[str] = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def short_aircraft(name: str) -> str:
    """Compact a verbose aircraft string for the table. 'Boeing 737-700 (Scimitar Winglets) Pax' →
    '737-700'; 'Embraer 175' → 'E175'; 'Boeing 737MAX 9 Passenger' → '737MAX 9'. (Aircraft is a real
    signal — mainline narrowbody vs. smaller regional jet.)"""
    if not name:
        return "—"
    n = re.sub(r"\(.*?\)", "", name)  # drop parentheticals
    n = re.sub(r"\b(Passenger|Pax)\b", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\s+", " ", n).strip()
    n = n.replace("Boeing ", "").replace("Airbus ", "")
    n = re.sub(r"^Embraer\s*", "E", n)
    return n.strip() or "—"


def _times_cell(it: FlightItinerary) -> str:
    if not it.legs:
        return "—"
    cell = f"{_clock(it.depart_time)} → {_clock(it.arrive_time)}"
    try:  # mark an overnight/multi-day arrival with +N
        d0 = datetime.strptime(it.depart_time, "%Y-%m-%d %H:%M").date()
        d1 = datetime.strptime(it.arrive_time, "%Y-%m-%d %H:%M").date()
        if d1 > d0:
            cell += f" +{(d1 - d0).days}"
    except (ValueError, TypeError):
        pass
    return cell


def _stops_cell(it: FlightItinerary) -> str:
    if it.is_nonstop:
        return "nonstop"
    if len(it.layovers) == 1:
        lay = it.layovers[0]
        return f"1 stop · {lay.airport or lay.name} {fmt_dur(lay.duration_minutes)}"
    pts = ", ".join(lay.airport or lay.name for lay in it.layovers)
    return f"{it.stops} stops · {pts}"


def _aircraft_cell(it: FlightItinerary) -> str:
    return " / ".join(_dedup([short_aircraft(leg.airplane) for leg in it.legs])) or "—"


def _airline_cell(it: FlightItinerary) -> str:
    return " / ".join(_dedup([leg.airline for leg in it.legs])) or "—"


def _legroom_cell(it: FlightItinerary) -> str:
    lr = it.min_legroom
    return lr.replace(" in", '"') if lr else "—"


def _detail_table(pool: list[FlightItinerary]) -> list[str]:
    rows = [
        "| Times | Stops | Dur | Airline | Aircraft | Legroom | Price |",
        "|---|---|---|---|---|---|---|",
    ]
    for it in pool:
        rows.append(
            f"| {_times_cell(it)} | {_stops_cell(it)} | {fmt_dur(it.total_duration_minutes)} "
            f"| {_airline_cell(it)} | {_aircraft_cell(it)} | {_legroom_cell(it)} "
            f"| **${it.price_usd:,.0f}** |"
        )
    return rows


def _verdict_band(
    s: FlightSearch, origin: str | None
) -> tuple[str, float, float, float, float, int] | None:
    """Classify the first-class upgrade → (band_key, econ, first, delta, mult, route_minutes). Route
    'length' is the SHORTEST option (the nonstop), not the cheapest (which may be a slow 1-stop run)."""
    econ, first = s.cheapest("economy", origin=origin), s.cheapest("first", origin=origin)
    if econ is None or first is None:
        return None
    e, f = econ.price_usd, first.price_usd
    delta, mult = f - e, (f / e if e else 99.0)
    durs = [
        o.total_duration_minutes
        for o in s.options
        if (origin is None or o.origin_iata == origin) and o.total_duration_minutes
    ]
    dur = min(durs) if durs else 0
    if delta <= 0:
        key = "cheaper"
    elif dur >= _LONG_HAUL_MIN:  # LONG haul — first is a high preference
        if f <= _UPGRADE_MAX_ABS and (mult <= _UPGRADE_MAX_MULT or delta <= _UPGRADE_DELTA_OK):
            key = "long_worth"
        elif f >= _PAUSE_MIN_ABS or mult >= _PAUSE_MIN_MULT:
            key = "long_pause"
        else:
            key = "long_judgment"
    elif delta <= _SHORT_DELTA_OK or mult <= _SHORT_MULT_OK:  # SHORT hop — modest upcharge only
        key = "short_cheap"
    else:
        key = "short_skip"
    return key, e, f, delta, mult, dur


def upgrade_verdict(s: FlightSearch, *, origin: str | None = None) -> str | None:
    """First-class-upgrade read, **duration-gated**: a high preference on long (~4h+) flights, a
    modest-upcharge-only nice-to-have on short hops. Cheapest economy vs cheapest first for the origin.
    Thresholds come from config (the calibration band)."""
    band = _verdict_band(s, origin)
    if band is None:
        return None
    key, e, f, delta, mult, dur = band
    hrs = f"~{dur // 60}h{dur % 60:02d}m" if dur else "short"
    base = f"+${delta:,.0f} ({mult:.1f}×) over economy (${e:,.0f} → ${f:,.0f})"
    return {
        "cheaper": f"✈ First class is **cheaper or equal** here (${f:,.0f} vs ${e:,.0f}) — take it.",
        "long_worth": (
            f"✈ **First-class upgrade looks worth it** — {base}, inside the configured upgrade band "
            f"(default ≤~2× / ≤~$500) on a long ({hrs}) flight where legroom matters most."
        ),
        "long_pause": f"First class is {base} — the rational-pause zone, even on a long ({hrs}) flight.",
        "long_judgment": (
            f"First class is {base} on a long ({hrs}) flight — a judgment call between the yes/no bands."
        ),
        "short_cheap": (
            f"✈ **Cheap upgrade on a short ({hrs}) hop** — only {base}; a modest upcharge worth grabbing."
        ),
        "short_skip": (
            f"First class is {base} on a short ({hrs}) hop — nice-to-have, but above the band the upgrade "
            f"is flagged as usually-skip on a short flight (a modest upcharge is taken; a large one "
            f"declined). Save first for 4h+ hauls."
        ),
    }[key]


_TAGS = {
    "cheaper": "take it — ≤ economy",
    "long_worth": "worth it",
    "long_pause": "pricey — pause",
    "long_judgment": "judgment call",
    "short_cheap": "cheap — grab it",
    "short_skip": "skip — short hop",
}


def _verdict_tag(s: FlightSearch, origin: str | None) -> str:
    band = _verdict_band(s, origin)
    return _TAGS[band[0]] if band else "—"


def _origin_label(s: FlightSearch, origin: str) -> str:
    """Display label for an origin: the local airport gets its convenience tagline appended."""
    if origin == s.home_airport and s.home_airport_note:
        return f"{origin} — {s.home_airport_note}"
    return origin


def _home_vs_hub(s: FlightSearch) -> str | None:
    """The convenience-vs-price read the user decides on: cheapest local-airport fare vs cheapest hub
    fare. Only when the local airport was queried (dest is served by it). Honest when the local airport
    has no service for the window. All airport identities are config-driven (carried on the search)."""
    home, hub, note = s.home_airport, s.comparison_airport, (s.home_airport_note or "the local airport")
    if not home or home not in s.origins:
        return None
    h = s.cheapest_overall(home)
    b = s.cheapest_overall(hub) if hub else None
    if h is None:
        return (
            f"✈ **{home} doesn't fly {s.dest_iata} on these dates** — {hub} only below. {home}'s "
            f"schedule is sparse; if the dates are flexible, shifting the window may unlock {note}."
        )
    if b is None:
        return f"✈ **{home} flies this** — from ${h.price_usd:,.0f} ({note}). (No {hub} fare returned.)"
    delta = h.price_usd - b.price_usd
    if delta <= 0:
        return (
            f"✈ **{home} wins outright** — from ${h.price_usd:,.0f} ({note}) vs {hub} "
            f"${b.price_usd:,.0f}: cheaper *and* closer. Take {home}."
        )
    return (
        f"✈ **{home} vs {hub}** — {home} from ${h.price_usd:,.0f} ({note}) vs {hub} "
        f"${b.price_usd:,.0f} = **+${delta:,.0f} for the convenience**. Worth it for the "
        f"airport experience unless the gap feels steep to you."
    )


def _fare_context(s: FlightSearch) -> str | None:
    pi = s.price_insight
    if pi is None or pi.price_level is None:
        return None
    band = ""
    if pi.typical_low and pi.typical_high:
        band = f" (typical ${pi.typical_low:,.0f}–${pi.typical_high:,.0f})"
    low = f" Lowest seen: ${pi.lowest_price:,.0f}." if pi.lowest_price else ""
    return f"Google rates the economy fare **{pi.price_level}**{band}.{low}"


def _at_a_glance(s: FlightSearch) -> list[str]:
    """The decision-at-a-glance table: cheapest economy + first per serving origin, with a short
    upgrade tag — mirrors Google Flights' 'top departing flights' before the full lists."""
    serving = s.origins_with_service()
    if not serving:
        return []
    out = [
        "## ✈ At a glance",
        "",
        "| From | Economy | First | First-class upgrade |",
        "|---|---|---|---|",
    ]
    for o in serving:
        e = s.cheapest("economy", origin=o)
        f = s.cheapest("first", origin=o)
        frm = f"**{o}** · {s.home_airport_note}" if o == s.home_airport and s.home_airport_note else o
        ecell = f"${e.price_usd:,.0f}" if e else "—"
        fcell = f"${f.price_usd:,.0f}" if f else "—"
        out.append(f"| {frm} | {ecell} | {fcell} | {_verdict_tag(s, o)} |")
    out.append("")
    return out


def write_flights_report(
    search: FlightSearch,
    dest: str,
    vault_root: Path,
    *,
    force: bool = False,
    researched_on: date | None = None,
) -> Path:
    """Write `{dest}/flights/{depart}.md` from a FlightSearch — window-stamped so a destination's
    `flights/` dir accretes a price/trend time-series across trips (one file per travel window).
    `dest` is a bare destination slug or a vault path under travel/. `researched_on` (default today)
    stamps when the snapshot was taken — the datapoint's capture date, essential for trend value.
    Returns the report path. Non-destructive: raises FileExistsError if that window's report exists
    and `force` is False (re-checking the same window overwrites with force)."""
    stamped = researched_on or date.today()
    folder_parts = dest_dir_parts(dest, vault_root)
    flights_dir = vault_root.joinpath(*folder_parts, "flights")
    report = flights_dir / f"{search.depart.isoformat()}.md"
    if report.exists() and not force:
        raise FileExistsError(f"{report} already exists — pass force=True to overwrite")
    flights_dir.mkdir(parents=True, exist_ok=True)

    trip = "round trip" if search.round_trip else "one way"
    window = search.depart.isoformat() + (f" → {search.return_.isoformat()}" if search.return_ else "")
    queried = " / ".join(search.origins)
    # Header: title + a tight metadata line + an italic caveat — a document, not a run-on note.
    lines: list[str] = [
        f"# Flights — {queried} → {search.dest_iata}",
        "",
        f"**{window}** · {trip} · researched {stamped.isoformat()}",
        "",
        "*Live Google-Flights price snapshot — fares drift; re-run `travel flights` near booking. "
        f"{trip.capitalize()} totals; outbound options shown (return chosen at booking). "
        "Booking stays manual (gated).*",
        "",
    ]
    fare = _fare_context(search)
    if fare:
        lines += [f"> {fare}", ""]

    lines += _at_a_glance(search)
    home_vs_hub = _home_vs_hub(search)
    if home_vs_hub:
        lines += [home_vs_hub, ""]

    serving = search.origins_with_service()
    if not serving:
        lines += ["*(no flights returned for any queried origin + window)*", ""]
    for origin in serving:
        lines += [f"## From {_origin_label(search, origin)}", ""]
        verdict = upgrade_verdict(search, origin=origin)
        if verdict:
            lines += [f"> {verdict}", ""]
        for cabin in search.cabins:
            pool = sorted(
                (
                    o
                    for o in search.options
                    if o.origin_iata == origin and o.cabin.lower().startswith(cabin.lower())
                ),
                key=lambda o: o.price_usd,
            )
            label = cabin.capitalize()
            lines.append(f"### {label}")
            if not pool:
                lines += ["*(none returned)*", ""]
                continue
            lines += _detail_table(pool)
            lines.append("")
        lines += [
            f"[Search {origin} on Google Flights →]({_gflights_url(search, origin, 'economy')})",
            "",
        ]

    report.write_text("\n".join(lines))
    return report
