"""Composite ranker: flight + screen + batch-relative price -> ranked Shortlist.

Output is always a ranked shortlist (never a single pick). Every score is explainable via
ScoreBreakdown.rationale. Hard-no screens are filtered out; in-screen kept-with-pushback.
"""

from __future__ import annotations

from harness.travel.models import (
    Candidate,
    FlightOffer,
    ScoreBreakdown,
    ScoredCandidate,
    Shortlist,
)
from harness.travel.ranking.flight_score import score_flight
from harness.travel.ranking.screen import score_screen
from harness.travel.ranking.weights import WeightConfig

CandidateOffers = tuple[Candidate, list[FlightOffer]]


def _pick_best_offer(
    offers: list[FlightOffer], weights: WeightConfig
) -> tuple[FlightOffer | None, float, list[str]]:
    """Best = highest flight_score (so a local-airport offer can beat a marginally cheaper hub one)."""
    best: FlightOffer | None = None
    best_score = 0.0
    best_rationale: list[str] = []
    for offer in offers:
        fs = score_flight(offer, weights.flight)
        if best is None or fs.total > best_score:
            best, best_score, best_rationale = offer, fs.total, fs.rationale
    return best, best_score, best_rationale


def _price_components(
    scored: list[ScoredCandidate], max_points: float
) -> dict[str, float]:
    """Batch-relative: cheapest gets max_points, priciest 0; neutral (half) if not comparable."""
    priced = [(sc.candidate.slug, sc.best_flight.price_usd) for sc in scored if sc.best_flight]
    if len(priced) < 2:
        return {slug: max_points / 2 for slug, _ in priced}
    lo = min(p for _, p in priced)
    hi = max(p for _, p in priced)
    if hi == lo:
        return {slug: max_points / 2 for slug, _ in priced}
    return {slug: max_points * (hi - price) / (hi - lo) for slug, price in priced}


def rank_candidates(
    items: list[CandidateOffers], weights: WeightConfig, window: str
) -> Shortlist:
    scored: list[ScoredCandidate] = []
    dropped_notes: list[str] = []

    for candidate, offers in items:
        screen = score_screen(candidate.screen_status, weights.screen)
        if screen.filtered_out:
            reason = "; ".join(screen.rationale)
            dropped_notes.append(f"{candidate.display_name}: dropped — {reason}")
            continue

        best, flight_total, flight_rationale = _pick_best_offer(offers, weights)
        breakdown = ScoreBreakdown()
        rationale: list[str] = []

        if best is None:
            breakdown.components["flight"] = 0.0
            rationale.append("no flight offer found (no priced route within constraints)")
        else:
            breakdown.components["flight"] = flight_total
            rationale.extend(flight_rationale)
            rationale.append(
                f"best: {best.carrier} {best.origin_iata}->{best.dest_iata} "
                f"${best.price_usd:.0f}"
            )

        breakdown.components["screen_penalty"] = -screen.penalty
        rationale.extend(screen.rationale)

        # Home-airport convenience. A live local-airport offer already carries the bonus via
        # flight_score — that path lights up once a GDS provider that can see the local airport is
        # wired. Until then, Google Flights may not price it at all, so we apply the bonus from CORPUS
        # KNOWLEDGE when the destination is home-airport-served. The shown fare is the hub equivalent;
        # the local-airport fare must be checked with the carrier directly.
        ha = weights.flight.home_airport
        hub = weights.flight.comparison_airport
        live_home = best is not None and bool(ha) and best.origin_iata == ha
        corpus_home = (
            not live_home and bool(ha)
            and candidate.dest_iata in set(weights.flight.home_airport_served_iata)
        )
        if corpus_home:
            breakdown.components["home_airport_corpus_bonus"] = weights.flight.home_airport_bonus
            rationale.append(
                f"{ha}-reachable (per corpus): +{weights.flight.home_airport_bonus:g} convenience — "
                f"the shown fare is the {hub} equivalent; check the carrier direct for {ha} fares"
            )

        # Lodging (v0): informational only — NOT a gate. The bar is "best-in-class for the
        # area" (v1 hotel search scores availability); lacking anchors never excludes a place.
        if candidate.lodging_anchors:
            rationale.append(f"lodging anchors on file ({len(candidate.lodging_anchors)})")
        else:
            rationale.append("no lodging anchors on file yet (best-in-class scored in v1)")

        breakdown.rationale = rationale
        scored.append(
            ScoredCandidate(candidate=candidate, best_flight=best, score=breakdown)
        )

    # Batch-relative price component, then finalize totals + sort.
    price_pts = _price_components(scored, weights.flight.price_component_max)
    for sc in scored:
        flight = sc.score.components.get("flight", 0.0)
        screen_pen = sc.score.components.get("screen_penalty", 0.0)
        home_corpus = sc.score.components.get("home_airport_corpus_bonus", 0.0)
        price = price_pts.get(sc.candidate.slug, 0.0)
        if sc.best_flight:
            sc.score.components["price_component"] = price
            sc.score.rationale.append(f"price vs batch: +{price:.0f}")
        sc.score.total = flight + screen_pen + home_corpus + price
        sc.score.rationale.append(f"TOTAL {sc.score.total:.0f}")

    scored.sort(key=lambda s: s.score.total, reverse=True)

    notes = _home_airport_tradeoff_notes(scored, weights) + dropped_notes
    return Shortlist(window=window, candidates=scored, notes=notes)


def _home_airport_tradeoff_notes(scored: list[ScoredCandidate], weights: WeightConfig) -> list[str]:
    ha = weights.flight.home_airport
    hub = weights.flight.comparison_airport
    if not ha:
        return []  # no local airport configured → no convenience tradeoff to surface
    home_airport_served = set(weights.flight.home_airport_served_iata)
    note = f" ({weights.flight.home_airport_note})" if weights.flight.home_airport_note else ""
    reachable = [
        s.candidate.display_name
        for s in scored
        if (s.best_flight and s.best_flight.origin_iata == ha)
        or s.candidate.dest_iata in home_airport_served
    ]
    if reachable:
        return [
            f"{ha} tradeoff: "
            + ", ".join(reachable)
            + f" is/are reachable from {ha}{note} and carry the convenience bonus; the rest route "
            f"through {hub}. {ha} is a heavy weight, not a filter."
        ]
    return [f"{ha} tradeoff: none of these candidates are {ha}-reachable on current routes — all via {hub}."]
