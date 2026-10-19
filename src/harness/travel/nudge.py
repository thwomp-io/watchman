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

THE HORIZON AXIS. The weekend engine alone over-indexes near-term: a 2-3-night
window can never be won by a 10-hour flight, so the far destinations stayed structurally
invisible, and a family holiday could get nudged as a getaway. Four lanes now:
  weekend   — the engine above (0-26 weeks of weekends);
  long-lead — 5-9-night BLOCKS 3-12 months out (holiday-anchored weeks + open weeks), where the
              reach bonus makes the lane FOR the destinations that need a week;
  horizon   — worth-a-look ideas: season-shaped (not date-shaped) cards for never-tripped +
              screen-calibrated destinations, surfaced WITH the calibration line (the
              tactile-exposure posture — an in-screen destination scores UP here, not down);
  family    — holidays the user names as family time are never escape-nudged; at most one
              practical card (an authored logistics line) surfaces per family window.
Each lane is a WEIGHT-shaped filter of the same registry — nothing is ever hard-excluded from
the corpus, only from the lane where it would be noise.
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
    kind: str = "weekend"  # "weekend" | "long-weekend" | "week" (long-lead block) | "season" | "family"
    anchor: str = ""  # what extended/flavors it (holiday name, personal date)
    anchor_date: date | None = None  # the anchoring holiday's own date — the identity a weekend
    #                                  and a week block share when the same holiday shapes both
    personal: str = ""  # overlapping personal-date name, if any
    pto_days: int = 0  # weekdays inside the window that aren't holidays (blocks; 0 for weekends)
    title: str = ""  # a custom label ("best Oct–Nov") — season windows aren't date-shaped

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    @property
    def label(self) -> str:
        """Human window label: 'Fri Sep 4 → Mon Sep 7' (or the custom title for season windows)."""
        if self.title:
            return self.title

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
    lane: str = "weekend"  # "weekend" | "long-lead" | "horizon" | "family"
    components: list[NudgeComponent] = Field(default_factory=list)
    reason: str = ""  # the assembled "because Z" (dominant components, templated + live-filled)
    hook: str = ""  # authored nudge_hooks flavor whose trigger fired, verbatim
    events: list[EventResult] = Field(default_factory=list)  # window events at the destination
    weather_note: str = ""  # in-horizon forecast summary ("mostly dry, highs ~74°F")
    doc: str = ""  # vault-relative destination folder-note path ("" when no doc exists) — the
    #               console's deep-link target
    image: str = ""  # the destination note's first embedded image (vault-relative) — the card thumb
    in_decision: bool = False  # also a candidate on the live trip decision's radar

    @property
    def line(self) -> str:
        """The nudge, one line — shaped per lane:
        weekend / long-lead: 'You could be in {city} Fri Sep 4 → Mon Sep 7 — …'
        horizon:             'Worth a look: {city} (best Oct–Nov) — …'
        family:              '{anchor} Thu Nov 26 → Sun Nov 29 is a family window — {note}'"""
        if self.lane == "horizon":
            text = f"Worth a look: {self.display} ({self.window.label}) — {self.reason}"
        elif self.lane == "family":
            text = f"{self.display} {self.window.label} is a family window — {self.reason}"
        else:
            text = f"You could be in {self.display} {self.window.label} — {self.reason}"
        return f"{text} ({self.hook})" if self.hook else text


def _display(slug: str) -> str:
    """Human label from a slug — utilitarian title-case ('new-york-city' → 'New York City')."""
    small = {"bc", "la"}
    return " ".join(w.upper() if w in small else w.capitalize() for w in slug.split("-"))


def _almanac(reference: list[EventResult]) -> tuple[dict[date, str], dict[date, str]]:
    """(holidays, personals) by date from the almanac rows; holiday names trimmed of their notes."""
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
    return holidays, personals


def is_family_window(anchor: str, family_windows: list[str]) -> bool:
    """A window anchored on a holiday the user names as family time (substring, case-insensitive)."""
    a = anchor.lower()
    return bool(a) and any(f.lower() in a for f in family_windows if f)


