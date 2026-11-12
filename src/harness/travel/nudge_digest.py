"""The nudge digest: windows × destinations, scored and picked into the cards the Planning tab
shows. `harness.travel.nudge` scores one pair; this module enumerates the pairs, runs the live
enrich for the leaders, and picks a varied set per lane.

The lanes, each a differently weighted view of the same destination registry:

  weekend    every enumerated weekend × every destination, scored from the corpus and the free
             almanac; the leaders earn the live enrich (ticketed events, and weather inside the
             forecast horizon).
  games      a followed team's game in a destination city, on the weekend it falls in.
  long-lead  week-long blocks three to twelve months out, scored with the reach bonus so the far
             destinations win the windows they need. Events only: no forecast reaches that far.
  horizon    season-shaped "worth a look" cards for destinations never tripped. Corpus only.
  family     holidays the corpus names as family time. Never an escape nudge: one authored card
             per window, when a note is set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from harness.paths import harness_cache_dir
from harness.travel import games as _g
from harness.travel import nudge as _n
from harness.travel.config.settings import get_settings
from harness.travel.corpus.reader import _resolve_folder_note
from harness.travel.corpus.reference import read_reference
from harness.travel.models import EventResult, Trip, is_live
from harness.travel.planning import hero_image, trip_name
from harness.travel.providers import get_event_provider
from harness.travel.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.travel.service import TravelService

LANES = ("weekend", "games", "long-lead", "horizon", "family")


def nudge(
    svc: TravelService,
    today: date,
    *,
    horizon_days: int = 182,
    top: int = 5,
    live: bool = True,
    include_taken: bool = False,
    lanes: list[str] | None = None,
) -> dict[str, Any]:
    """The digest: {nudges, windows, skipped, note, lanes, decision_trip}.

    `lanes` limits it to some lanes (default all). `live=False` skips every network read and scores
    from the corpus alone. A window a live trip already occupies is skipped unless `include_taken`.
    `top` bounds the weekend lane; the long-lead and horizon counts come from the nudge weights.
    Every nudge carries its lane and its full component breakdown, so each score can be explained.
    """
    weights = svc.weights.nudge
    want = set(lanes) if lanes else set(LANES)
    reference = read_reference(svc.reader._root, svc.weights.events.followed_teams)
    trips = svc.reader.scan_trips()
    windows = _open_windows(svc, reference, trips, today, horizon_days, include_taken)
    slugs = list(svc.weights.destination_nudge) or list(svc.weights.destination_airports)

    # Every followed team's games across the horizon, fetched once: the games lane places them and
    # the family lane mentions a home game that falls inside a family window.
    games: list[_g.MarketGame] = []
    games_note = ""
    if live and svc.weights.events.centerpiece_markets and want & {"games", "family"}:
        games, games_note = _game_prepass(svc, today, horizon_days)

    picker = _Picker(svc)
    cards: list[_n.NudgeCandidate] = []
    notes: list[str] = []
    if "weekend" in want:
        pairs = [_n.score_static(s, w, svc.weights, reference) for w in windows.weekends for s in slugs]
        cards += picker.pick(_ranked(svc, pairs, max(top * 3, 12), today, live, notes), top)
    if "games" in want and games:
        cards += _game_cards(svc, picker, games, windows.weekends, reference, today, shown=cards)
    if "long-lead" in want and weights.long_lead_top > 0:
        pairs = [
            _n.score_static(s, w, svc.weights, reference, lane="long-lead")
            for w in windows.blocks
            for s in slugs
        ]
        leaders = max(weights.long_lead_top * 2, 6)
        cards += picker.pick(_ranked(svc, pairs, leaders, today, live, notes), weights.long_lead_top)
    if "horizon" in want and weights.horizon_cards > 0:
        cards += picker.pick(_horizon_candidates(svc, picker, slugs, trips, today), weights.horizon_cards)
    if "family" in want:
        cards += _family_cards(svc, windows.family, games)

    decision = svc.active_decision()
    _mark_in_decision(cards, decision)
    lane_counts: dict[str, int] = {}
    for card in cards:
        lane_counts[card.lane] = lane_counts.get(card.lane, 0) + 1
    return {
        "nudges": cards,
        "windows": windows.weekends + windows.blocks,
        "skipped": windows.skipped,
        "note": "; ".join(n for n in [*notes, games_note] if n),
        "lanes": lane_counts,
        "decision_trip": decision.get("trip", ""),
    }


# ---- windows -------------------------------------------------------------------------------------


@dataclass
class _Windows:
    weekends: list[_n.NudgeWindow]
    blocks: list[_n.NudgeWindow]  # the week-long windows of the long-lead lane
    family: list[_n.NudgeWindow]
    skipped: list[str] = field(default_factory=list)  # why a window isn't offered, one line each


def _open_windows(
    svc: TravelService,
    reference: list[EventResult],
    trips: list[Trip],
    today: date,
    horizon_days: int,
    include_taken: bool,
) -> _Windows:
    """Every window the digest may offer, with the family windows set apart and the ones a live
    trip already occupies removed (nudging those would nag a decision already made)."""
    weights = svc.weights.nudge
    weekends = _n.enumerate_windows(
        reference, today, horizon_days=horizon_days, family_windows=weights.family_windows
    )
    blocks = _n.enumerate_blocks(
        reference, today, min_days=weights.long_lead_min_days, max_days=weights.long_lead_max_days,
        family_windows=weights.family_windows,
    )
    # One family card per holiday. A holiday can anchor both a weekend and a week block (New Year's
    # Day does), so they are keyed by anchor date and the weekend shape wins.
    family = [w for w in weekends if w.kind == "family"]
    anchors = {w.anchor_date for w in family}
    for w in blocks:
        if w.kind == "family" and w.anchor_date not in anchors:
            family.append(w)
            anchors.add(w.anchor_date)
    skipped = [f"{w.label} — family window ({w.anchor})" for w in family]

    active = [t for t in trips if is_live(t.status) and t.start and t.end]

    def still_open(candidates: list[_n.NudgeWindow]) -> list[_n.NudgeWindow]:
        if include_taken:
            return candidates
        kept = []
        for w in candidates:
            taken_by = next(
                (t for t in active if t.start and t.end and t.start <= w.end and w.start <= t.end), None
            )
            if taken_by is None:
                kept.append(w)
            else:
                skipped.append(f"{w.label} — {trip_name(taken_by)} already on the calendar")
        return kept

    return _Windows(
        weekends=still_open([w for w in weekends if w.kind != "family"]),
        blocks=still_open([w for w in blocks if w.kind != "family"]),
        family=family,
        skipped=skipped,
    )


# ---- picking -------------------------------------------------------------------------------------


class _Picker:
    """Chooses the cards to show from a ranked list, so the digest stays varied: a destination
    appears once across all lanes, and a window once within a lane. Without it the top pairs collapse
    into one strong destination in every window it wins."""

    def __init__(self, svc: TravelService) -> None:
        self._svc = svc
        self._destinations = svc.reader._root / "destinations"
        self.shown: set[str] = set()  # destination slugs already on a card

    def pick(self, ranked: list[_n.NudgeCandidate], limit: int) -> list[_n.NudgeCandidate]:
        chosen: list[_n.NudgeCandidate] = []
        windows: set[str] = set()
        for cand in ranked:
            window = cand.window.start.isoformat()
            if cand.slug in self.shown or window in windows:
                continue
            _n.assemble_reason(cand, self._svc.weights)
            self.link(cand)
            chosen.append(cand)
            self.shown.add(cand.slug)
            windows.add(window)
            if len(chosen) >= limit:
                break
        return chosen

    def link(self, cand: _n.NudgeCandidate) -> None:
        """Point the card at its destination note in the vault, and at that note's lead image."""
        note = _resolve_folder_note(self._destinations, cand.slug)
        if note.exists():
            cand.doc = f"travel/destinations/{note.relative_to(self._destinations)}"
            cand.image = hero_image(note) or ""


