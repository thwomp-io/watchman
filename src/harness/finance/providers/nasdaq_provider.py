"""Nasdaq analyst-API provider — CONFIRMED earnings dates + consensus price targets, keyless.

Why this exists (two wire-integrity gaps, one source):
- **Earnings dates**: the EDGAR filing-cadence estimator (`estimate_next_print`) is honest but
  approximate — it has produced month-off estimates when a filer's cadence shifted (a real
  print-date error propagated in reports for days before an IR page corrected it). Nasdaq's
  analyst API carries the Zacks-fed announcement date and — critically — SAYS when the date is
  algorithm-derived vs announced, so the consumer can label `confirmed` vs `est.` honestly.
- **Price-target consensus**: rating/PT changes on held names otherwise surface only if a generic
  news feed happens to carry them; a deterministic consensus snapshot diffed run-over-run turns
  "an analyst moved the target" into a first-class wire item instead of luck.

Endpoints (probe-validated; unofficial, can change without notice):
  GET https://api.nasdaq.com/api/analyst/{SYM}/earnings-date
  GET https://api.nasdaq.com/api/analyst/{SYM}/targetprice

Landmines (read before extending — the module-docstring convention):
- **Browser UA required.** api.nasdaq.com stalls/blocks tool UAs (`harness/<ver>` hangs until
  timeout); a generic Mozilla UA responds instantly. Endpoint-required, not a PII choice — the
  same posture as `ratings_provider._BROWSER_UA` (zero PII either way).
- **The Python client is refused; curl is not** (verified 2026-11-10). Every request from httpx
  answers 403 while curl with the same two headers answers 200, over HTTP/2 and HTTP/1.1 alike, so
  the block keys on the client's TLS fingerprint. Requests therefore go through the system `curl`
  when there is one. Where there isn't (the container image), httpx is tried and the 403 surfaces
  as a transient miss: dates fall back to the filing-cadence estimate, labeled as such.
- **`data` comes back null** for ETFs/funds/unknown symbols with status 200 — treat null data as
  an honest miss (ProviderError), never a parse crash or a fabricated date.
- **The confirmed/estimated distinction lives in prose.** `data.reportText` contains the phrase
  "derived from an algorithm" for Zacks-estimated dates; announced dates say "is expected* to
  report". Parse the date from `data.announcement` ("Earnings announcement* for JPM: Jul 14,
  2026") — it's the cleanest dated field either way.
- **Zacks consensus is not the company's IR page.** Even a "confirmed" date can shift; the label
  is `confirmed · nasdaq`, and consumers should treat it as strong-but-not-gospel. Runner-up
  source evaluated: Yahoo quoteSummary `calendarEvents` — rejected because query1/query2
  wholesale-429 this egress even with the cookie+crumb dance (the ratings
  provider's handshake worked earlier but the calendar module was throttled to uselessness).
- **Volume discipline is the CALLER's job**: serial fetches + a day-scale TTL cache (see
  `watch_state.EarningsDateCache` / `watch_state.ConsensusState`). Many pulse runs per market day across a
  multi-name book
  must NOT mean holdings × runs hits on an unofficial API — the cache bounds it to ~one sweep/day.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime
from typing import Any

import httpx

from harness._http import describe_http_error, get_with_retry
from harness.errors import ProviderError
from harness.finance.models import ConsensusPT, EarningsDate
from harness.finance.scan import paced
from harness.paths import which_tool

_EARNINGS_URL = "https://api.nasdaq.com/api/analyst/{symbol}/earnings-date"
_TARGET_URL = "https://api.nasdaq.com/api/analyst/{symbol}/targetprice"

# Endpoint-required browser UA (see landmines above) — generic, carries no PII.
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # noqa: E501
_HDRS = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}

_TIMEOUT_S = 20
# The symbol becomes part of a URL handed to curl: letters, digits, dot and dash only.
_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,9}")

# "Earnings announcement* for JPM: Jul 14, 2026" → the dated tail. Month names are Nasdaq's
# abbreviated-English format; strptime handles them without a locale dependency.
_ANNOUNCE_RE = re.compile(r":\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*$")
_ALGO_PHRASE = "derived from an algorithm"


def get_json(target: str) -> Any:
    """GET an api.nasdaq.com URL and parse the body, through the system curl when there is one.
    Every failure here is transient: it says nothing about what was asked for, so a caller that
    caches misses must not cache it."""
    curl = which_tool("curl")
    try:
        text = _curl(curl, target) if curl else _httpx(target)
        return json.loads(text)
    except ValueError as e:
        raise ProviderError("nasdaq: the response wasn't JSON", transient=True) from e


def _curl(curl: str, target: str) -> str:
    """Fetch through the system curl (see the landmines above: the Python client is refused)."""
    args = [curl, "--silent", "--show-error", "--proto", "=https", "--max-time", str(_TIMEOUT_S)]
    for name, value in _HDRS.items():
        args += ["--header", f"{name}: {value}"]
    # The status rides on a last line of its own, after the body.
    args += ["--write-out", "\n%{http_code}", "--", target]
    with paced(target):
        try:
            out = subprocess.run(  # noqa: S603 - a fixed program, a URL built from a checked symbol
                args, capture_output=True, text=True, timeout=_TIMEOUT_S + 5, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError("nasdaq: no answer in time", transient=True) from e
    if out.returncode != 0:
        reason = out.stderr.strip().splitlines()[-1:] or [f"curl exited {out.returncode}"]
        raise ProviderError(f"nasdaq: {reason[0]}", transient=True)
    body, _, status = out.stdout.rpartition("\n")
    if status != "200":
        raise ProviderError(f"nasdaq: HTTP {status}", transient=True)
    return body


def _httpx(target: str) -> str:
    """The fallback where there is no curl (the container image). Expect a 403 from here."""
    try:
        with paced(target):
            return get_with_retry(target, headers=_HDRS, timeout=_TIMEOUT_S).text
    except httpx.HTTPError as e:
        raise ProviderError(f"nasdaq: {describe_http_error(e)}", transient=True) from e


def _data(url: str, symbol: str) -> dict[str, Any]:
    """Fetch one symbol's document and unwrap Nasdaq's {data, status} envelope."""
    symbol = symbol.upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ProviderError(f"nasdaq: {symbol!r} isn't a ticker symbol")
    try:
        body = get_json(url.format(symbol=symbol))
    except ProviderError as e:
        raise ProviderError(f"{e} for {symbol}", transient=e.transient) from e
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        # ETFs/funds/unknown symbols return status-200 + null data — an honest miss, not a crash.
        raise ProviderError(f"nasdaq: no analyst data for {symbol}")
    return data


def fetch_earnings_date(symbol: str) -> EarningsDate:
    """Next earnings date for one symbol, labeled confirmed vs algorithm-estimated."""
    data = _data(_EARNINGS_URL, symbol)
    announcement = str(data.get("announcement") or "")
    m = _ANNOUNCE_RE.search(announcement)
    if not m:
        raise ProviderError(f"nasdaq: no dated announcement for {symbol}")
    try:
        when: date = datetime.strptime(m.group(1), "%b %d, %Y").date()
    except ValueError as e:
        raise ProviderError(f"nasdaq: unparseable date {m.group(1)!r} for {symbol}") from e
    report_text = str(data.get("reportText") or "")
    return EarningsDate(
        symbol=symbol.upper(),
        report_date=when,
        confirmed=_ALGO_PHRASE not in report_text,
    )


def fetch_price_target(symbol: str) -> ConsensusPT:
    """Sell-side consensus PT + rating mix for one symbol (the diffable snapshot)."""
    data = _data(_TARGET_URL, symbol)
    overview = data.get("consensusOverview")
    if not isinstance(overview, dict) or overview.get("priceTarget") is None:
        raise ProviderError(f"nasdaq: no consensus overview for {symbol}")

    def _num(key: str) -> float | None:
        v = overview.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _count(key: str) -> int:
        v = overview.get(key)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    mean = _num("priceTarget")
    if mean is None:  # pragma: no cover - guarded above; belt for shape drift
        raise ProviderError(f"nasdaq: no mean price target for {symbol}")
    return ConsensusPT(
        symbol=symbol.upper(),
        mean=mean,
        low=_num("lowPriceTarget"),
        high=_num("highPriceTarget"),
        buy=_count("buy"),
        hold=_count("hold"),
        sell=_count("sell"),
    )
