"""Booking research: flight search and ranking, Google Travel Explore, hotels, and the SerpAPI quota."""

from __future__ import annotations

import json
from datetime import date

import typer
from rich.table import Table

from harness.errors import HarnessError
from harness.travel.cli.common import console, travel_service
from harness.travel.config.settings import get_settings
from harness.travel.models import Shortlist
from harness.travel.providers.base import ProviderError
from harness.travel.service import TravelService


def search(
    to: str = typer.Option(..., "--to", help="Destination IATA (e.g. DEN)"),
    depart: str = typer.Option(..., "--depart", help="YYYY-MM-DD"),
    ret: str | None = typer.Option(None, "--return", help="YYYY-MM-DD (round trip)"),
    origins: str | None = typer.Option(
        None, "--from", help="Comma-separated origin IATAs (default: configured home airport + hub)"
    ),
    provider: str = typer.Option("serpapi", "--provider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Raw flight search across one or more origins."""
    svc = travel_service(provider)
    origin_list = (
        [o.strip().upper() for o in origins.split(",")]
        if origins
        else svc.weights.flight.query_origins(home_served=True)
    )
    offers = svc.search_flights(
        origins=origin_list,
        destination=to.upper(),
        depart=date.fromisoformat(depart),
        return_=date.fromisoformat(ret) if ret else None,
    )

    if as_json:
        console.print_json(json.dumps([o.model_dump() for o in offers]))
        return
    table = Table(title=f"Flights → {to.upper()}")
    for col in ("Origin", "Carrier", "Stops", "Duration", "Price"):
        table.add_column(col)
    for o in offers:
        table.add_row(
            o.origin_iata, o.carrier, str(o.stops),
            f"{o.duration_hours:.1f}h", f"${o.price_usd:.0f}",
        )
    console.print(table)


def flights(
    to: str = typer.Option(..., "--to", help="Destination IATA (e.g. SAN)"),
    depart: str = typer.Option(..., "--depart", help="YYYY-MM-DD"),
    ret: str | None = typer.Option(None, "--return", help="YYYY-MM-DD (round trip)"),
    origins: str | None = typer.Option(
        None, "--origins", help="Comma-separated origins (default: home airport + hub when served, else hub)"
    ),
    cabins: str = typer.Option(
        "economy,first", "--cabins", help="Comma-separated: economy,premium,business,first"
    ),
    limit: int = typer.Option(3, "--limit", help="Options per cabin"),
    report: str = typer.Option(
        "", "--report", help="Write a {dest}/flights/ report (slug or vault path under travel/)"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing flights report"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Cabin-aware flight research across origins — the configured home airport + a hub side-by-side
    for served dests, economy vs first, real times/layovers/legroom + a first-class-upgrade verdict.
    The flights twin of `hn travel hotels`; `--report` persists a {dest}/flights/ doc. ~1 search per
    origin × cabin (default 4)."""
    from harness.travel.flights import _home_vs_hub, fmt_dur, fmt_time, upgrade_verdict

    cabin_list = [c.strip().lower() for c in cabins.split(",") if c.strip()]
    origin_list = [o.strip().upper() for o in origins.split(",")] if origins else None
    search = TravelService().research_flights(
        to,
        date.fromisoformat(depart),
        date.fromisoformat(ret) if ret else None,
        origins=origin_list,
        cabins=tuple(cabin_list),
        limit=limit,
    )

    if as_json:
        console.print_json(json.dumps(search.model_dump(mode="json")))
    else:
        trip = "round trip" if search.round_trip else "one way"
        win = f"{search.depart}" + (f" → {search.return_}" if search.return_ else "")
        orgs = "/".join(search.origins)
        console.print(
            f"\n[bold]Flights {orgs} → {search.dest_iata}[/bold] [dim]{win} · {trip}[/dim]"
        )
        hvh = _home_vs_hub(search)
        if hvh:
            console.print(f"  {hvh}")
        serving = search.origins_with_service()
        if not serving:
            console.print("[dim]  (no flights returned for any queried origin)[/dim]")
        for origin in serving:
            v = upgrade_verdict(search, origin=origin)
            label = (
                f"{origin} ({search.home_airport_note})"
                if origin == search.home_airport and search.home_airport_note
                else origin
            )
            console.print(f"\n[bold]From {label}[/bold]" + (f"\n  {v}" if v else ""))
            for cabin in search.cabins:
                pool = sorted(
                    (
                        o
                        for o in search.options
                        if o.origin_iata == origin and o.cabin.lower().startswith(cabin.lower())
                    ),
                    key=lambda o: o.price_usd,
                )
                console.print(f"[bold]{cabin.capitalize()}[/bold] [dim]({len(pool)})[/dim]")
                if not pool:
                    console.print("[dim]  (none)[/dim]")
                    continue
                table = Table()
                for col in ("Price", "Depart", "Arrive", "Carrier", "Stops", "Dur", "Legroom"):
                    table.add_column(col)
                for o in pool:
                    table.add_row(
                        f"${o.price_usd:,.0f}", fmt_time(o.depart_time), fmt_time(o.arrive_time),
                        o.primary_airline, "nonstop" if o.is_nonstop else f"{o.stops}",
                        fmt_dur(o.total_duration_minutes), o.min_legroom or "—",
                    )
                console.print(table)

    if report:
        try:
            path = TravelService().write_flights_report(search, report, force=force)
            console.print(f"\n[bold green]Flights report written:[/bold green] {path}")
        except FileExistsError as e:
            raise HarnessError(str(e)) from e


def rank(
    trip: str = typer.Option(..., "--trip", help="Trip slug, e.g. 2027-01-sample-trip"),
    provider: str = typer.Option("serpapi", "--provider"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Rank a trip's candidate destinations against live flight data + the screens/home-airport weight."""
    shortlist = travel_service(provider).rank_trip(trip)

    if as_json:
        console.print_json(shortlist.model_dump_json())
        return
    _render_shortlist(shortlist)


def explore(
    origins: str = typer.Option(
        "", "--from", help="Origin IATA codes, comma-separated (default: home airport + hub)"
    ),
    month: int = typer.Option(0, "--month", help="Flexible dates: month 1-12 (0 = the next six months)"),
    duration: str = typer.Option(
        "week", "--duration", help="Flexible trip length: weekend | week | two-weeks"
    ),
    depart: str = typer.Option("", "--depart", help="Exact dates instead: outbound YYYY-MM-DD"),
    return_date: str = typer.Option("", "--return", help="Exact return YYYY-MM-DD (omit = one way)"),
    cabin: str = typer.Option("economy", "--class", help="economy | premium | business | first"),
    stops: str = typer.Option("any", "--stops", help="any | nonstop | 1 | 2 (max stops)"),
    max_price: int = typer.Option(0, "--max-price", help="Per-person round-trip USD ceiling (0 = none)"),
    to: str = typer.Option(
        "", "--to", help="Region (caribbean, mexico, hawaii, central-america, south-america, europe, "
        "asia, japan, canada), a Google /m/ id, or one airport code (default: Google's picks)",
    ),
    limit: int = typer.Option(25, "--limit", help="Rows to show (0 = all)"),
    refresh: bool = typer.Option(False, "--refresh", help="Force fresh searches (skip the 24h cache)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Where can we fly, and for how much: Google Travel Explore (via SerpAPI) lists destinations
    with the cheapest per-person fare from each origin, for exact dates or a flexible month + trip
    length. Merged across origins, cheapest first; the avoid list is excluded; destinations already
    profiled in the corpus are marked. **Quota: 1 search per origin** (24h cache, re-views free)."""
    svc = TravelService()
    try:
        res = svc.explore_destinations(
            origins=[o.strip() for o in origins.split(",") if o.strip()] or None,
            outbound=date.fromisoformat(depart) if depart else None,
            return_=date.fromisoformat(return_date) if return_date else None,
            month=month, duration=duration, travel_class=cabin, stops=stops,
            max_price=max_price or None, to=to or None, limit=limit, refresh=refresh,
        )
    except (ProviderError, ValueError) as e:
        raise HarnessError(str(e)) from e
    if as_json:
        console.print_json(json.dumps(res.model_dump(mode="json")))
        return
    spent = f"{res.searches} live search(es) — {res.searches} quota" if res.searches else "cached — 0 quota"
    console.print(f"\n[bold]Explore from {' + '.join(res.origins)}[/bold]  "
                  f"[dim]{res.dates}, {cabin}, per person[/dim]  [dim]({spent})[/dim]")
    for n in res.notes:
        console.print(f"[yellow]{n}[/yellow]")
    if not res.destinations:
        console.print("[yellow]No priced destinations (widen --month / --max-price / --stops).[/yellow]")
        return
    table = Table()
    for col in ("Destination", "Fare", *(f"{o}" for o in res.origins), "Dates", "Stops", "Flight",
                "Airline", "Hotel/n", "Corpus"):
        table.add_column(col)
    for d in res.destinations:
        best = d.best
        by_origin = {f.origin: f for f in d.fares}
        per_origin = []
        for o in res.origins:
            f = by_origin.get(o)
            per_origin.append("—" if f is None or f.price_usd is None else f"${f.price_usd:,.0f}")
        mins = best.flight_minutes if best else None
        when = best.start_date if best else ""
        if best and best.end_date:
            when = f"{best.start_date[5:]} → {best.end_date[5:]}"
        table.add_row(
            f"{d.name}, {d.country}" if d.country and d.country != "United States" else d.name,
            f"${best.price_usd:,.0f}" if best and best.price_usd is not None else "—",
            *per_origin,
            when,
            "—" if not best or best.stops is None else ("nonstop" if best.stops == 0 else str(best.stops)),
            f"{mins // 60}h{mins % 60:02d}" if mins else "—",
            best.airline if best else "",
            f"${best.hotel_price_usd:,.0f}" if best and best.hotel_price_usd else "—",
            ", ".join(d.corpus),
        )
    console.print(table)


def quota(as_json: bool = typer.Option(False, "--json")) -> None:
    """The SerpAPI budget: searches left this month, usage, renewal date. Free: the Account API
    does not count against the quota."""
    from harness.travel.providers.serpapi_explore_provider import account_quota

    q = account_quota(get_settings().serpapi_key)
    if as_json:
        console.print_json(json.dumps(q))
        return
    console.print(
        f"SerpAPI {q.get('plan_name') or ''}: [bold]{q.get('total_searches_left')}[/bold] searches left of "
        f"{q.get('searches_per_month')} · used {q.get('this_month_usage')} this month · "
        f"{q.get('this_hour_searches')} this hour · renews {q.get('plan_renewal_date') or '—'}"
    )


def hotels(
    location: str = typer.Option(..., "--location", "--where", help="Where, e.g. 'La Jolla'"),
    check_in: str = typer.Option(..., "--check-in", help="Check-in YYYY-MM-DD"),
    check_out: str = typer.Option(..., "--check-out", help="Check-out YYYY-MM-DD"),
    adults: int = typer.Option(2, "--adults"),
    min_class: int = typer.Option(4, "--min-class", help="Min hotel stars (4-5 = the luxe lens; 0 = any)"),
    max_price: int = typer.Option(0, "--max-price", help="Per-night USD ceiling (0 = none)"),
    limit: int = typer.Option(5, "--limit", help="How many properties to surface"),
    vacation_rentals: bool = typer.Option(False, "--vacation-rentals", help="Homes/cabins, not hotels"),
    report: str = typer.Option("", "--report", help="Write a {dest}/lodging/ report (slug or vault path)"),
    photos: int = typer.Option(5, "--photos", help="Photos per property in the report (API returns ~9+)"),
    pick: str = typer.Option(
        "", "--pick", help="Comma-separated name fragments: keep exactly these properties (in this "
        "order) from the FULL result list, not the first --limit (e.g. 'Parker,Sparrows')",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing lodging report"),
    refresh: bool = typer.Option(False, "--refresh", help="Force a fresh search (skip the cache)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Tangible bookable properties for a destination+dates — the lodging-research layer (Google
    Hotels via SerpAPI). ONE search returns the whole ranked list (not one per hotel); a date-keyed
    cache makes re-views cost ZERO quota. **Opt-in / quota-spending** — confirm before running. The
    luxe-stay texture: named, priced, photographed, next-step-is-booking."""
    svc = TravelService()
    res = svc.search_hotels(
        location,
        date.fromisoformat(check_in),
        date.fromisoformat(check_out),
        adults=adults,
        min_hotel_class=min_class,
        max_price=max_price or None,
        limit=100 if pick else limit,  # --pick chooses from the whole list (same search, 0 extra quota)
        vacation_rentals=vacation_rentals,
        refresh=refresh,
    )
    if pick:
        from harness.travel.lodging import pick_offers

        try:
            res.offers = pick_offers(res.offers, [t for t in pick.split(",") if t.strip()])
        except ValueError as e:
            raise HarnessError(str(e)) from e

    if as_json:
        console.print_json(json.dumps(res.model_dump(mode="json")))
        return

    src = "[dim](cached — 0 quota)[/dim]" if res.from_cache else "[dim](live search — 1 quota)[/dim]"
    console.print(
        f"\n[bold]{res.location}[/bold]  "
        f"[dim]{check_in} → {check_out}, {res.nights}n, {adults} adults[/dim]  {src}"
    )
    if not res.offers:
        console.print("[yellow]No properties returned (try --min-class 0 or widen dates).[/yellow]")
        return
    table = Table()
    for col in ("Property", "★", "Rating", "$/night", "Total", "Amenities"):
        table.add_column(col)
    for o in res.offers:
        table.add_row(
            o.name,
            "—" if o.hotel_class is None else str(o.hotel_class),
            "—" if o.overall_rating is None else f"{o.overall_rating}",
            "—" if o.price_per_night_usd is None else f"${o.price_per_night_usd:,.0f}",
            "—" if o.total_usd is None else f"${o.total_usd:,.0f}",
            ", ".join(o.amenities[:3]),
        )
    console.print(table)
    console.print("\n[dim]Details:[/dim]")
    for o in res.offers:
        deal = f"  [green]⭐ {o.deal}[/green]" if o.deal else ""
        console.print(f"\n[bold]{o.name}[/bold]{deal}")
        if o.description:
            console.print(f"  {o.description}")
        if o.nearby_places:
            near = " · ".join(
                f"{np.name} ({np.duration} {np.transport})".strip().replace("( )", "")
                for np in o.nearby_places[:4]
            )
            console.print(f"  [dim]Nearby:[/dim] {near}")
        if o.excluded_amenities:
            console.print(f"  [yellow]Lacks:[/yellow] {', '.join(o.excluded_amenities)}")
        if o.booking_link:
            console.print(f"  [dim]Book:[/dim] {o.booking_link}")

    if report:
        try:
            path = svc.write_lodging_report(res, report, photos_per=photos, force=force)
            console.print(f"\n[bold green]Lodging report written:[/bold green] {path}")
        except FileExistsError as e:
            raise HarnessError(f"{e} (use --force to overwrite)") from e


def _render_shortlist(s: Shortlist) -> None:
    console.print(f"[bold]Shortlist — {s.window}[/bold]  (ranked; not a single pick)")
    table = Table()
    for col in ("#", "Candidate", "Origin", "Carrier", "Stops", "Duration", "Price", "Score"):
        table.add_column(col)
    for i, sc in enumerate(s.candidates, 1):
        f = sc.best_flight
        table.add_row(
            str(i), sc.candidate.display_name,
            f.origin_iata if f else "—",
            f.carrier if f else "—",
            str(f.stops) if f else "—",
            f"{f.duration_hours:.1f}h" if f else "—",
            f"${f.price_usd:.0f}" if f else "—",
            f"{sc.score.total:.0f}",
        )
    console.print(table)
    for sc in s.candidates:
        console.print(f"\n[bold]{sc.candidate.display_name}[/bold] — {sc.score.total:.0f}")
        for r in sc.score.rationale:
            console.print(f"   • {r}")
    if s.notes:
        console.print("\n[bold]Notes[/bold]")
        for n in s.notes:
            console.print(f"   • {n}")
