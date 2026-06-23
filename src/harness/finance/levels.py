"""Deterministic support-level detection from daily bars.

The trap-setting discipline wants CHARTED levels, not round percentages — a buy set at an
observed swing-low support tends to hold better than one at an arbitrary round number. This
module makes that observation mechanical: swing lows (local minima of the low series) clustered
by proximity into named levels with touch counts. Pure functions, no I/O, no model —
deterministic-core doctrine.

This is an observation surface for the sounding-board: levels are *descriptions of past price
behavior*, never predictions or recommendations.
"""

from __future__ import annotations

from statistics import median

from harness.finance.models import Bar, SupportLevel


def swing_lows(bars: list[Bar], *, wing: int = 2) -> list[int]:
    """Indices whose low is the strict minimum of the ±``wing`` window around them.

    Endpoints (without a full wing on both sides) are excluded — a first/last bar can't be
    called a *swing* low yet.
    """
    out: list[int] = []
    for i in range(wing, len(bars) - wing):
        window = bars[i - wing : i + wing + 1]
        lo = bars[i].low
        if all(lo <= b.low for b in window) and sum(1 for b in window if b.low == lo) == 1:
            out.append(i)
    return out


def support_levels(
    bars: list[Bar], *, wing: int = 2, tol_pct: float = 1.5, max_levels: int = 4
) -> list[SupportLevel]:
    """Cluster swing lows within ``tol_pct`` of each other into support levels.

    Returns up to ``max_levels`` levels sorted nearest-first by distance below/around the last
    close. Each level carries its touch count (cluster size) and the most recent touch date —
    more touches + more recent = a better-evidenced level. Distance is signed: negative means
    the level sits below the last close (a resting-buy candidate zone).
    """
    if not bars:
        return []
    idxs = swing_lows(bars, wing=wing)
    if not idxs:
        return []
    last_close = bars[-1].c

    # greedy proximity clustering over price-sorted swing lows
    points = sorted((bars[i].low, bars[i].t) for i in idxs)
    clusters: list[list[tuple[float, str]]] = [[points[0]]]
    for price, when in points[1:]:
        anchor = median(p for p, _ in clusters[-1])
        if abs(price - anchor) / anchor * 100.0 <= tol_pct:
            clusters[-1].append((price, when))
        else:
            clusters.append([(price, when)])

    levels = [
        SupportLevel(
            level=round(median(p for p, _ in c), 2),
            touches=len(c),
            last_touch=max(w for _, w in c)[:10],
            distance_pct=round((median(p for p, _ in c) - last_close) / last_close * 100.0, 2),
        )
        for c in clusters
    ]
    levels.sort(key=lambda lv: abs(lv.distance_pct))
    return levels[:max_levels]
