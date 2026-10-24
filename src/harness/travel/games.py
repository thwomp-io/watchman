"""The GAME-FIRST nudge pass — centerpiece games become trip windows instead of only bonusing them.

The weekend lane scores windows × destinations statically and live-enriches only its top pairs, so a
game in a city that doesn't already rank is never looked up. This pass inverts that for the
centerpiece leagues: each followed market's games are fetched once across the whole horizon (one
Ticketmaster call per market, cached for the day), every game that lands on an open weekend (Fri–Sun,
or a Thu/Mon game that stretches it) becomes a candidate on that market's destination card, and the
card's line leads with the game ("Warriors vs. Lakers · Fri 10/30 7:00p — peak October season there").

Deterministic: the corpus market table says which teams, where they're searched and which card they
ride; the opponent never filters (a soft market only weighs less).

`build_cards` turns the placed games into the lane's cards: one per game, a PAIR card when two
leagues play one destination the same weekend (the NBA game and the followed NFL team's), and a
CORRIDOR card when two or more games fall in one weekend across a configured corridor. Convenience is
weighted by lead time (the curve multiplies feasibility), dead windows are skipped, and the greedy pick
keeps one card per destination per weekend.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from itertools import product
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from harness.travel.models import EventResult
from harness.travel.nudge import (
    NudgeCandidate,
    NudgeComponent,
    NudgeWindow,
    _apply_event_components,
    _display,
    _short_name,
    lead_factor,
    score_static,
)
from harness.travel.ranking.weights import CenterpieceMarket, GameCorridor, WeightConfig

# listings that share a game's name but aren't the game ("Comets VIP Packages: 11/12/2026")
_NOT_A_GAME = ("parking", "suite", "premium", "hospitality", "club access", "tailgate", "package", "vip ")


class MarketGame(BaseModel):
    """One centerpiece game and the destination card it rides."""

    market: CenterpieceMarket
    slug: str  # the registry slug whose card it lands on
    event: EventResult
    home: bool  # the market's team is the home side


def _cache_name(market: CenterpieceMarket) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{market.league}-{market.team}".lower()).strip("-") + ".json"


def is_game(event: EventResult, market: CenterpieceMarket) -> bool:
    """A real league game for this team — not a parking pass or a hospitality package."""
    name = event.name.lower()
    return (
        event.subgenre.strip().upper() == market.league.strip().upper()
        and market.team.lower() in name
        and not any(word in name for word in _NOT_A_GAME)
    )


def fetch_market_games(
    provider: Any,
    market: CenterpieceMarket,
    start: date,
    end: date,
    *,
    cache_dir: Path | None,
    today: date,
) -> list[EventResult]:
    """The market's games in [start, end] — one search for the whole horizon, cached for the day
    (a schedule moves by the week, not the minute). A `follow_away` market is searched by team name
    (home + away); every other market by its venue city."""
    path = cache_dir / _cache_name(market) if cache_dir else None
    if path is not None and path.is_file():
        try:
            blob = json.loads(path.read_text())
            if blob.get("day") == today.isoformat() and blob.get("start") == start.isoformat() \
                    and blob.get("end") == end.isoformat():
                return [EventResult.model_validate(e) for e in blob.get("events", [])]
        except (ValueError, OSError):
            pass  # a torn cache file is a miss
    if market.follow_away:
        found = provider.search_events("", start, end, classification=market.league, keyword=market.team)
    else:
        found = provider.search_events(market.city, start, end, classification=market.league)
    games: list[EventResult] = []
    seen: set[tuple[str, str]] = set()
    for e in found:
        key = (e.local_date, _short_name(e.name).lower())
        if is_game(e, market) and key not in seen:
            seen.add(key)
            games.append(e)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "day": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(),
            "events": [g.model_dump(mode="json") for g in games],
        }))
    return games


def place_game(
    event: EventResult, market: CenterpieceMarket, markets: list[CenterpieceMarket]
) -> MarketGame | None:
    """Which card a game rides. A city-searched market's games are its home games. A
    `follow_away` team's game lands on the market whose city hosts it (its own city = home); a game
    in a city no market names has no card (None)."""
    if not market.follow_away:
        return MarketGame(market=market, slug=market.nudge_as, event=event, home=True)
    venue = event.city.strip().lower()
    if venue == market.city.strip().lower():
        return MarketGame(market=market, slug=market.nudge_as, event=event, home=True)
    host = next((m for m in markets if m.city.strip().lower() == venue), None)
    if host is None:
        return None
    return MarketGame(market=market, slug=host.nudge_as, event=event, home=False)


def game_window(d: date, weekends: list[NudgeWindow]) -> NudgeWindow | None:
    """The trip window a game on day `d` makes: the open weekend containing it, or — for a Thursday
    or Monday game — that weekend stretched to the game day, the extra weekday counted as PTO.
    Tue/Wed games make no weekend (None); so does a weekend that's taken or not enumerated."""
    for w in weekends:
        if w.start <= d <= w.end:
            return w
    if d.weekday() == 3:  # Thursday → the weekend that starts the next day
        nxt = next((w for w in weekends if w.start == d + timedelta(days=1)), None)
        if nxt is not None:
            return nxt.model_copy(update={"start": d, "pto_days": nxt.pto_days + 1})
    if d.weekday() == 0:  # Monday → the weekend that ended the day before
        prv = next((w for w in weekends if w.end == d - timedelta(days=1)), None)
        if prv is not None:
            return prv.model_copy(update={"end": d, "pto_days": prv.pto_days + 1})
    return None


