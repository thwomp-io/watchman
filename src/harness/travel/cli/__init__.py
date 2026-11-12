"""`hn travel`: the travel lane's CLI. Each module holds one noun's verbs; this file builds the group
and fixes the order `hn travel --help` lists them in. Thin over `TravelService`."""

from __future__ import annotations

import typer

from harness.packs import PackGroup
from harness.travel.cli import booking, conditions, corpus, events, facts, food, horizon, images, visits

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn travel plan --pack …)
    add_completion=False,
    help="Travel lane — flight and hotel search ranked against your preferences, trip and visit "
    "planning, events, and live conditions (weather, air, traffic, ferries).",
)


# Registration order is the order `--help` lists the verbs in.
app.command()(corpus.prefs)
app.command()(booking.search)
app.command()(booking.flights)
app.command()(booking.rank)
app.command()(images.images)
app.command("contact-sheet")(images.contact_sheet)
app.command()(visits.decision)
app.command()(visits.bank)
app.command()(visits.banks)
app.command()(visits.schedule)
app.command(name="bank-enrich")(visits.bank_enrich)
app.command(name="bank-photos")(visits.bank_photos)
app.command()(horizon.nudge)
app.command()(events.events)
app.command()(events.calendar)
app.command()(events.reference)
app.command()(horizon.trips)
app.command()(horizon.upcoming)
app.command()(booking.explore)
app.command()(booking.quota)
app.command()(booking.hotels)
app.command()(conditions.weather)
app.command()(conditions.air)
app.command()(conditions.pulse)
app.command()(food.food)
app.command(name="food-photos")(food.food_photos)
app.command(name="shrink-images")(images.shrink_images)
app.command()(conditions.quakes)
app.command()(facts.fx)
app.command()(facts.holidays)
app.command()(conditions.sun)
app.command()(facts.country)
app.command()(conditions.traffic)
app.command()(conditions.ferry)
app.command()(corpus.viz)
app.command()(corpus.mcp)
