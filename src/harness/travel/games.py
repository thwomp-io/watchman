"""The GAME-FIRST nudge pass — centerpiece games become trip windows instead of only bonusing them.

The weekend lane scores windows × destinations statically and live-enriches only its top pairs, so a
game in a city that doesn't already rank is never looked up. This pass inverts that for the
centerpiece leagues: each followed market's games are fetched once across the whole horizon (one
Ticketmaster call per market, cached for the day), every game that lands on an open weekend (Fri–Sun,
or a Thu/Mon game that stretches it) becomes a candidate on that market's destination card, and the
card's line leads with the game ("Warriors vs. Lakers · Fri 10/30 7:00p — peak October season there").

Deterministic: the corpus market table says which teams, where they're searched and which card they
ride; the opponent never filters (a soft market only weighs less).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from harness.travel.models import EventResult
from harness.travel.nudge import (
    NudgeCandidate,
    NudgeComponent,
    NudgeWindow,
    _apply_event_components,
    _short_name,
    score_static,
)
from harness.travel.ranking.weights import CenterpieceMarket, WeightConfig

# listings that share a game's name but aren't the game
_NOT_A_GAME = ("parking", "suite", "premium", "hospitality", "club access", "tailgate")


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
        and not name.startswith(_NOT_A_GAME)
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
    word — 'Atlanta Hawks' → 'Hawks')."""
    s = side.strip()
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
) -> NudgeCandidate:
    """The GAME WEEKENDS card for one game: the destination's static score for the game's window
    with the game as its centerpiece event (a soft market's pull scaled down), the line leading with
    the matchup and tip-off, then the window's strongest other reasons."""
    markets = weights.events.centerpiece_markets
    cand = score_static(game.slug, window, weights, reference, lane="games")
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
    cand.score = round(sum(c.points for c in cand.components), 1)
    rest = [
        c.fact for c in sorted(cand.components, key=lambda c: c.points, reverse=True)
        if c.points > 0 and c.name != "event" and c.fact
    ][:2]
    lead = game_line(game, markets)
    cand.reason = f"{lead} — {' and '.join(rest)}" if rest else lead
    if window.pto_days:
        cand.reason += f" ({window.pto_days} PTO day{'s' if window.pto_days != 1 else ''})"
    return cand
