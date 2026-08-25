"""Holdings appraisal — the deterministic data layer behind the Watchman HOLDINGS tab.

Where the projections contract is the SHOP (every name in the agent-authored params registry,
scannable by item level), this module is the INVENTORY: the user's actual held positions, each
appraised against the same registry. Two reads per appraised holding:

- ``item_level`` — the 12mo mid-corner return from the LIVE price (identical to the projections
  tab's number; classified once, engine-side, so every surface agrees on what's legendary);
- ``entry_item_level`` — the same arithmetic run from the holding's AVERAGE COST instead of the
  live price: "against the current model of the business, how good was my entry?" A basis far
  below the modeled 12mo mid = a strong buy still carrying modeled upside; a basis at/above the
  band ceiling = bought near a peak with thin modeled value left.

``entry_edge`` (entry_item_level − item_level) is the delta the entry itself captured — value
banked by buying well, as opposed to value still on the table.

Honesty is structural, not decorative:
- the appraisal uses CURRENT params against a PAST entry — a present-knowledge lens (params
  re-anchor at each print cycle), never a judgment of what was knowable at buy time; the
  disclaimer states this on every payload;
- holdings without a registry entry (fund structures, deliberate skips) render ``appraised:
  false`` with the reason named — never silently dropped, never given invented numbers;
- prices come from the caller's positions snapshot (live quote / last-known NAV / static), with
  the valuation basis carried through.

No synthesis, no model in the loop (determinism doctrine — same family as the print scorecards and
the projections registry this module joins against).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from harness.finance.projections import (
    STALE_AFTER_DAYS,
    ProjectionParams,
    classify_rarity,
    grid_for,
)


def build_holdings_contract(
    params: list[ProjectionParams],
    positions: list[dict[str, Any]],
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """The dashboard/JSON contract. ``positions`` rows carry the snapshot fields the service's
    positions verb already computes (symbol, name, shares, avg_cost, cost_basis, price,
    market_value, valuation); instrument rows only — cash sweeps / static account balances are
    the caller's to exclude. Pure function — tests supply fixtures."""
    today = today or _dt.date.today()
    by_symbol = {p.symbol: p for p in params}
    invested_total = sum(
        float(pos.get("market_value") or 0.0) for pos in positions
    )
    out: list[dict[str, Any]] = []
    for pos in positions:
        symbol = str(pos.get("symbol", "")).upper()
        shares = float(pos.get("shares") or 0.0)
        avg_cost = float(pos.get("avg_cost") or 0.0)
        cost_basis = float(pos.get("cost_basis") or 0.0)
        price = pos.get("price")
        value = float(pos.get("market_value") or 0.0)
        entry: dict[str, Any] = {
            "symbol": symbol,
            "name": pos.get("name") or symbol,
            "account": pos.get("account"),
            "valuation": pos.get("valuation"),
            "shares": shares,
            "avg_cost": round(avg_cost, 4) if avg_cost else None,
            "cost_basis": round(cost_basis, 2) if cost_basis else None,
            "price": round(float(price), 2) if price is not None else None,
            "value": round(value, 2),
            "weight_pct": round(value / invested_total * 100, 2) if invested_total else 0.0,
        }
        if cost_basis and value:
            entry["unrealized_gl"] = round(value - cost_basis, 2)
            entry["unrealized_gl_pct"] = round((value / cost_basis - 1) * 100, 1)
        p = by_symbol.get(symbol)
        if p is None or price is None or avg_cost <= 0:
            # Honest no-appraisal: no registry entry (fund structures, or names deliberately left
            # out of the registry), no price, or no usable basis. The row still renders.
            entry["appraised"] = False
            entry["unappraised_reason"] = (
                "no params-registry entry" if p is None
                else "no price available" if price is None
                else "no cost basis on record"
            )
            out.append(entry)
            continue
        live_price = float(price)
        grid = grid_for(p, live_price)
        mid_12mo = next(r["return_pct"]["mid"] for r in grid if r["horizon"] == "12mo")
        # The entry appraisal: identical arithmetic, basis as the denominator. mid_12mo_price
        # comes from the same grid row so both reads share one scenario space by construction.
        mid_12mo_price = next(r["price"]["mid"] for r in grid if r["horizon"] == "12mo")
        entry_return = (mid_12mo_price / avg_cost - 1) * 100
        entry.update(
            {
                "appraised": True,
                "item_level": round(mid_12mo),
                "rarity": classify_rarity(mid_12mo),
                "entry_item_level": round(entry_return),
                "entry_rarity": classify_rarity(entry_return),
                # item-level points the entry itself captured: modeled upside from basis minus modeled upside
                # from here. Positive = the buy captured value; ~0 = bought at today's read.
                "entry_edge": round(entry_return - mid_12mo),
                # What the entry paid on today's forward EPS vs what the market pays now — the
                # "bought at Nx forward" line of the appraisal.
                "paid_multiple": round(avg_cost / p.fwd_eps, 1) if p.fwd_eps else None,
                "fwd_multiple_now": round(live_price / p.fwd_eps, 1) if p.fwd_eps else None,
                "stale": (today - p.as_of).days > STALE_AFTER_DAYS,
                "screen_note": p.screen_note,
                "eps_basis": p.eps_basis,
                "as_of": p.as_of.isoformat(),
                "provenance": p.provenance,
                "grid": grid,
            }
        )
        out.append(entry)
    # Inventory order: biggest position first, not alphabetical.
    out.sort(key=lambda e: e["value"], reverse=True)
    appraised = [e for e in out if e["appraised"]]
    summary: dict[str, Any] = {
        "invested_total": round(invested_total, 2),
        "count": len(out),
        "appraised": len(appraised),
        "unrealized_gl_total": round(
            sum(e.get("unrealized_gl", 0.0) for e in out), 2
        ),
        "by_rarity": {
            r: n
            for r in ("legendary", "epic", "rare", "common", "poor")
            if (n := sum(1 for e in appraised if e["rarity"] == r))
        },
    }
    if appraised:
        best = max(appraised, key=lambda e: e["entry_item_level"])
        weakest = min(appraised, key=lambda e: e["entry_item_level"])
        summary["best_entry"] = {"symbol": best["symbol"], "entry_item_level": best["entry_item_level"]}
        summary["weakest_entry"] = {
            "symbol": weakest["symbol"],
            "entry_item_level": weakest["entry_item_level"],
        }
    return {
        "holdings": out,
        "summary": summary,
        "disclaimer": (
            "Entry appraisals run CURRENT scenario params against your basis — a present-"
            "knowledge lens (params re-anchor each print cycle), never a judgment of what was "
            "knowable at buy time. Scenarios to reason against, never forecasts or verdicts."
        ),
    }
