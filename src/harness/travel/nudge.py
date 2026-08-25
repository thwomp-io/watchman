"""The proactive-nudge deterministic core — windows × destinations scoring.

The nudge shape: "You could be in {destination} {window} — {because Z}".
The model is NEVER in this loop — intelligence is precomputed into the corpus (the `destination_nudge`
registry: authored `best_windows` / feasibility / `base_fit` / `nudge_hooks`), and this engine only
enumerates, scores, and assembles. "Because Z" decomposes into computable components (calendar /
season / event / weather / feasibility / base-fit); the surfaced reason is the dominant components'
templated strings filled with the facts the scorer already holds.

Two-phase by design (quota-shaped): the STATIC phase scores every window × destination from corpus +
the free reference almanac; only the top pairs earn the live ENRICH phase (Ticketmaster events —
free-tier keyed; Open-Meteo weather when the window sits inside the ~16-day forecast horizon).
No paid quota is ever spent here.

Windows are enumerated, not picked: every upcoming weekend in the horizon, extended when a holiday
lands adjacent (Fri or Mon off → a 3-night shape for ≤1 PTO day). Long-haul friction scales DOWN as
windows lengthen — the mechanism that lets far options surface on long-lead holiday windows instead
of being reflexively clamped to the short-haul set.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from pydantic import BaseModel, Field

from harness.travel.models import DailyWeather, EventResult
from harness.travel.ranking.weights import DestinationNudge, WeightConfig

# Open-Meteo's practical daily-forecast horizon is ~16 days; windows
# starting beyond it simply skip the weather component (season carries the climate judgment there).
WEATHER_HORIZON_DAYS = 16


class NudgeWindow(BaseModel):
    """One enumerated candidate window — a (start, end) span with its calendar character."""

    start: date
    end: date
    kind: str = "weekend"  # "weekend" | "long-weekend"
    anchor: str = ""  # what extended/flavors it (holiday name, personal date)
    personal: str = ""  # overlapping personal-date name, if any

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    @property
    def label(self) -> str:
        """Human window label: 'Fri Sep 4 → Mon Sep 7'."""

        def _d(d: date) -> str:
            return f"{_cal.day_abbr[d.weekday()]} {_cal.month_abbr[d.month]} {d.day}"

        return f"{_d(self.start)} → {_d(self.end)}"


class NudgeComponent(BaseModel):
    """One scored component of Z — kept so every nudge is explainable (`--json` carries them all)."""

    name: str  # base_fit | season | calendar | feasibility | screen | event | weather
    points: float
    fact: str  # the human fragment this component contributes ("nonstop from the home airport")


class NudgeCandidate(BaseModel):
    """A scored (destination, window) pair — the engine's output unit."""

    slug: str
    display: str
    city: str = ""
    window: NudgeWindow
    score: float
    components: list[NudgeComponent] = Field(default_factory=list)
    reason: str = ""  # the assembled "because Z" (dominant components, templated + live-filled)
    hook: str = ""  # authored nudge_hooks flavor whose trigger fired, verbatim
    events: list[EventResult] = Field(default_factory=list)  # window events at the destination
    weather_note: str = ""  # in-horizon forecast summary ("mostly dry, highs ~74°F")
    doc: str = ""  # vault-relative destination folder-note path ("" when no doc exists) — the
    #               console's deep-link target

    @property
    def line(self) -> str:
        """The nudge, one line: 'You could be in {city} Fri Sep 4 → Mon Sep 7 — …'."""
        text = f"You could be in {self.display} {self.window.label} — {self.reason}"
        return f"{text} ({self.hook})" if self.hook else text


def _display(slug: str) -> str:
    """Human label from a slug — utilitarian title-case ('new-york-city' → 'New York City')."""
    small = {"bc", "la"}
    return " ".join(w.upper() if w in small else w.capitalize() for w in slug.split("-"))


