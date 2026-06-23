"""Keyless analyst-ratings provider — Yahoo Finance `quoteSummary` `financialData` module.

The sell-side consensus a broker console shows (mean/high/low price target + recommendation +
analyst count). Yahoo gates this endpoint behind a **cookie + crumb** handshake (the same dance
yfinance does), so the fetch is three calls sharing one `httpx.Client` cookie jar:

  1. GET fc.yahoo.com  → sets the consent/session cookie (returns 404, that's fine — cookie still set)
  2. GET /v1/test/getcrumb (with the cookie) → the crumb token
  3. GET /v10/finance/quoteSummary/{sym}?modules=financialData&crumb=… (cookie + crumb)

Probe-validated. **Unofficial endpoint** — it can rate-limit, change,
or EU-consent-wall; failures raise ProviderError (honest miss), never a false-empty. The data is
INFORMATION, not a verdict — sell-side bias caveat lives in the CLI/MCP presentation layer.
"""

from __future__ import annotations

from typing import Any

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import AnalystRatings

_COOKIE_URL = "https://fc.yahoo.com"
_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"

# Yahoo's consumer query1 endpoints (getcrumb / quoteSummary) throttle the non-browser harness/<ver>
# tool-UA (HTTP 429); they expect a browser UA. This is endpoint-required, not a PII choice — a
# generic Mozilla UA carries zero PII, consistent with the good-API-citizen posture (cf. _http.py).
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # noqa: E501
_HDRS = {"User-Agent": _BROWSER_UA}


def _raw(node: dict[str, Any] | None) -> float | None:
    """Yahoo wraps numbers as {raw, fmt}; pull the raw float (None-safe)."""
    if isinstance(node, dict) and node.get("raw") is not None:
        try:
            return float(node["raw"])
        except (TypeError, ValueError):
            return None
    return None


def fetch_yahoo_ratings(symbol: str) -> AnalystRatings:
    """Analyst price-target consensus + recommendation for `symbol` (keyless, cookie+crumb)."""
    sym = symbol.upper()
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # 1. seed the cookie jar (fc.yahoo.com 404s but still sets the cookie)
        get_with_retry(_COOKIE_URL, client=client, headers=_HDRS, allow_status={200, 404})
        # 2. crumb (bound to that cookie)
        crumb_resp = get_with_retry(_CRUMB_URL, client=client, headers=_HDRS)
        crumb = crumb_resp.text.strip()
        if not crumb or "<" in crumb:  # an HTML/consent page, not a crumb token
            raise ProviderError(f"yahoo ratings {sym}: no crumb (consent wall / endpoint changed)")
        # 3. the gated financialData module (crumb via params → httpx URL-encodes it)
        resp = get_with_retry(
            _SUMMARY_URL.format(symbol=sym), client=client, headers=_HDRS,
            params={"modules": "financialData", "crumb": crumb},
        )

    if resp.status_code != 200:
        raise ProviderError(f"yahoo ratings {sym}: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise ProviderError(f"yahoo ratings {sym}: bad JSON") from e

    result = (data.get("quoteSummary") or {}).get("result")
    if not result:
        err = (data.get("quoteSummary") or {}).get("error") or data.get("finance", {}).get("error")
        raise ProviderError(f"yahoo ratings {sym}: no financialData ({err or 'empty result'})")
    fd = result[0].get("financialData") or {}

    current = _raw(fd.get("currentPrice"))
    mean = _raw(fd.get("targetMeanPrice"))
    upside = round((mean - current) / current * 100.0, 2) if (mean and current) else None
    n_node = fd.get("numberOfAnalystOpinions") or {}
    n = int(n_node["raw"]) if n_node.get("raw") is not None else None

    rec = str(fd.get("recommendationKey") or "").strip()
    if not mean and not rec:
        raise ProviderError(f"yahoo ratings {sym}: no analyst coverage (fund/ETF or untracked)")

    return AnalystRatings(
        symbol=sym,
        current_price=current,
        target_mean=mean,
        target_high=_raw(fd.get("targetHighPrice")),
        target_low=_raw(fd.get("targetLowPrice")),
        target_median=_raw(fd.get("targetMedianPrice")),
        recommendation_key=rec,
        recommendation_mean=_raw(fd.get("recommendationMean")),
        num_analysts=n,
        upside_pct=upside,
        source="yahoo",
    )
