"""Side-by-side comparison assembly (hn finance compare).

Composes the EXISTING surfaces — `multiples` (valuation triplet + market cap, EDGAR + live price),
`quote` (price + day move), `screen` (the values verdict) — into one side-by-side report for a
selected pick-set. `build_compare` is a PURE function over already-gathered inputs (no network), so
the gather→compute stays model-free + unit-testable with fixtures (the determinism doctrine, same as
`market.build_overview`).

The deterministic data feeds the Compare-tab table; the interpretive *comparison* (which name wins,
and why) is a separate agent-written doc-series the dashboard browses — never computed here.
"""

from __future__ import annotations

from harness.finance.models import (
    CompareReport,
    CompareRow,
    Multiples,
    Quote,
    ScreenResult,
)

# P/S above this on a profitable mega-cap is almost always the TTM mis-tag (Q4-only-in-10-K
# inflates the multiple — e.g. NVDA showed 455x vs the real ~24x). Flag, don't trust, pending the TTM-tag fix.
_PS_SANITY_CEILING = 60.0


def _row(
    symbol: str,
    quote: Quote | None,
    mult: Multiples | None,
    screen: ScreenResult | None,
    mult_error: str | None,
    research_dir: str,
) -> CompareRow:
    note_parts: list[str] = []
    row = CompareRow(symbol=symbol, research_dir=research_dir)

    if quote is not None and quote.available:
        row.price = quote.price
        row.day_change_pct = quote.day_change_pct
    elif quote is not None and quote.note:
        note_parts.append(quote.note)

    if mult is not None:
        row.entity_name = mult.entity_name
        row.market_cap = mult.market_cap
        row.ps, row.pe, row.ev_ebitda = mult.ps, mult.pe, mult.ev_ebitda
        # the price column prefers the live quote; fall back to the multiples' price if no quote feed
        if row.price is None:
            row.price = mult.price
        # sanity-guard: a profitable-looking P/S this high is a TTM mis-tag, not reality
        if isinstance(mult.ps, float) and mult.ps > _PS_SANITY_CEILING:
            note_parts.append(
                f"P/S {mult.ps:.0f}x looks inflated — verify (multiples Q4-in-10-K bug)"
            )
    elif mult_error:
        note_parts.append(mult_error)

    if screen is not None:
        row.screen = screen.status
        row.screen_category = screen.category

    row.note = " · ".join(note_parts)
    return row


def build_compare(
    symbols: list[str],
    quotes: dict[str, Quote],
    multiples: dict[str, Multiples],
    screens: dict[str, ScreenResult],
    mult_errors: dict[str, str] | None = None,
    research_dirs: dict[str, str] | None = None,
) -> CompareReport:
    """Assemble the side-by-side report from already-gathered per-symbol inputs. Pure: no network,
    no model, no filesystem — same inputs → same output (research_dirs are resolved at the I/O edge
    by the caller and passed in). Order follows the requested `symbols` list."""
    errs = mult_errors or {}
    dirs = research_dirs or {}
    rows = [
        _row(
            sym, quotes.get(sym), multiples.get(sym), screens.get(sym),
            errs.get(sym), dirs.get(sym, ""),
        )
        for sym in symbols
    ]
    as_of = next((q.as_of for q in quotes.values() if q.available and q.as_of), None)

    notes: list[str] = [
        "Multiples are EDGAR-reported (TTM) + a live price — reported figures LAG (newest 10-Q/10-K); "
        "only price/day move are live.",
    ]
    if any(r.note for r in rows):
        notes.append("See per-row notes for caveats (failed resolve / P/S sanity flag).")

    return CompareReport(rows=rows, as_of=as_of, notes=notes)