def enumerate_windows(
    reference: list[EventResult],
    today: date,
    *,
    horizon_days: int = 182,
    lead_min_days: int = 4,
) -> list[NudgeWindow]:
    """Enumerate the GOOD upcoming windows — deterministic, calendar-driven.

    Every Fri→Sun weekend from `today + lead_min_days` to `today + horizon_days`, extended to a
    3-night shape when the almanac says the adjacent Monday (Fri→Mon), Friday (Thu→Sun), or
    Thursday (Thu→Sun — the Thanksgiving shape) is a holiday. Personal dates annotate any window
    they overlap. `lead_min_days` keeps the nudge bookable (no nudging tomorrow morning); the
    default ~6-month horizon reaches across seasons (an August scan surfaces the deep-winter
    warm-beach windows), the long-lead end being exactly where the far options earn their shot.
    """
    holidays: dict[date, str] = {}
    personals: dict[date, str] = {}
    for e in reference:
        try:
            d = date.fromisoformat(e.local_date)
        except ValueError:
            continue
        seg = e.segment.lower()
        if seg == "holiday":
            holidays[d] = e.name.split("—")[0].strip()
        elif seg == "personal":
            personals[d] = e.name

    out: list[NudgeWindow] = []
    first = today + timedelta(days=lead_min_days)
    # walk to the first Friday at/after `first`
    fri = first + timedelta(days=(4 - first.weekday()) % 7)
    while fri <= today + timedelta(days=horizon_days):
        start, end, kind, anchor = fri, fri + timedelta(days=2), "weekend", ""
        if (mon := fri + timedelta(days=3)) in holidays:  # Mon off → Fri→Mon, 3 nights
            end, kind, anchor = mon, "long-weekend", holidays[mon]
        elif fri in holidays:  # Fri off → Thu→Sun, 3 nights
            start, kind, anchor = fri - timedelta(days=1), "long-weekend", holidays[fri]
        elif (thu := fri - timedelta(days=1)) in holidays:  # Thu off (Thanksgiving) → Thu→Sun
            start, kind, anchor = thu, "long-weekend", holidays[thu]
        personal = next(
            (name for d, name in personals.items() if start <= d <= end),
            "",
        )
        out.append(NudgeWindow(start=start, end=end, kind=kind, anchor=anchor, personal=personal))
        fri += timedelta(days=7)
    return out


def score_static(
    slug: str,
    window: NudgeWindow,
    weights: WeightConfig,
    reference_events: list[EventResult],
) -> NudgeCandidate:
    """Phase-1 static score for one (destination, window) pair — corpus + almanac only, no I/O.

    Components (all WEIGHTS, never filters — friction lands as score, not absence):
    base_fit (authored) · season (month ∈ best_windows) · calendar (window kind) · feasibility
    (home-airport direct / short drive / short flight, with long-haul friction per-night-forgiven)
    · screen (in-screen axes, mild calibrated penalty) · almanac events in-window at the city.
    """
    nw = weights.nudge
    cfg: DestinationNudge = weights.destination_nudge.get(slug, DestinationNudge())
    city = weights.destination_cities.get(slug, "")
    comps: list[NudgeComponent] = []

    if cfg.base_fit != 5.0:
        comps.append(
            NudgeComponent(
                name="base_fit",
                points=(cfg.base_fit - 5.0) * nw.base_fit_factor,
                fact="a standing favorite" if cfg.base_fit > 5 else "a weaker standing fit",
            )
        )

    if window.start.month in cfg.best_windows or window.end.month in cfg.best_windows:
        comps.append(
            NudgeComponent(
                name="season",
                points=nw.season_match,
                fact=f"peak {_cal.month_name[window.start.month]} season there",
            )
        )

    if window.kind == "long-weekend":
        comps.append(
            NudgeComponent(
                name="calendar",
                points=nw.long_weekend,
                fact=f"it's a 3-day weekend ({window.anchor})" if window.anchor else "it's a 3-day weekend",
            )
        )
    if window.personal:
        comps.append(
            NudgeComponent(
                name="calendar", points=nw.personal_date, fact=f"{window.personal} falls that window"
            )
        )

    # Feasibility — home-airport-direct is DERIVED (gateway ∈ served set), never authored twice.
    gateway = weights.destination_airports.get(slug, "")
    home = weights.flight.home_airport
    home_direct = bool(home) and gateway in weights.flight.home_airport_served_iata
    drivable = cfg.drive_hours is not None and cfg.drive_hours <= nw.short_drive_hours
    if home_direct:
        comps.append(
            NudgeComponent(name="feasibility", points=nw.home_airport_direct, fact=f"nonstop from {home}")
        )
    elif drivable:
        comps.append(
            NudgeComponent(name="feasibility", points=nw.short_drive, fact=f"~{cfg.drive_hours:g}h drive")
        )
    elif cfg.flight_hours is not None and cfg.flight_hours <= nw.short_flight_hours:
        comps.append(
            NudgeComponent(
                name="feasibility", points=nw.short_flight, fact=f"a short ~{cfg.flight_hours:g}h flight"
            )
        )
    elif cfg.flight_hours is not None:
        over = cfg.flight_hours - nw.short_flight_hours
        penalty = -over * nw.flight_penalty_per_hour(window.nights)
        comps.append(
            NudgeComponent(
                name="feasibility",
                points=penalty,
                fact=f"~{cfg.flight_hours:g}h flight — fits a {window.nights}-night window"
                if window.nights >= 3
                else f"~{cfg.flight_hours:g}h flight (long for a plain weekend)",
            )
        )

    screen = weights.destination_screens.get(slug)
    if screen is not None:
        flagged = [
            ax for ax, v in (("geological", screen.geological), ("social/crime", screen.social_crime))
            if v == "in_screen"
        ]
        if flagged:
            comps.append(
                NudgeComponent(
                    name="screen",
                    points=-nw.screen_in_screen_penalty * len(flagged),
                    fact=f"screen-calibrated ({', '.join(flagged)})",
                )
            )

    # Free almanac events at this destination during the window (live TM rides the enrich phase).
    ref_here = [
        e
        for e in reference_events
        if e.city
        and city
        and (city.lower() in e.city.lower() or e.city.lower() in city.lower())
        and _in_window(e, window)
    ]
    cand = NudgeCandidate(
        slug=slug,
        display=_display(slug),
        city=city,
        window=window,
        score=0.0,
        components=comps,
        events=ref_here,
    )
    _apply_event_components(cand, weights)
    cand.score = round(sum(c.points for c in cand.components), 1)
    return cand