def _ranked(
    svc: TravelService,
    pairs: list[_n.NudgeCandidate],
    leaders: int,
    today: date,
    live: bool,
    notes: list[str],
) -> list[_n.NudgeCandidate]:
    """`pairs` best first. With `live`, the top `leaders` get the live enrich first, which can
    reorder them; what the enrich couldn't do is appended to `notes`."""
    pairs.sort(key=lambda c: c.score, reverse=True)
    if live:
        notes.append(_nudge_enrich(svc, pairs[:leaders], today))
        pairs.sort(key=lambda c: c.score, reverse=True)
    return pairs


# ---- the lanes that aren't a plain rank-and-pick -------------------------------------------------


def _game_cards(
    svc: TravelService,
    picker: _Picker,
    games: list[_g.MarketGame],
    weekends: list[_n.NudgeWindow],
    reference: list[EventResult],
    today: date,
    *,
    shown: list[_n.NudgeCandidate],
) -> list[_n.NudgeCandidate]:
    """One card per game weekend, except where a card already `shown` offers that destination on
    an overlapping weekend."""

    def already_offered(slug: str, w: _n.NudgeWindow) -> bool:
        return any(
            c.slug == slug and c.window.start <= w.end and w.start <= c.window.end for c in shown
        )

    cards = _g.build_cards(games, weekends, svc.weights, reference, today, blocked=already_offered)
    for card in cards:
        picker.link(card)
    return cards


