"""Scenario-grid projections — the deterministic data layer behind the Watchman PROJECTIONS tab.

The params registry (`finance/reference/projection-params.yaml`, tracker-resident; pack-overridable)
is a THIN machine projection authored by the operating agent at research/grade time — one entry per
name, carrying the scenario inputs (forward EPS + a growth band + a multiple band) and their
provenance. The rich reasoning stays in the research artifact the entry points at; this module only
loads, validates, and runs the arithmetic (price = EPS × multiple, compounded per horizon). No
synthesis, no model in the loop (determinism doctrine — the same pattern as the print scorecards).

Honesty is structural, not decorative:
- every grid corner is a SCENARIO, never a forecast — the contract labels horizons ≤1y "estimate"
  and >1y "sketch" (bands compound wider the further out you look);
- params carry `as_of` + `provenance`; entries older than ~a quarter flag `stale` so the tab can
  dim them rather than render confident numbers off expired inputs;
- prices prefer the live quote and degrade to the registry's `ref_price` with the source named
  (`price_source`), never silently.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Horizons: (label, years). ≤1y renders as "estimate"; beyond that the compounding makes any
# point-band a sketch — the tier is part of the contract so every surface inherits the honesty.
HORIZONS: tuple[tuple[str, float], ...] = (
    ("6mo", 0.5),
    ("12mo", 1.0),
    ("24mo", 2.0),
    ("36mo", 3.0),
)
SKETCH_BEYOND_YEARS = 1.0
STALE_AFTER_DAYS = 100  # ~a quarter — params should be re-anchored at each print cycle


class ProjectionError(ValueError):
    """A malformed registry — raised with the offending entry named (fail loud, never guess)."""


@dataclass(frozen=True)
class ProjectionParams:
    """One name's scenario inputs, as authored by the agent at research time."""

    symbol: str
    fwd_eps: float  # next-12-months EPS estimate (basis stated in eps_basis)
    eps_basis: str  # e.g. "non-GAAP diluted, Q2A+Q3G annualized (latest 8-K)"
    growth_pct_low: float  # annual EPS growth band, in percent (e.g. 15 = 15%/yr)
    growth_pct_high: float
    mult_low: float  # forward-multiple band the market has recently paid / could pay
    mult_mid: float
    mult_high: float
    ref_price: float  # price when params were authored — the offline fallback + drift anchor
    as_of: _dt.date
    provenance: str  # vault path of the research artifact the numbers derive from
    notes: str | None = None
    # Values-screen status line, when the screen affects this name (screened-out / undecided /
    # look-through-only). Screened-out names stay visible on the tab: the item
    # renders MARKED, never hidden — the screen gates what gets BOUGHT, not what gets seen.
    screen_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fwd_eps": self.fwd_eps,
            "eps_basis": self.eps_basis,
            "growth_pct_low": self.growth_pct_low,
            "growth_pct_high": self.growth_pct_high,
            "mult_low": self.mult_low,
            "mult_mid": self.mult_mid,
            "mult_high": self.mult_high,
            "ref_price": self.ref_price,
            "as_of": self.as_of.isoformat(),
            "provenance": self.provenance,
            "notes": self.notes,
            "screen_note": self.screen_note,
        }