def _in_window(e: EventResult, window: NudgeWindow) -> bool:
    try:
        d = date.fromisoformat(e.local_date)
    except ValueError:
        return False
    return window.start <= d <= window.end


def _short_name(name: str) -> str:
    """Trim ticketing-cruft suffixes from an event name for the surfaced reason ("Home vs.
    Away | Official Hotel Packages" → "Home vs. Away"). The events list keeps the raw name.
    TM pads the pipe with a NON-BREAKING space (\\xa0) on some listings — normalize first."""
    return name.replace("\xa0", " ").split(" | ")[0].strip()


def _apply_event_components(cand: NudgeCandidate, weights: WeightConfig) -> None:
    """(Re)compute the event component from `cand.events` — called at static time (almanac) and
    again after the enrich phase merges live results. Centerpiece anchors; perks cap. Also orders
    the candidate's event list for display: centerpiece first, then by date."""
    nw = weights.nudge
    cand.components = [c for c in cand.components if c.name != "event"]
    if not cand.events:
        return
    center = [e for e in cand.events if weights.events.tier_for(e.subgenre) == "centerpiece"]
    mega = [e for e in cand.events if e.segment.lower() == "mega-event"]
    perks = [e for e in cand.events if e not in center and e not in mega]
    cand.events = (
        sorted(center, key=lambda e: e.local_date)
        + sorted(mega, key=lambda e: e.local_date)
        + sorted(perks, key=lambda e: (e.local_date, e.local_time or ""))
    )
    if center:
        cand.components.append(
            NudgeComponent(
                name="event", points=nw.event_centerpiece, fact=f"{_short_name(center[0].name)} is on"
            )
        )
    elif mega:
        cand.components.append(
            NudgeComponent(
                name="event", points=nw.event_centerpiece * 0.8, fact=f"{_short_name(mega[0].name)} is on"
            )
        )
    if perks:
        pts = min(nw.event_perk * len(perks), nw.event_perk_cap)
        # perks uplift; they are never the anchor (the tiering doctrine) — fact stays secondary.
        first = _short_name(perks[0].name)
        fact = first if len(perks) == 1 else f"{first} (+{len(perks) - 1} more on)"
        cand.components.append(NudgeComponent(name="event", points=pts, fact=fact))


def apply_weather(cand: NudgeCandidate, days: list[DailyWeather], weights: WeightConfig) -> None:
    """Enrich-phase weather component for an in-horizon window — real forecast over climate prior."""
    nw = weights.nudge

    def _day(d: DailyWeather) -> date | None:
        try:
            return date.fromisoformat(d.date)
        except ValueError:
            return None

    in_win = [d for d in days if (dd := _day(d)) is not None and cand.window.start <= dd <= cand.window.end]
    if not in_win:
        return
    wet = [d for d in in_win if (d.precip_prob or 0) >= 50]
    highs = [d.temp_max for d in in_win if d.temp_max is not None]
    avg_high = round(sum(highs) / len(highs)) if highs else None
    mostly_wet = len(wet) > len(in_win) / 2
    points = nw.weather_wet if mostly_wet else nw.weather_clear
    desc = "mostly wet" if mostly_wet else "mostly dry"
    fact = f"forecast {desc}" + (f", highs ~{avg_high}°" if avg_high is not None else "")
    cand.components.append(NudgeComponent(name="weather", points=points, fact=fact))
    cand.weather_note = fact
    cand.score = round(sum(c.points for c in cand.components), 1)


def assemble_reason(cand: NudgeCandidate, weights: WeightConfig) -> None:
    """Build the surfaced 'because Z' from the DOMINANT positive components (top two, templated,
    already live-filled) + attach the first authored hook whose trigger fired. Deterministic —
    templates + facts the scorer holds; no generation."""
    ranked = sorted((c for c in cand.components if c.points > 0), key=lambda c: c.points, reverse=True)
    facts = [c.fact for c in ranked[:2] if c.fact]
    cand.reason = " and ".join(facts) if facts else "an open window"

    cfg = weights.destination_nudge.get(cand.slug)
    if cfg is None:
        return
    fired: set[str] = {"weekend"}
    if cand.window.kind == "long-weekend":
        fired.add("long_weekend")
    if any(c.name == "season" for c in ranked):
        fired.add("season")
    for hook in cfg.nudge_hooks:
        when = hook.when.lower()
        if when in fired:
            cand.hook = hook.text
            break
        if when.startswith("event:"):
            needle = when.split(":", 1)[1].strip()
            if any(needle in e.name.lower() for e in cand.events):
                cand.hook = hook.text
                break
