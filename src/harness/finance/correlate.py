"""Return-correlation analysis (`hn finance correlate`) — the "is this name actually a diversifier?"
hard-data surface.

PURE function over aligned closes: daily simple returns → pairwise Pearson correlation matrix +
per-symbol annualized vol; optionally a designated equal-weight FACTOR (e.g. an AI-names basket) → each
symbol's correlation + beta to it + the top days the FOCAL name (the first symbol) DIVERGED from the
factor (what moved independently).

Determinism doctrine (same as `market.build_overview` / `compare.build_compare`): the math lives here
and is unit-tested over fixtures; the INTERPRETATION ("this decorrelates the rest of the book") is the agent's
narrative, written into the research, never computed. No network here — the service gathers the bars.
"""

from __future__ import annotations

import math

from harness.finance.models import CorrelationReport, DivergenceDay

_TRADING_DAYS = 252


def _returns(closes: list[float]) -> list[float]:
    """Daily simple returns from a price series."""
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], mx: float) -> float:
    return math.sqrt(sum((x - mx) ** 2 for x in xs) / len(xs)) if xs else 0.0


def _cov(xs: list[float], ys: list[float], mx: float, my: float) -> float:
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / len(xs) if xs else 0.0


def _corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _std(xs, mx), _std(ys, my)
    if sx == 0 or sy == 0:
        return 0.0
    return _cov(xs, ys, mx, my) / (sx * sy)


def build_correlation(
    closes_by_sym: dict[str, dict[str, float]],
    *,
    factor: list[str] | None = None,
    top_divergence: int = 8,
) -> CorrelationReport:
    """Pure: aligned closes → correlation FACTS. `closes_by_sym` maps symbol → {date: close}.

    Aligns all symbols on their COMMON dates (so a stock with a shorter history doesn't silently skew
    the matrix), computes daily returns, then the pairwise Pearson matrix + annualized vol. With a
    `factor` (a list of symbols present in the data), builds an equal-weight factor return series and
    reports each symbol's corr + beta to it, plus the focal (first symbol) vs factor divergence days.
    """
    symbols = list(closes_by_sym.keys())
    if not symbols:
        return CorrelationReport(notes=["no symbols"])

    common = set.intersection(*(set(d.keys()) for d in closes_by_sym.values()))
    dates = sorted(common)
    if len(dates) < 3:
        return CorrelationReport(symbols=symbols, notes=["insufficient overlapping history"])

    rets = {s: _returns([closes_by_sym[s][d] for d in dates]) for s in symbols}
    ret_dates = dates[1:]
    n = min(len(r) for r in rets.values())
    # guard against ragged series (shouldn't happen post-alignment, but stay safe)
    rets = {s: r[:n] for s, r in rets.items()}
    ret_dates = ret_dates[:n]

    matrix = [[round(_corr(rets[a], rets[b]), 3) for b in symbols] for a in symbols]
    vol = {s: round(_std(rets[s], _mean(rets[s])) * math.sqrt(_TRADING_DAYS) * 100, 1) for s in symbols}

    rep = CorrelationReport(
        symbols=symbols,
        n_obs=n,
        start=dates[0],
        end=dates[-1],
        matrix=matrix,
        vol_annual=vol,
    )

    fac = [s for s in (factor or []) if s in symbols]
    if fac:
        factor_ret = [_mean([rets[s][i] for s in fac]) for i in range(n)]
        mf = _mean(factor_ret)
        vf = _std(factor_ret, mf) ** 2
        rep.factor = fac
        rep.factor_corr = {s: round(_corr(rets[s], factor_ret), 3) for s in symbols}
        rep.factor_beta = {
            s: round(_cov(rets[s], factor_ret, _mean(rets[s]), mf) / vf, 2) if vf else 0.0
            for s in symbols
        }
        focal = symbols[0]
        top_idx = sorted(range(n), key=lambda i: abs(rets[focal][i] - factor_ret[i]), reverse=True)[
            :top_divergence
        ]
        rep.divergence_days = [
            DivergenceDay(
                date=ret_dates[i],
                focal=focal,
                focal_ret_pct=round(rets[focal][i] * 100, 2),
                factor_ret_pct=round(factor_ret[i] * 100, 2),
                gap_pct=round((rets[focal][i] - factor_ret[i]) * 100, 2),
                members={s: round(rets[s][i] * 100, 2) for s in fac},
            )
            for i in sorted(top_idx, key=lambda i: ret_dates[i], reverse=True)
        ]
    return rep
