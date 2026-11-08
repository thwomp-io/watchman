"""The fund proxy's two judgment calls, pure: whether a thin OTC name's day move is real, and how much of
the fund each exposure fill stands in for.

A basket quotes the roster names it can see. Before fills, the rest of the fund was re-spread over those
names, so a selloff concentrated in the names it can't quote (a Taiwan supply-chain tail, onshore China)
was diluted by whatever it could. A fill gives each exposure its own line, weighted by the roster's
unquoted names in it plus any basket name that couldn't be priced this run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from harness.finance.corpus.reader import ProxyFill
from harness.finance.providers.global_provider import DailyHistory
from harness.yamlio import load_yaml


@dataclass
class OtcRead:
    price: float | None = None
    prev_close: float | None = None
    move_pct: float | None = None
    basis: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.move_pct is not None


def previous_session(histories: list[DailyHistory], session: date) -> date | None:
    """The last date before `session` on which any of these names traded. Read from the bars rather than a
    calendar, so the day after an exchange holiday finds the session before it."""
    seen = [d for h in histories for d, c, v in h.bars if d < session and c is not None and (v or 0) > 0]
    return max(seen) if seen else None


def otc_move(h: DailyHistory | None, session: date, prior: date | None) -> OtcRead:
    """A thin OTC name counts only when it traded on `session` and has a real close on the session before.
    A chart's previous close can be days old (one read +14% on a +1.75% day), and a name with no trade today
    carries yesterday's price: both would read as a move that didn't happen."""
    if h is None:
        return OtcRead(reason="no quote")
    if h.last_trade != session:
        last = h.last_trade.isoformat() if h.last_trade else "unknown"
        return OtcRead(reason=f"no trade on {session.isoformat()}; last {last}")
    if prior is None:
        return OtcRead(reason="no previous session found")
    prev = next((c for d, c, v in h.bars if d == prior and c is not None and (v or 0) > 0), None)
    if prev is None or prev <= 0:
        return OtcRead(reason=f"no trade on the previous session ({prior.isoformat()}), so no prior close")
    # The session's own bar first: the quote field can hold a late print the daily bar later settles away
    # from (a few cents off the close on a thin name), and two verbs reading the same day should agree.
    price = next((c for d, c, _ in h.bars if d == session and c is not None), None)
    if price is None:
        price = h.price
    if price is None:
        return OtcRead(reason="no price")
    return OtcRead(price=price, prev_close=prev, move_pct=(price / prev - 1.0) * 100.0,
                   basis=f"bars {prior.isoformat()} → {session.isoformat()}")


def read_roster(path: Path | None) -> list[dict[str, Any]]:
    """The roster's holdings rows, or [] when there's no file (fills then can't be weighed)."""
    if path is None or not path.exists():
        return []
    data = load_yaml(path.read_text()) or {}
    return [r for r in data.get("holdings", []) or [] if isinstance(r, dict)]


_TICKER = re.compile(r"[A-Z][A-Z0-9.]{0,5}")


def _listed_as(row: dict[str, Any], basket: set[str]) -> str | None:
    """The basket symbol a roster row is quoted through, from its `us` field ("ABCDF/ABCDY (OTC)")."""
    hits = [t for t in _TICKER.findall(str(row.get("us") or "")) if t in basket]
    return hits[0] if hits else None


def _exposure(row: dict[str, Any], fills: list[ProxyFill]) -> str | None:
    if row.get("exposure"):
        return str(row["exposure"]).upper()
    country = str(row.get("country") or "").upper()
    return next((f.exposure for f in fills if country in f.countries), None)


@dataclass
class FillPlan:
    weights: dict[str, float] = field(default_factory=dict)  # exposure -> % of the fund
    parts: dict[str, list[str]] = field(default_factory=dict)  # exposure -> what it's made of
    unfilled: float = 0.0  # unquoted weight no fill takes (another country, a private stake)


def plan_fills(roster: list[dict[str, Any]], basket_weights: dict[str, float], unavailable: set[str],
               fills: list[ProxyFill]) -> FillPlan:
    """Each exposure's weight: the roster names the basket doesn't quote, plus basket names unavailable this
    run, by the row's exposure (or its country's). Cash-sweep rows are the basket's flat cash line already."""
    plan = FillPlan()
    if not roster or not fills:
        return plan
    basket = set(basket_weights)
    unmapped: dict[str, float] = {}
    from_basket: dict[str, list[str]] = {}
    for row in roster:
        if str(row.get("sector") or "") == "cash-sweep":
            continue
        pct = float(row.get("pct") or 0.0)
        sym = _listed_as(row, basket)
        exp = _exposure(row, fills)
        if sym is not None and sym not in unavailable:
            continue
        if exp is None:
            plan.unfilled += pct if sym is None else 0.0
            continue
        if sym is None:
            unmapped[exp] = unmapped.get(exp, 0.0) + pct
        else:
            plan.weights[exp] = plan.weights.get(exp, 0.0) + basket_weights[sym]
            from_basket.setdefault(exp, []).append(sym)
    for f in fills:
        w = unmapped.get(f.exposure, 0.0)
        plan.weights[f.exposure] = plan.weights.get(f.exposure, 0.0) + w
        parts = [f"{w:.2f}% unquoted roster names"] if w else []
        for s in sorted(from_basket.get(f.exposure, [])):
            parts.append(f"{s} (unavailable, {basket_weights[s]:.2f}%)")
        plan.parts[f.exposure] = parts
    plan.unfilled = round(plan.unfilled, 2)
    return plan
