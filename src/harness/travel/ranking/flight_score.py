"""Flight scoring for a single offer.

Additive, explainable. The home-airport convenience bonus is a heavy thumb-on-the-scale applied
to local-airport offers — NOT a filter. Price is handled separately (batch-relative) in the
ranker, since it requires the whole candidate set to normalize.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.travel.models import FlightOffer
from harness.travel.ranking.weights import FlightWeights

BASE_FLIGHT_SCORE = 100.0


@dataclass
class FlightScore:
    total: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)


def score_flight(offer: FlightOffer, weights: FlightWeights) -> FlightScore:
    score = FlightScore(components={"base": BASE_FLIGHT_SCORE})
    total = BASE_FLIGHT_SCORE
    rationale: list[str] = []

    # Over-soft-cap duration penalty (≤4h preferred; soft, not a filter).
    over = max(0.0, offer.duration_hours - weights.hour_soft_cap)
    if over > 0:
        pen = over * weights.over_cap_penalty_per_hour
        total -= pen
        score.components["over_cap_penalty"] = -pen
        rationale.append(
            f"{offer.duration_hours:.1f}h is {over:.1f}h over the {weights.hour_soft_cap:g}h "
            f"soft cap (-{pen:.0f})"
        )
    else:
        rationale.append(f"{offer.duration_hours:.1f}h within the {weights.hour_soft_cap:g}h cap")

    # Connection penalty (2+ = high-friction).
    conn = weights.connection_penalty_for(offer.stops)
    if conn > 0:
        total -= conn
        score.components["connection_penalty"] = -conn
        rationale.append(f"{offer.stops} stop(s) (-{conn:.0f})")
    else:
        rationale.append("nonstop")

    # home-airport convenience bonus — heavy weight, applies only to local-airport offers.
    if weights.home_airport and offer.origin_iata == weights.home_airport:
        total += weights.home_airport_bonus
        score.components["home_airport_bonus"] = weights.home_airport_bonus
        note = f" ({weights.home_airport_note})" if weights.home_airport_note else ""
        rationale.append(
            f"{weights.home_airport}-reachable: +{weights.home_airport_bonus:g} convenience{note}"
        )

    score.total = total
    score.rationale = rationale
    return score