def enumerate_windows(
    reference: list[EventResult],
    today: date,
    *,
    horizon_days: int = 182,
    lead_min_days: int = 4,
    family_windows: list[str] | None = None,
) -> list[NudgeWindow]:
    """Enumerate the GOOD upcoming windows — deterministic, calendar-driven.

    Every Fri→Sun weekend from `today + lead_min_days` to `today + horizon_days`, extended to a
    3-night shape when the almanac says the adjacent Monday (Fri→Mon), Friday (Thu→Sun), or
    Thursday (Thu→Sun — the Thanksgiving shape) is a holiday. Personal dates annotate any window
    they overlap. `lead_min_days` keeps the nudge bookable (no nudging tomorrow morning); the
    default ~6-month horizon reaches across seasons (an August scan surfaces the deep-winter
    warm-beach windows), the long-lead end being exactly where the far options earn their shot.
    A holiday-extended window whose anchor is in `family_windows` comes back `kind="family"` —
    enumerated (so the caller can surface the practical card) but never scored as an escape.
    """
    holidays, personals = _almanac(reference)
    fam = family_windows or []

    out: list[NudgeWindow] = []
    first = today + timedelta(days=lead_min_days)
    # walk to the first Friday at/after `first`
    fri = first + timedelta(days=(4 - first.weekday()) % 7)
    while fri <= today + timedelta(days=horizon_days):
        start, end, kind, anchor = fri, fri + timedelta(days=2), "weekend", ""
        anchor_date: date | None = None
        if (mon := fri + timedelta(days=3)) in holidays:  # Mon off → Fri→Mon, 3 nights
            end, kind, anchor, anchor_date = mon, "long-weekend", holidays[mon], mon
        elif fri in holidays:  # Fri off → Thu→Sun, 3 nights
            start, kind, anchor, anchor_date = fri - timedelta(days=1), "long-weekend", holidays[fri], fri
        elif (thu := fri - timedelta(days=1)) in holidays:  # Thu off (Thanksgiving) → Thu→Sun
            start, kind, anchor, anchor_date = thu, "long-weekend", holidays[thu], thu
        if is_family_window(anchor, fam):
            kind = "family"
        personal = next(
            (name for d, name in personals.items() if start <= d <= end),
            "",
        )
        out.append(NudgeWindow(
            start=start, end=end, kind=kind, anchor=anchor, anchor_date=anchor_date, personal=personal
        ))
        fri += timedelta(days=7)
    return out