def _horizon_candidates(
    svc: TravelService, picker: _Picker, slugs: list[str], trips: list[Trip], today: date
) -> list[_n.NudgeCandidate]:
    """The destinations not already on a card, best first. "Tripped" means the destination's name
    appears on any trip record, planned or done: trips name their destination in free text."""
    trip_text = " ".join(f"{t.destination} {t.title} {t.slug}".lower() for t in trips)
    candidates = []
    for slug in slugs:
        if slug in picker.shown:
            continue
        tripped = _n._display(slug).lower() in trip_text or slug in trip_text
        candidate = _n.horizon_candidate(slug, svc.weights, today, tripped=tripped)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _family_cards(
    svc: TravelService, family: list[_n.NudgeWindow], games: list[_g.MarketGame]
) -> list[_n.NudgeCandidate]:
    """One authored card per family window. A followed team's home game inside the window is
    mentioned on the card; it is never offered as a trip."""
    markets = svc.weights.events.centerpiece_markets
    cards = []
    for w in sorted(family, key=lambda w: w.start):
        card = _n.family_card(w, svc.weights)
        if card is None:
            continue
        home_games = [
            g for g in games
            if g.market.follow_away and g.home
            and w.start.isoformat() <= g.event.local_date <= w.end.isoformat()
        ]
        if home_games:
            card.events = [g.event for g in home_games]
            card.reason += "".join(
                f" · {_g.matchup(g.event, markets)} at {g.event.venue}, {_g.when_label(g.event)}"
                for g in home_games
            )
        cards.append(card)
    return cards


def _mark_in_decision(cards: list[_n.NudgeCandidate], decision: dict[str, Any]) -> None:
    """Flag the cards whose destination the open decision is already weighing."""
    labels = [str(c.get("label", "")) for c in decision.get("candidates", []) if isinstance(c, dict)]
    for card in cards:
        if card.lane != "family":
            card.in_decision = any(_same_place(card.display, label) for label in labels)


def _same_place(display: str, label: str) -> bool:
    """Whether a card's destination and a decision candidate name the same place: equal ignoring
    case, or one containing the other ("San Diego" and "San Diego (La Jolla)")."""
    d, lbl = display.strip().lower(), label.strip().lower()
    return bool(d and lbl) and (d == lbl or d in lbl or lbl in d)


# ---- live reads ----------------------------------------------------------------------------------


def _game_prepass(svc: TravelService, today: date, horizon_days: int) -> tuple[list[Any], str]:
    """Every followed market's centerpiece games over the horizon, each placed on the
    registry card it rides — one Ticketmaster search per market, cached for the day. A market
    whose search fails is named in the note (a partial result never reads as a clean empty)."""
    markets = svc.weights.events.centerpiece_markets
    if not get_settings().ticketmaster_key:
        return [], "game weekends skipped (TICKETMASTER_KEY is not set)"
    provider = get_event_provider("ticketmaster")
    cache = harness_cache_dir() / "games"
    start, end = today, today + timedelta(days=horizon_days)
    out: list[_g.MarketGame] = []
    failed: list[str] = []
    for m in markets:
        try:
            found = _g.fetch_market_games(provider, m, start, end, cache_dir=cache, today=today)
        except ProviderError:
            failed.append(m.team)
            continue
        for e in found:
            g = _g.place_game(e, m, markets)
            if g is not None and g.slug in svc.weights.destination_nudge:
                out.append(g)
    note = f"game weekends: no schedule for {', '.join(failed)}" if failed else ""
    return out, note


def _nudge_enrich(svc: TravelService, cands: list[Any], today: date) -> str:
    """Live enrich for the static top pairs: TM events per DISTINCT (city, window) + weather for
    in-horizon windows. Best-effort — a missing TM key or transient provider error degrades to
    the static result with an honest note, never an exception (the almanac already carried the
    free layer)."""
    notes: list[str] = []
    # events: one scan per distinct (city, window-start) — the top-pairs cap bounds the calls
    ev_cache: dict[tuple[str, str], list[EventResult]] = {}
    try:
        for cand in cands:
            if not cand.city:
                continue
            key = (cand.city, cand.window.start.isoformat())
            if key not in ev_cache:
                found = svc.scan_events([cand.city], cand.window.start, cand.window.end)
                ev_cache[key] = found[0].events if found else []
            live_events = [e for e in ev_cache[key] if e not in cand.events]
            if live_events:
                cand.events = cand.events + live_events
                _n._apply_event_components(cand, svc.weights)
                cand.score = round(sum(c.points for c in cand.components), 1)
    except ProviderError as err:
        notes.append(f"live events unavailable ({err}) — almanac-only")

    wx_cache: dict[str, list[Any]] = {}
    for cand in cands:
        if not cand.city or (cand.window.start - today).days > _n.WEATHER_HORIZON_DAYS:
            continue
        try:
            if cand.city not in wx_cache:
                wx_cache[cand.city] = svc.get_weather(cand.city, cand.window.start, cand.window.end).days
            _n.apply_weather(cand, wx_cache[cand.city], svc.weights)
        except ProviderError:
            continue  # forecast horizon / geocode miss — season already carries the judgment
    return "; ".join(notes)
