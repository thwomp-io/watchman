"""Global markets quotes — futures, world indices, energy, metals, FX (keyless Yahoo).

The follow-the-sun surface: what the US tape can't show after its close. Yahoo's crumb-free
`/v8/finance/chart/{symbol}` meta covers index futures (ES=F), world indices (^N225), commodities
(BZ=F), and FX (EURUSD=X) — one polite self-paced call per card, no cookie/crumb handshake at all
(the v7 batch quote endpoint needs the crumb, and its getcrumb gate burst-throttles shared IPs —
learned live on first build night). Rails mirror the pre-market/Asia/Europe/oil/metals tab grammar
of mainstream market sites, so an overnight read (is the futures market panicking? did Asia
confirm?) is one command instead of a browser tour. Per-card resilience: one dead symbol never
bricks a rail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_PACE_SECONDS = 0.15  # self-pacing between per-symbol calls (good-citizen posture: pace, don't burst)

# Browser UA is endpoint-required (query1 429s tool UAs; zero PII) — but DISTINCT from the
# ratings provider's string on purpose: Yahoo buckets its burst throttle per UA+IP, so sharing one
# string lets the ratings wire's daily diff starve this surface (proven live: identical requests,
# the shared UA 429'd while this one 200'd). One static UA per provider + self-pacing keeps each
# component inside its own budget — good-citizen posture, not rotation. Field note (survey, build
# night): Stooq's light-quote CSV endpoint is retired (404) and every keyed free tier gates this
# coverage — v8 chart is the last keyless source standing for futures/world-indices/commodities.
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_HDRS = {"User-Agent": _BROWSER_UA}

# The rails — section → [(yahoo symbol, display name)]. Ordered as read: US-overnight first,
# then the sun (Asia → Europe), then the barrels/metals/FX that price every session.
GLOBAL_RAILS: dict[str, list[tuple[str, str]]] = {
    "futures": [
        ("ES=F", "S&P 500 fut"),
        ("NQ=F", "Nasdaq 100 fut"),
        ("YM=F", "Dow fut"),
        ("RTY=F", "Russell 2000 fut"),
        ("^TNX", "US 10-yr yield"),
    ],
    "asia": [
        ("^N225", "Nikkei 225"),
        ("^HSI", "Hang Seng"),
        ("000001.SS", "Shanghai Comp"),
        ("^TWII", "Taiwan TAIEX"),
        ("^KS11", "KOSPI"),
        ("^AXJO", "ASX 200"),
        ("^NSEI", "Nifty 50"),
    ],
    "europe": [
        ("^FTSE", "FTSE 100"),
        ("^GDAXI", "DAX"),
        ("^FCHI", "CAC 40"),
        ("^STOXX50E", "Euro Stoxx 50"),
    ],
    "oil": [
        ("CL=F", "WTI crude"),
        ("BZ=F", "Brent crude"),
        ("NG=F", "Nat gas"),
        ("RB=F", "RBOB gasoline"),
    ],
    "metals": [
        ("GC=F", "Gold"),
        ("SI=F", "Silver"),
        ("HG=F", "Copper"),
        ("PL=F", "Platinum"),
    ],
    "fx": [
        ("DX-Y.NYB", "Dollar index"),
        ("EURUSD=X", "EUR/USD"),
        ("USDJPY=X", "USD/JPY"),
        ("USDCNY=X", "USD/CNY"),
    ],
}


# Flat scalar keys for dashboard stat tiles (value_path "scalars.<key>") — the same
# tile-first pattern as market's breadth scalars: tiles want one number, not an array walk.
SCALAR_KEYS: dict[str, str] = {
    "ES=F": "es_pct", "NQ=F": "nq_pct", "YM=F": "ym_pct", "RTY=F": "rty_pct",
    "^TNX": "us10y",  # level, not pct — the tile shows the yield itself
    "^N225": "nikkei_pct", "^HSI": "hsi_pct", "000001.SS": "shanghai_pct",
    "^TWII": "taiex_pct", "^KS11": "kospi_pct", "^AXJO": "asx_pct", "^NSEI": "nifty_pct",
    "^FTSE": "ftse_pct", "^GDAXI": "dax_pct", "^FCHI": "cac_pct", "^STOXX50E": "stoxx_pct",
    "CL=F": "wti_pct", "BZ=F": "brent_pct", "NG=F": "natgas_pct", "RB=F": "rbob_pct",
    "GC=F": "gold_pct", "SI=F": "silver_pct", "HG=F": "copper_pct", "PL=F": "platinum_pct",
    "DX-Y.NYB": "dxy_pct", "EURUSD=X": "eurusd_pct", "USDJPY=X": "usdjpy_pct",
    "USDCNY=X": "usdcny_pct",
}


def rail_scalars(rails: dict[str, list[GlobalQuote]]) -> dict[str, float]:
    """Tile-ready flat scalars from fetched rails (pct for most, level for the 10-yr)."""
    out: dict[str, float] = {}
    for cards in rails.values():
        for q in cards:
            key = SCALAR_KEYS.get(q.symbol)
            if key is None:
                continue
            val = q.price if key == "us10y" else q.change_pct
            if val is not None:
                out[key] = round(float(val), 2)
    return out


@dataclass
class GlobalQuote:
    """One rail card: last price + day change vs previous close, with venue state/time."""

    symbol: str
    name: str
    price: float | None
    change: float | None
    change_pct: float | None
    market_state: str  # Yahoo's venue state (REGULAR/CLOSED/PRE/POST…)
    market_time: int | None  # epoch seconds of the quote, venue-local meaning


def _quote_one(client: httpx.Client, symbol: str) -> GlobalQuote | None:
    """One v8 chart-meta quote. None on any per-symbol failure — the rail omits the card."""
    try:
        resp = get_with_retry(
            _CHART_URL.format(symbol=symbol), client=client, headers=_HDRS,
            params={"interval": "1d", "range": "1d"}, retries=2,
        )
        meta = resp.json()["chart"]["result"][0]["meta"]
    except Exception:  # noqa: BLE001 — per-card resilience is the contract
        return None
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    change = change_pct = None
    if price is not None and prev:
        change = price - prev
        change_pct = (price / prev - 1.0) * 100.0
    return GlobalQuote(
        symbol=symbol,
        name=meta.get("shortName") or symbol,
        price=price,
        change=change,
        change_pct=change_pct,
        market_state=meta.get("marketState") or "",
        market_time=meta.get("regularMarketTime"),
    )


def fetch_global_quotes(symbols: list[str]) -> dict[str, GlobalQuote]:
    """Quote `symbols` via crumb-free v8 chart meta, self-paced. Failures are simply absent."""
    out: dict[str, GlobalQuote] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for i, sym in enumerate(symbols):
            if i:
                time.sleep(_PACE_SECONDS)
            q = _quote_one(client, sym)
            if q is not None:
                out[sym] = q
    if not out:
        raise ProviderError("yahoo global: every symbol failed (endpoint change or network)")
    return out


def fetch_global_rails(sections: list[str] | None = None) -> dict[str, list[GlobalQuote]]:
    """The rails, resolved: one batch call, grouped + ordered per GLOBAL_RAILS.

    Unknown/unavailable symbols are omitted from their rail rather than failing the read —
    an overnight surface must degrade gracefully per-card, never brick on one delisted future.
    """
    wanted = {k: v for k, v in GLOBAL_RAILS.items() if sections is None or k in sections}
    flat = [sym for rail in wanted.values() for sym, _ in rail]
    quotes = fetch_global_quotes(flat)
    out: dict[str, list[GlobalQuote]] = {}
    for section, rail in wanted.items():
        cards = []
        for sym, display in rail:
            q = quotes.get(sym)
            if q is not None:
                q.name = display  # display names are ours, not Yahoo's marketing strings
                cards.append(q)
        out[section] = cards
    return out