def _pto_days(start: date, end: date, holidays: dict[date, str]) -> int:
    """Weekdays in [start, end] that aren't holidays — the PTO a block costs."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            n += 1
        d += timedelta(days=1)
    return n


def enumerate_blocks(
    reference: list[EventResult],
    today: date,
    *,
    min_days: int = 90,
    max_days: int = 365,
    family_windows: list[str] | None = None,
) -> list[NudgeWindow]:
    """The LONG-LEAD windows: 7-8-night BLOCKS 3-12 months out.

    Two shapes, deterministic from the almanac: (a) a HOLIDAY-ANCHORED week — the Saturday
    before the holiday through the Sunday after the following weekend (8 nights; the holiday
    itself is a free day, so a Monday holiday costs 4 PTO days for 8 nights, Thanksgiving 3);
    (b) an OPEN week per month with no holiday block — the month's first Saturday → the next
    Saturday (7 nights, 5 PTO). Family-anchored blocks come back `kind="family"`; personal
    dates annotate. Weeks are the shape a 6-13h flight actually needs — this is the lane where
    the far destinations get a window they can win."""
    holidays, personals = _almanac(reference)
    fam = family_windows or []
    lo, hi = today + timedelta(days=min_days), today + timedelta(days=max_days)
    out: list[NudgeWindow] = []
    taken_months: set[tuple[int, int]] = set()

    for h, name in sorted(holidays.items()):
        if not (lo <= h <= hi):
            continue
        start = h - timedelta(days=(h.weekday() + 2) % 7)  # the Saturday on/before the holiday
        end = start + timedelta(days=8)  # → the Sunday after the following weekend
        kind = "family" if is_family_window(name, fam) else "week"
        out.append(NudgeWindow(
            start=start, end=end, kind=kind, anchor=name, anchor_date=h,
            personal=next((n for d, n in personals.items() if start <= d <= end), ""),
            pto_days=_pto_days(start, end, holidays),
        ))
        taken_months.add((start.year, start.month))

    # open weeks: one per month without a holiday block, first Saturday → next Saturday
    cursor = lo.replace(day=1)
    while cursor <= hi:
        if (cursor.year, cursor.month) not in taken_months:
            sat = cursor + timedelta(days=(5 - cursor.weekday()) % 7)
            end = sat + timedelta(days=7)
            if lo <= sat and end <= hi:
                out.append(NudgeWindow(
                    start=sat, end=end, kind="week", anchor="",
                    personal=next((n for d, n in personals.items() if sat <= d <= end), ""),
                    pto_days=_pto_days(sat, end, holidays),
                ))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    out.sort(key=lambda w: w.start)
    return out


def season_window(best_windows: list[int], today: date) -> NudgeWindow | None:
    """The HORIZON lane's window: the next contiguous run of `best_windows` months from today,
    date-spanned (first day of the run → last day) and titled 'best Oct–Nov'. Season-shaped by
    design — a horizon card names a season, not a booking date."""
    if not best_windows:
        return None
    months = sorted(set(m for m in best_windows if 1 <= m <= 12))
    # rotate to the first best month at/after this month (a run can wrap the year)
    order = [((today.month - 1 + i) % 12) + 1 for i in range(12)]
    start_m = next((m for m in order if m in months), None)
    if start_m is None:
        return None
    run = [start_m]
    nxt = start_m
    while True:
        nxt = (nxt % 12) + 1
        if nxt in months and nxt not in run and len(run) < 12:
            run.append(nxt)
        else:
            break
    y = today.year if start_m >= today.month else today.year + 1
    start = date(y, start_m, 1)
    end_m = run[-1]
    end_y = y + (1 if end_m < start_m else 0)
    end = date(end_y, end_m, _cal.monthrange(end_y, end_m)[1])
    title = (
        f"best {_cal.month_abbr[start_m]}"
        if len(run) == 1
        else f"best {_cal.month_abbr[start_m]}–{_cal.month_abbr[end_m]}"
    )
    return NudgeWindow(start=start, end=end, kind="season", title=title)


def score_static(
    slug: str,
    window: NudgeWindow,
    weights: WeightConfig,
    reference_events: list[EventResult],
    *,
    lane: str = "weekend",
) -> NudgeCandidate:
    """Phase-1 static score for one (destination, window) pair — corpus + almanac only, no I/O.

    Components (all WEIGHTS, never filters — friction lands as score, not absence):
    base_fit (authored) · season (month ∈ best_windows) · calendar (window kind) · feasibility
    (home-airport direct / short drive / short flight, with long-haul friction per-night-forgiven)
    · screen (in-screen axes, mild calibrated penalty) · almanac events in-window at the city.
    `lane="long-lead"` (a week block) adds the REACH bonus — flights beyond the short-hop bar
    score UP, because a week is the window those destinations need — and phrases the calendar
    component as the PTO math ("8 nights for 4 PTO days").
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
    elif window.kind == "week" and window.anchor:
        # a holiday-anchored block: the PTO arithmetic IS the fact
        comps.append(
            NudgeComponent(
                name="calendar",
                points=nw.long_weekend,
                fact=f"{window.nights} nights for {window.pto_days} PTO days ({window.anchor})",
            )
        )
    if window.personal:
        comps.append(
            NudgeComponent(
                name="calendar", points=nw.personal_date, fact=f"{window.personal} falls that window"
            )
        )

    # Feasibility — home-airport-direct is DERIVED (gateway ∈ served set), never authored twice.
    # On a long-lead BLOCK the short-hop bonuses are zero: a week doesn't need low friction, and
    # paying for it lets a 3h drive out-score a 6h flight on the one window shape the flight
    # needs (the lane's reason to exist). The friction PENALTY on far flights stays (per-night
    # forgiven) — it just competes with the reach bonus below instead of the home-airport bonus.
    gateway = weights.destination_airports.get(slug, "")
    home = weights.flight.home_airport
    home_direct = bool(home) and gateway in weights.flight.home_airport_served_iata
    drivable = cfg.drive_hours is not None and cfg.drive_hours <= nw.short_drive_hours
    hop = 0.0 if lane == "long-lead" else 1.0
    if home_direct:
        comps.append(
            NudgeComponent(
                name="feasibility", points=nw.home_airport_direct * hop, fact=f"nonstop from {home}"
            )
        )
    elif drivable:
        comps.append(
            NudgeComponent(
                name="feasibility", points=nw.short_drive * hop, fact=f"~{cfg.drive_hours:g}h drive"
            )
        )
    elif cfg.flight_hours is not None and cfg.flight_hours <= nw.short_flight_hours:
        comps.append(
            NudgeComponent(
                name="feasibility", points=nw.short_flight * hop,
                fact=f"a short ~{cfg.flight_hours:g}h flight",
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
        if lane == "long-lead":
            # the lane's purpose: a week is the window a far destination NEEDS — reward reach
            far = cfg.flight_hours >= nw.far_reach_hours
            pts = nw.long_lead_reach + (nw.long_lead_far_reach if far else 0.0)
            comps.append(
                NudgeComponent(
                    name="reach", points=pts,
                    fact=f"far enough to need a week (~{cfg.flight_hours:g}h flight)",
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
        lane=lane,
        components=comps,
        events=ref_here,
    )
    _apply_event_components(cand, weights)
    cand.score = round(sum(c.points for c in cand.components), 1)
    return cand


def horizon_candidate(
    slug: str, weights: WeightConfig, today: date, *, tripped: bool
) -> NudgeCandidate | None:
    """The HORIZON lane's card for one destination — season-shaped, corpus-only, no I/O.

    The horizon-lane scorer: base_fit (authored) · novelty (never on the trip calendar) · reach
    (a ≥far_reach_hours flight = a build-toward that no weekend lane will ever surface) · screen
    CALIBRATION — an in-screen axis scores UP here, and the fact carries the axis, so the card
    says "geological — calibrates clean" rather than hiding the destination. That sign flip is the
    whole point of the lane (surface it with the data rather than pre-screening it). Returns None
    for a destination with no authored best_windows (no season to name)."""
    nw = weights.nudge
    cfg: DestinationNudge = weights.destination_nudge.get(slug, DestinationNudge())
    window = season_window(cfg.best_windows, today)
    if window is None:
        return None
    comps: list[NudgeComponent] = []
    if cfg.base_fit != 5.0:
        comps.append(
            NudgeComponent(
                name="base_fit",
                points=(cfg.base_fit - 5.0) * nw.base_fit_factor,
                fact="a standing favorite" if cfg.base_fit > 5 else "a weaker standing fit",
            )
        )
    if not tripped:
        comps.append(NudgeComponent(name="novelty", points=nw.horizon_novelty, fact="never on your calendar"))
    if cfg.flight_hours is not None and cfg.flight_hours >= nw.far_reach_hours:
        comps.append(
            NudgeComponent(
                name="reach", points=nw.horizon_reach,
                fact=f"a build-toward (~{cfg.flight_hours:g}h flight — never a weekend)",
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
                    name="calibration",
                    points=nw.horizon_calibration * len(flagged),
                    fact=f"screen-calibrated ({', '.join(flagged)}) — the exposure-building pick",
                )
            )
    cand = NudgeCandidate(
        slug=slug, display=_display(slug), city=weights.destination_cities.get(slug, ""),
        window=window, score=round(sum(c.points for c in comps), 1), lane="horizon", components=comps,
    )
    return cand


def family_card(window: NudgeWindow, weights: WeightConfig) -> NudgeCandidate | None:
    """The FAMILY lane's card for one family window — the authored practical line, nothing else.
    None when no `family_note` is configured (the window is then simply skipped, with a reason)."""
    note = weights.nudge.family_note.strip()
    if not note:
        return None
    return NudgeCandidate(
        slug=f"family-{window.start.isoformat()}", display=window.anchor or "Family window",
        window=window, score=0.0, lane="family",
        components=[NudgeComponent(name="calendar", points=0.0, fact=f"{window.anchor} — a family window")],
        reason=note,
    )


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
    if cand.lane == "horizon":
        # the horizon card ALWAYS carries its calibration line when one exists — surfacing
        # an in-screen destination without the data is the enabling failure mode, not a nudge
        cal = next((c.fact for c in cand.components if c.name == "calibration"), "")
        if cal and cal not in facts:
            facts = facts[:1] + [cal]
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