def short_team(side: str, markets: list[CenterpieceMarket]) -> str:
    """'Golden State Warriors' → 'Warriors' (a market's team name when one matches, else the last
    word — 'Atlanta Hawks' → 'Hawks'). Promo text after the team is dropped first: 'Harbor
    Gulls: Star Guard Bobblehead', 'Gulls ("Splash" Bobblehead…)', '… - Fan Night'."""
    s = re.split(r"\s*(?:[(\[:]|\s-\s)", side.strip(), maxsplit=1)[0].strip()
    for m in markets:
        if m.team.lower() in s.lower():
            return m.team
    return s.split()[-1] if s.split() else s


def matchup(event: EventResult, markets: list[CenterpieceMarket]) -> str:
    """'Golden State Warriors vs. Los Angeles Lakers' → 'Warriors vs. Lakers'."""
    name = _short_name(event.name)
    # listings write "vs.", "vs", a bare "v" (Washington's), "at" or "@"
    parts = re.split(r"\s+(?:vs?\.?|at|@)\s+", name, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return name
    return f"{short_team(parts[0], markets)} vs. {short_team(parts[1], markets)}"


def when_label(event: EventResult) -> str:
    """'Fri 10/30 7:00p' — the game's local day and tip-off."""
    try:
        d = date.fromisoformat(event.local_date)
    except ValueError:
        return event.local_date
    out = f"{d.strftime('%a')} {d.month}/{d.day}"
    if event.local_time:
        try:
            hh, mm = (int(x) for x in event.local_time.split(":")[:2])
        except ValueError:
            return out
        out += f" {hh % 12 or 12}:{mm:02d}{'a' if hh < 12 else 'p'}"
    return out


def game_line(game: MarketGame, markets: list[CenterpieceMarket]) -> str:
    """The game, as a card leads with it: 'Warriors vs. Lakers · Fri 10/30 7:00p'."""
    return f"{matchup(game.event, markets)} · {when_label(game.event)}"


def game_candidate(
    game: MarketGame,
    window: NudgeWindow,
    weights: WeightConfig,
    reference: list[EventResult],
    today: date | None = None,
) -> NudgeCandidate:
    """The GAME WEEKENDS card for one game: the destination's static score for the game's window
    with the game as its centerpiece event (a soft market's pull scaled down), the line leading with
    the matchup and tip-off, then the window's strongest other reasons. With `today`, feasibility is
    scaled by the lead-time convenience curve and a zero-point `lead_time` component records why."""
    markets = weights.events.centerpiece_markets
    days = (window.start - today).days if today is not None else None
    conv = lead_factor(weights.nudge.convenience_lead_curve, days) if days is not None else 1.0
    cand = score_static(game.slug, window, weights, reference, lane="games", convenience=conv)
    others = [e for e in cand.events if e != game.event]
    cand.events = [game.event, *others]
    _apply_event_components(cand, weights)
    # the game leads the events list whatever the tier sort did
    cand.events = [game.event, *[e for e in cand.events if e != game.event]]
    if game.market.weight == "soft":
        cand.components = [
            NudgeComponent(name=c.name, points=c.points * weights.nudge.games_soft_factor, fact=c.fact)
            if c.name == "event" else c
            for c in cand.components
        ]
    if days is not None and conv != 1.0:
        cand.components.append(NudgeComponent(
            name="lead_time", points=0.0, fact=f"{days} days out: convenience counts x{conv:.1f}"
        ))
    cand.score = round(sum(c.points for c in cand.components), 1)
    cand.reason = _reason(game_line(game, markets), cand, window)
    return cand


def _reason(lead: str, cand: NudgeCandidate, window: NudgeWindow) -> str:
    rest = [
        c.fact for c in sorted(cand.components, key=lambda c: c.points, reverse=True)
        if c.points > 0 and c.name not in ("event", "pair", "corridor") and c.fact
    ][:2]
    out = f"{lead} — {' and '.join(rest)}" if rest else lead
    if window.pto_days:
        out += f" ({window.pto_days} PTO day{'s' if window.pto_days != 1 else ''})"
    return out


# ————— the lane's cards: singles, pairs, corridors, and the pick —————


def saturday(window: NudgeWindow) -> date:
    """A weekend's identity is its Saturday — a Thursday- or Monday-stretched window is still that weekend."""
    return window.start + timedelta(days=(5 - window.start.weekday()) % 7)


class _Single(BaseModel):
    game: MarketGame
    window: NudgeWindow
    cand: NudgeCandidate


def _merged_window(items: list[_Single], weekends: list[NudgeWindow]) -> NudgeWindow:
    """The window that holds every game: earliest start → latest end, one PTO day per side stretched
    past the enumerated weekend (a Thursday tip-off and a Monday night game make two)."""
    sat = saturday(items[0].window)
    base = next((w for w in weekends if w.start <= sat <= w.end), items[0].window)
    start = min(i.window.start for i in items)
    end = max(i.window.end for i in items)
    pto = base.pto_days + (1 if start < base.start else 0) + (1 if end > base.end else 0)
    return base.model_copy(update={"start": start, "end": end, "pto_days": pto})


def _combined(
    items: list[_Single],
    weekends: list[NudgeWindow],
    weights: WeightConfig,
    *,
    component: NudgeComponent,
    title: str = "",
) -> NudgeCandidate:
    """One card for several games: the strongest game's card, stretched over all their days, every
    game named in the line (in date order), plus the pair/corridor component."""
    markets = weights.events.centerpiece_markets
    best = max(items, key=lambda i: i.cand.score)
    window = _merged_window(items, weekends)
    ordered = sorted(items, key=lambda i: (i.game.event.local_date, i.game.event.local_time or ""))
    games = [i.game.event for i in ordered]
    cand = best.cand.model_copy(deep=True)
    cand.window = window
    cand.events = [*games, *[e for e in best.cand.events if e not in games]]
    cand.components = [*cand.components, component]
    cand.score = round(sum(c.points for c in cand.components), 1)
    if title:
        cand.display = title
    cand.reason = _reason(" + ".join(game_line(i.game, markets) for i in ordered), cand, window)
    return cand


def _event_points(cand: NudgeCandidate) -> float:
    return sum(c.points for c in cand.components if c.name == "event")


def _dead(window: NudgeWindow, weights: WeightConfig) -> bool:
    return any(d.start <= window.end and window.start <= d.end for d in weights.nudge.games_dead_windows)


def build_cards(
    games: list[MarketGame],
    weekends: list[NudgeWindow],
    weights: WeightConfig,
    reference: list[EventResult],
    today: date,
    *,
    blocked: Callable[[str, NudgeWindow], bool] = lambda slug, w: False,
) -> list[NudgeCandidate]:
    """The GAME WEEKENDS lane, picked. Every game on an open weekend becomes a card; two leagues at
    one destination in one weekend also become a PAIR card; two or more games across a corridor in
    one weekend a CORRIDOR card. Greedy by score: a (destination, weekend) is held by one card at
    most, a weekend by `games_per_window` cards, a destination by `games_per_city`, the lane by
    `games_top`. `blocked(slug, window)` drops a game another lane already shows."""
    nw = weights.nudge
    singles: list[_Single] = []
    for g in games:
        try:
            gd = date.fromisoformat(g.event.local_date)
        except ValueError:
            continue
        gw = game_window(gd, weekends)
        if gw is None or _dead(gw, weights) or blocked(g.slug, gw):
            continue
        singles.append(_Single(game=g, window=gw, cand=game_candidate(g, gw, weights, reference, today)))

    pool: list[tuple[NudgeCandidate, set[tuple[str, date]]]] = [
        (s.cand, {(s.game.slug, saturday(s.window))}) for s in singles
    ]

    by_key: dict[tuple[str, date], list[_Single]] = {}
    for s in singles:
        by_key.setdefault((s.game.slug, saturday(s.window)), []).append(s)
    for key, items in by_key.items():
        leagues = {s.game.market.league.upper() for s in items}
        if len(leagues) < 2:
            continue
        # one game per league, the combination that stretches the trip least (fewest PTO days —
        # the same-day double first), then the strongest
        per_league = [[s for s in items if s.game.market.league.upper() == lg] for lg in sorted(leagues)]
        combos = [list(c) for c in product(*per_league)]
        chosen = min(
            combos,
            key=lambda combo: (_merged_window(combo, weekends).pto_days, -sum(s.cand.score for s in combo)),
        )
        chosen.sort(key=lambda s: s.game.event.local_date)
        lead = max(chosen, key=lambda s: s.cand.score)
        # both games count: the lead game's card already holds its own event points; add the
        # others' (a soft market's still scaled), plus the pair premium
        others = sum(_event_points(s.cand) for s in chosen if s is not lead)
        teams = " and ".join(s.game.market.team for s in chosen)
        comp = NudgeComponent(
            name="pair", points=round(others + nw.games_pair_bonus, 1), fact=f"{teams} the same weekend"
        )
        pool.append((_combined(chosen, weekends, weights, component=comp), {key}))

    for corridor in nw.games_corridors:
        pool.extend(_corridor_cards(corridor, singles, weekends, weights))

    chosen_cards: list[NudgeCandidate] = []
    held: set[tuple[str, date]] = set()
    per_window: dict[date, int] = {}
    per_city: dict[str, int] = {}
    for cand, keys in sorted(pool, key=lambda p: p[0].score, reverse=True):
        if len(chosen_cards) >= nw.games_top:
            break
        sat = next(iter(keys))[1]
        slugs = {k[0] for k in keys}
        if keys & held or per_window.get(sat, 0) >= nw.games_per_window:
            continue
        if any(per_city.get(slug, 0) >= nw.games_per_city for slug in slugs):
            continue
        chosen_cards.append(cand)
        held |= keys
        per_window[sat] = per_window.get(sat, 0) + 1
        for slug in slugs:
            per_city[slug] = per_city.get(slug, 0) + 1
    return chosen_cards


def _corridor_cards(
    corridor: GameCorridor, singles: list[_Single], weekends: list[NudgeWindow], weights: WeightConfig
) -> list[tuple[NudgeCandidate, set[tuple[str, date]]]]:
    """A corridor's weekends as an itinerary: one game a night across its destinations — each day's
    best game, a city not yet on the trip first — when that makes two or more game nights. The
    bonus is per game night beyond the first (a night holds one game, however many are on), and the
    card holds every member destination it visits that weekend."""
    by_sat: dict[date, list[_Single]] = {}
    for s in singles:
        if s.game.slug in corridor.slugs:
            by_sat.setdefault(saturday(s.window), []).append(s)
    out: list[tuple[NudgeCandidate, set[tuple[str, date]]]] = []
    for sat, items in by_sat.items():
        by_day: dict[str, list[_Single]] = {}
        for s in items:
            by_day.setdefault(s.game.event.local_date, []).append(s)
        nights: list[_Single] = []
        for day in sorted(by_day):
            visited = {n.game.slug for n in nights}
            nights.append(min(by_day[day], key=lambda s: (s.game.slug in visited, -s.cand.score)))
        if len(nights) < 2 or len({n.game.slug for n in nights}) < 2:
            continue  # one city's games alone are that city's own cards, not a corridor
        cities = sorted({_display(n.game.slug) for n in nights})
        comp = NudgeComponent(
            name="corridor",
            points=weights.nudge.games_corridor_bonus * (len(nights) - 1),
            fact=f"{len(nights)} game nights across {', '.join(cities)}",
        )
        card = _combined(nights, weekends, weights, component=comp, title=corridor.name)
        out.append((card, {(n.game.slug, sat) for n in nights}))
    return out
