"""FMP (Financial Modeling Prep) provider — clean PRE-COMPUTED TTM valuation ratios.

Why this exists: the keyless EDGAR path (`compute_multiples`) assembles TTM from raw XBRL facts, and
the Q4-only-in-10-K filer cadence makes that assembly mis-tag TTM revenue/earnings for a LARGE share
of names (the TTM-assembly trap — a mega-cap's P/S can come back many multiples too high when Q4 is
only in the 10-K). FMP pre-computes the TTM
ratios on its side,
sidestepping the whole assembly problem, and covers foreign filers / ADRs (TSM, ASML) that aren't in
SEC's ticker→CIK map at all.

This is the TRUSTED-PRECOMPUTED path; the keyless EDGAR path stays as the auditable-from-facts
fallback + the `fundamentals` raw-financials surface. Two calls per symbol (ratios-ttm + quote),
free tier 250 req/day. Read-only; honest misses raise ProviderError (never a false-empty or a
fabricated number — same honesty contract as the EDGAR path).

Field names confirmed live against FMP `/stable/` (NVDA); fallback keys cover the
known cross-version drift in FMP's naming.
"""

from __future__ import annotations

from typing import Any

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import Multiples, MultiplesComponent

_RATIOS_URL = "https://financialmodelingprep.com/stable/ratios-ttm"
_QUOTE_URL = "https://financialmodelingprep.com/stable/quote"

# first present key wins (FMP has drifted field names across API versions; primary = the /stable/
# name confirmed live, then legacy/alt names).
_PE_KEYS = ("priceToEarningsRatioTTM", "peRatioTTM")
_PS_KEYS = ("priceToSalesRatioTTM", "priceToSalesRatiosTTM")
_EVEBITDA_KEYS = ("enterpriseValueMultipleTTM", "evToEBITDATTM", "enterpriseValueOverEBITDATTM")
_PEG_KEYS = ("priceToEarningsGrowthRatioTTM", "pegRatioTTM")
_EV_KEYS = ("enterpriseValueTTM",)


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _ratio(v: Any) -> float | str | None:
    """FMP gives the ratio directly; apply the SAME honesty rules as the EDGAR path so the
    screened-core's honesty-string contract holds across both sources:
    - missing → "unavailable"
    - ≤ 0 (negative earnings/EBITDA, or a net-cash negative-EV artifact) → "N/M" (a negative
      ratio would mislead)
    - else → the rounded float.
    """
    if v is None:
        return "unavailable"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "unavailable"
    if f <= 0:
        return "N/M"
    return round(f, 2)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _fetch(url: str, symbol: str, api_key: str) -> dict[str, Any]:
    """GET one FMP /stable/ endpoint → the single result object. Honest misses raise ProviderError."""
    try:
        resp = get_with_retry(
            url, params={"symbol": symbol, "apikey": api_key}, allow_status={200, 401, 403}
        )
    except httpx.HTTPError as e:  # network / exhausted-retry
        raise ProviderError(f"FMP {symbol}: request failed ({e})") from e
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"FMP {symbol}: auth failed (HTTP {resp.status_code}) — check FMP_API_KEY"
        )
    if resp.status_code != 200:
        raise ProviderError(f"FMP {symbol}: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise ProviderError(f"FMP {symbol}: bad JSON") from e
    # FMP signals errors as {"Error Message": "..."}; no-coverage as an empty list.
    if isinstance(data, dict) and (msg := data.get("Error Message")):
        raise ProviderError(f"FMP {symbol}: {msg}")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ProviderError(
            f"FMP {symbol}: no data (unknown ticker, no coverage, or the 250/day free-tier limit hit)"
        )
    return data[0]


def fetch_fmp_multiples(symbol: str, api_key: str) -> Multiples:
    """Clean PRE-COMPUTED TTM multiples for `symbol` from FMP (ratios-ttm + quote). Read-only.

    Returns a `Multiples` with `source="fmp"`, the valuation quad (pe/ps/ev_ebitda/peg), live
    price + market cap + enterprise value, and a note that the figures are vendor-computed (not
    component-audited like the EDGAR path). Honest misses raise ProviderError.
    """
    sym = symbol.upper()
    if not api_key:
        raise ProviderError("FMP: no API key (set FMP_API_KEY in ./.env)")

    ratios = _fetch(_RATIOS_URL, sym, api_key)
    quote = _fetch(_QUOTE_URL, sym, api_key)

    m = Multiples(
        symbol=sym,
        entity_name=str(quote.get("name") or ""),
        source="fmp",
        price=_num(quote.get("price")),
        price_as_of="live (FMP)",
        market_cap=_num(quote.get("marketCap")),
        enterprise_value=_num(_first(ratios, _EV_KEYS)),
        pe=_ratio(_first(ratios, _PE_KEYS)),
        ps=_ratio(_first(ratios, _PS_KEYS)),
        ev_ebitda=_ratio(_first(ratios, _EVEBITDA_KEYS)),
        peg=_ratio(_first(ratios, _PEG_KEYS)),
        notes=[
            "Multiples = FMP (Financial Modeling Prep) PRE-COMPUTED TTM ratios + live FMP quote. FMP "
            "assembles TTM on its side, sidestepping the Q4-in-10-K XBRL-assembly trap "
            "— so these are vendor-computed, NOT component-audited like the keyless "
            "EDGAR path (--source edgar for that). PEG is included. Read-only; reported figures lag "
            "(TTM through the latest reported quarter), only the price is live.",
        ],
    )
    m.components = [
        MultiplesComponent(label="Live price", value=m.price, period="live", note="FMP quote"),
        MultiplesComponent(label="Market cap", value=m.market_cap, period="live", note="FMP quote"),
        MultiplesComponent(
            label="Enterprise value", value=m.enterprise_value, period="live", note="FMP ratios-ttm"
        ),
    ]
    return m