def _parse_entry(raw: dict[str, Any], idx: int) -> ProjectionParams:
    def need(key: str) -> Any:
        if key not in raw or raw[key] in (None, ""):
            raise ProjectionError(f"projections[{idx}]: missing required field '{key}'")
        return raw[key]

    def num(key: str) -> float:
        v = need(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ProjectionError(
                f"projections[{idx}] ({raw.get('symbol', '?')}): '{key}' must be numeric"
            )
        return float(v)

    raw_date = need("as_of")
    if isinstance(raw_date, _dt.date):
        as_of = raw_date
    else:
        try:
            as_of = _dt.date.fromisoformat(str(raw_date))
        except ValueError as e:
            raise ProjectionError(
                f"projections[{idx}] ({raw.get('symbol', '?')}): bad as_of '{raw_date}'"
            ) from e
    growth_low, growth_high = num("growth_pct_low"), num("growth_pct_high")
    if growth_low > growth_high:
        raise ProjectionError(
            f"projections[{idx}] ({raw.get('symbol', '?')}): growth band inverted "
            f"({growth_low} > {growth_high})"
        )
    mult_low, mult_mid, mult_high = num("mult_low"), num("mult_mid"), num("mult_high")
    if not (mult_low <= mult_mid <= mult_high):
        raise ProjectionError(
            f"projections[{idx}] ({raw.get('symbol', '?')}): multiple band must be "
            f"low ≤ mid ≤ high (got {mult_low}/{mult_mid}/{mult_high})"
        )
    return ProjectionParams(
        symbol=str(need("symbol")).upper(),
        fwd_eps=num("fwd_eps"),
        eps_basis=str(need("eps_basis")),
        growth_pct_low=growth_low,
        growth_pct_high=growth_high,
        mult_low=mult_low,
        mult_mid=mult_mid,
        mult_high=mult_high,
        ref_price=num("ref_price"),
        as_of=as_of,
        provenance=str(need("provenance")),
        notes=str(raw["notes"]) if raw.get("notes") else None,
        screen_note=str(raw["screen_note"]) if raw.get("screen_note") else None,
    )


def load_params(path: Path) -> list[ProjectionParams]:
    """Load + validate the registry, alphabetical by symbol (the tab re-sorts as it likes)."""
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("projections")
    if entries is None:
        raise ProjectionError("registry has no top-level 'projections:' list")
    if not isinstance(entries, list):
        raise ProjectionError("'projections:' must be a list")
    params = [_parse_entry(e, i) for i, e in enumerate(entries)]
    params.sort(key=lambda p: p.symbol)
    return params


def grid_for(p: ProjectionParams, price: float) -> list[dict[str, Any]]:
    """Public seam for sibling contracts (the holdings appraisal) — same arithmetic, one home."""
    return _grid_for(p, price)


def _grid_for(
    p: ProjectionParams, price: float
) -> list[dict[str, Any]]:
    """The arithmetic core: per horizon, the market prices the then-forward EPS
    (fwd_eps compounded by the growth band) at each multiple corner. Corners pair
    conservatively — low growth × low multiple through high × high — so the band edges are
    genuine worst/best of the stated scenario space, with mid × mid as the center read."""
    grid: list[dict[str, Any]] = []
    g_mid = (p.growth_pct_low + p.growth_pct_high) / 2
    for label, years in HORIZONS:
        corners = {
            "low": p.fwd_eps * (1 + p.growth_pct_low / 100) ** years * p.mult_low,
            "mid": p.fwd_eps * (1 + g_mid / 100) ** years * p.mult_mid,
            "high": p.fwd_eps * (1 + p.growth_pct_high / 100) ** years * p.mult_high,
        }
        grid.append(
            {
                "horizon": label,
                "years": years,
                "tier": "estimate" if years <= SKETCH_BEYOND_YEARS else "sketch",
                "price": {k: round(v, 2) for k, v in corners.items()},
                "return_pct": {
                    k: round((v / price - 1) * 100, 1) for k, v in corners.items()
                },
            }
        )
    return grid


# Rarity ladder — tiles classify by the 12mo MID-corner return into loot-style tiers, so the
# book can be scanned ("hunted") by item
# quality at a glance. Anchors: ~4-8% conservative ballast = rare/blue · 10-22% = epic ·
# 22%+ = legendary; the gaps close at the tier floors below. Classification is ENGINE-side so
# every surface agrees on what's legendary — never recomputed in a UI.
RARITY_LADDER: tuple[tuple[float, str], ...] = (
    (22.0, "legendary"),
    (10.0, "epic"),
    (4.0, "rare"),
    (0.0, "common"),
)


def classify_rarity(mid_12mo_return_pct: float) -> str:
    for floor, name in RARITY_LADDER:
        if mid_12mo_return_pct >= floor:
            return name
    return "poor"


def build_contract(
    params: list[ProjectionParams],
    quotes: dict[str, float],
    holdings: dict[str, float],
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """The dashboard/JSON contract. `quotes` maps symbol → live price (absent = degrade to
    ref_price, named); `holdings` maps symbol → avg cost for held names (from-basis returns).
    Pure function — the service layer supplies the lookups, tests supply fixtures."""
    today = today or _dt.date.today()
    out: list[dict[str, Any]] = []
    for p in params:
        live = quotes.get(p.symbol)
        price = live if live is not None else p.ref_price
        stale = (today - p.as_of).days > STALE_AFTER_DAYS
        grid = _grid_for(p, price)
        mid_12mo = next(r["return_pct"]["mid"] for r in grid if r["horizon"] == "12mo")
        entry: dict[str, Any] = {
            **p.to_dict(),
            "price": round(price, 2),
            "price_source": "live" if live is not None else "ref",
            "fwd_multiple_now": round(price / p.fwd_eps, 1) if p.fwd_eps else None,
            "stale": stale,
            "held": p.symbol in holdings,
            # The loot-tier read: item_level = the 12mo mid return (rounded), rarity from the
            # ladder above. A scannable convention, not a verdict — the disclaimer governs.
            "item_level": round(mid_12mo),
            "rarity": classify_rarity(mid_12mo),
            "grid": grid,
        }
        if p.symbol in holdings and holdings[p.symbol] > 0:
            basis = holdings[p.symbol]
            entry["basis"] = round(basis, 2)
            entry["basis_return_pct"] = round((price / basis - 1) * 100, 1)
        out.append(entry)
    return {
        "projections": out,
        "summary": {
            "total": len(out),
            "held": sum(1 for e in out if e["held"]),
            "stale": sum(1 for e in out if e["stale"]),
            "by_rarity": {
                r: n
                for r in ("legendary", "epic", "rare", "common", "poor")
                if (n := sum(1 for e in out if e["rarity"] == r))
            },
        },
        "disclaimer": (
            "Scenario grids from agent-authored params (growth × multiple bands) — "
            "surfaces to reason against, never forecasts; horizons beyond 1y are sketch-tier."
        ),
    }
