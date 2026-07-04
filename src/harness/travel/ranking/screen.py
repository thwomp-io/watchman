"""Screen-axis scoring.

hard_no  -> candidate filtered OUT entirely.
in_screen -> penalize-but-KEEP, and surface the corpus calibration prose as data-pushback
             (data informs the screen; the screen never silently filters).
clean    -> no penalty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.travel.models import ScreenStatus
from harness.travel.ranking.weights import ScreenWeights


@dataclass
class ScreenResult:
    filtered_out: bool = False
    penalty: float = 0.0
    rationale: list[str] = field(default_factory=list)


def score_screen(status: ScreenStatus, weights: ScreenWeights) -> ScreenResult:
    result = ScreenResult()

    for axis_name, verdict, axis_w in (
        ("geological", status.geological, weights.geological),
        ("social/crime", status.social_crime, weights.social_crime),
    ):
        if verdict == "hard_no":
            result.filtered_out = True
            result.rationale.append(f"{axis_name}: hard-no — excluded")
        elif verdict == "in_screen":
            result.penalty += axis_w.in_screen_penalty
            result.rationale.append(
                f"{axis_name}: in-screen (-{axis_w.in_screen_penalty:g}), kept with calibration"
            )
        else:
            result.rationale.append(f"{axis_name}: clean")

    # Surface calibration prose for any in-screen axis (data-pushback, not hidden).
    if status.calibration_notes and not result.filtered_out:
        result.rationale.extend(f"calibration: {n}" for n in status.calibration_notes)

    return result
