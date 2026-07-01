"""The bird's-eye market basket + overview assembly (hn finance market).

A point-in-time market-regime read — index breadth, the 11 SPDR sectors, semis, and Mag7 dispersion;
the deterministic data side of the watchman's MARKET dashboard. The basket here is MARKET STRUCTURE
(a standard SPDR sector map), reference data baked into the tool — distinct from
the user's personal holdings/watchlist (user-state, in portfolio.yaml; surfaced by `watch`). `build_overview`
is a PURE function over a list of Quotes (no network), so it's unit-testable with fixtures and the
gather→compute stays model-free (determinism doctrine).
"""

from __future__ import annotations

from harness.finance.models import (
    MarketBreadth,
    MarketMover,
    MarketOverview,
    MarketQuote,
    Quote,
)

# Market structure — (symbol, display label). Fixed reference facts, not user preferences.
INDICES: list[tuple[str, str]] = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("DIA", "Dow 30"),
    ("IWM", "Russell 2000"),
    ("RSP", "S&P 500 (equal-wt)"),
]
SECTORS: list[tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLC", "Comm services"),
    ("XLY", "Consumer disc."),
    ("XLP", "Consumer staples"),
    ("XLE", "Energy"),
    ("XLF", "Financials"),
    ("XLV", "Health care"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLRE", "Real estate"),
    ("XLU", "Utilities"),
]
SEMIS: list[tuple[str, str]] = [
    ("SMH", "Semis (VanEck)"),
    ("SOXX", "Semis (iShares)"),
]
MEGACAP: list[tuple[str, str]] = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("NVDA", "Nvidia"),
    ("META", "Meta"),
    ("TSLA", "Tesla"),
]

_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("indices", INDICES),
    ("sectors", SECTORS),
    ("semis", SEMIS),
    ("megacap", MEGACAP),
]


def all_symbols() -> list[str]:
    """Every basket symbol, de-duplicated, order-preserving — one snapshots call covers them all."""
    seen: dict[str, None] = {}
    for _key, members in _GROUPS:
        for sym, _label in members:
            seen.setdefault(sym, None)
    return list(seen)


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def _to_market_quote(q: Quote | None, symbol: str, label: str, group: str) -> MarketQuote:
    if q is None:
        return MarketQuote(
            symbol=symbol, label=label, group=group, available=False, note="no quote returned"
        )
    return MarketQuote(
        symbol=symbol,
        label=label,
        group=group,
        available=q.available,
        price=q.price,
        prev_close=q.prev_close,
        day_change=q.day_change,
        day_change_pct=q.day_change_pct,
        note=q.note,
    )


def _pct(rows: list[MarketQuote], symbol: str) -> float | None:
    for r in rows:
        if r.symbol == symbol:
            return r.day_change_pct
    return None


def build_overview(quotes: list[Quote]) -> MarketOverview:
    """Assemble the grouped overview + deterministic breadth facts from a flat list of Quotes.

    Pure: no network, no model. Same input → same output (the determinism the maintainer wants to validate)."""
    by_sym = {q.symbol.upper(): q for q in quotes}
    grouped: dict[str, list[MarketQuote]] = {
        key: [_to_market_quote(by_sym.get(sym), sym, label, key) for sym, label in members]
        for key, members in _GROUPS
    }
    indices, sectors, semis, megacap = (
        grouped["indices"],
        grouped["sectors"],
        grouped["semis"],
        grouped["megacap"],
    )

    # --- breadth facts ---
    sectors_adv = sum(1 for r in sectors if r.day_change_pct is not None and r.day_change_pct > 0)
    sectors_dec = sum(1 for r in sectors if r.day_change_pct is not None and r.day_change_pct < 0)
    spy, rsp = _pct(indices, "SPY"), _pct(indices, "RSP")
    ew_minus_cap = round(rsp - spy, 2) if (spy is not None and rsp is not None) else None
    mc_pcts = [r.day_change_pct for r in megacap if r.day_change_pct is not None]
    semis_pcts = [r.day_change_pct for r in semis if r.day_change_pct is not None]
    breadth = MarketBreadth(
        sectors_advancing=sectors_adv,
        sectors_declining=sectors_dec,
        spy_pct=spy,
        rsp_pct=rsp,
        equal_weight_minus_cap_pct=ew_minus_cap,
        megacap_avg_pct=_mean(mc_pcts),
        megacap_spread_pct=round(max(mc_pcts) - min(mc_pcts), 2) if mc_pcts else None,
        semis_avg_pct=_mean(semis_pcts),
    )

    # --- movers: rotation universe (sectors + semis + mega-caps; not the broad indices) ---
    pool = [r for r in (sectors + semis + megacap) if r.day_change_pct is not None]
    ranked = sorted(pool, key=lambda r: r.day_change_pct or 0.0, reverse=True)

    def _mover(r: MarketQuote) -> MarketMover:
        return MarketMover(
            symbol=r.symbol, label=r.label, group=r.group, day_change_pct=r.day_change_pct
        )

    leaders = [_mover(r) for r in ranked[:5]]
    laggards = [_mover(r) for r in list(reversed(ranked))[:5]]

    as_of = next((q.as_of for q in quotes if q.available and q.as_of), None)
    unavailable = [
        r.symbol for rows in grouped.values() for r in rows if not r.available
    ]
    notes: list[str] = []
    if unavailable:
        notes.append("no feed for: " + ", ".join(unavailable))

    return MarketOverview(
        indices=indices,
        sectors=sectors,
        semis=semis,
        megacap=megacap,
        leaders=leaders,
        laggards=laggards,
        breadth=breadth,
        as_of=as_of,
        notes=notes,
    )
