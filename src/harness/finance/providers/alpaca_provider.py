"""Alpaca market-data provider — the v0 (and sole) provider.

Read-only Market Data API v2 (https://data.alpaca.markets). Free tier: real-time IEX feed +
15-min-delayed SIP — plenty for a sounding-board. Auth via key-id/secret headers. The raw-GET
seam (`_raw_get`) is monkeypatched in tests so unit tests never hit the network.

Coverage caveat (handled, not hidden): Alpaca covers US-listed equities/ETFs. Mutual funds
and many OTC/grey-market ADRs (NSRGY/RHHBY/...) are NOT covered -> returned Quote(available=False)
with a note, so callers can degrade gracefully (this is exactly why fund-proxy exists).
"""

from __future__ import annotations

from typing import Any

from harness._http import get_with_retry
from harness.finance.models import Bar, Quote
from harness.finance.providers.base import ProviderError

_DATA_BASE = "https://data.alpaca.markets"


class AlpacaProvider:
    name = "alpaca"

    def __init__(self, key_id: str | None, secret_key: str | None, *, feed: str = "iex") -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._feed = feed
        self.request_count = 0

    def _headers(self) -> dict[str, str]:
        if not (self._key_id and self._secret_key):
            raise ProviderError(
                "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set — cannot query market data. "
                "Add them to .env (free keys at https://app.alpaca.markets/)."
            )
        return {"APCA-API-KEY-ID": self._key_id, "APCA-API-SECRET-KEY": self._secret_key}

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _raw_get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        resp = get_with_retry(f"{_DATA_BASE}{path}", params=params, headers=self._headers())
        self.request_count += 1
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        raw = self._raw_get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(s.upper() for s in symbols), "feed": self._feed},
        )
        # The snapshots endpoint returns {symbol: snapshot} (sometimes nested under "snapshots").
        snaps = raw.get("snapshots", raw)
        out: list[Quote] = []
        for sym in symbols:
            snap = snaps.get(sym.upper()) if isinstance(snaps, dict) else None
            out.append(self._quote_from_snapshot(sym.upper(), snap))
        return out

    def _quote_from_snapshot(self, symbol: str, snap: Any) -> Quote:
        if not isinstance(snap, dict):
            return Quote(
                symbol=symbol,
                available=False,
                feed=self._feed,
                note="no snapshot returned (not on this feed — mutual fund / OTC / unknown symbol)",
            )
        trade = snap.get("latestTrade") or {}
        daily = snap.get("dailyBar") or {}
        prev = snap.get("prevDailyBar") or {}
        price = trade.get("p") if trade.get("p") is not None else daily.get("c")
        if price is None:
            return Quote(
                symbol=symbol,
                available=False,
                feed=self._feed,
                note="snapshot had no trade/daily price",
            )
        return Quote(
            symbol=symbol,
            available=True,
            price=float(price),
            prev_close=float(prev["c"]) if prev.get("c") is not None else None,
            day_open=float(daily["o"]) if daily.get("o") is not None else None,
            day_high=float(daily["h"]) if daily.get("h") is not None else None,
            day_low=float(daily["l"]) if daily.get("l") is not None else None,
            volume=int(daily["v"]) if daily.get("v") is not None else None,
            as_of=trade.get("t"),
            feed=self._feed,
        )

    def get_bars(
        self, symbol: str, *, start: str, end: str | None = None, timeframe: str = "1Day"
    ) -> list[Bar]:
        params: dict[str, str | int] = {
            "timeframe": timeframe,
            "start": start,
            "feed": self._feed,
            "limit": 10000,
            # split-adjusted bars: Alpaca defaults to RAW, which renders a stock split as a giant
            # fake drawdown and can seed a bogus move-day. Request split-adjustment so a split is
            # not misread as a real move. Dividends stay raw (price history, not total return).
            "adjustment": "split",
        }
        if end:
            params["end"] = end
        raw = self._raw_get(f"/v2/stocks/{symbol.upper()}/bars", params)
        bars_raw = raw.get("bars") or []
        if not isinstance(bars_raw, list):
            return []
        return [Bar.model_validate(b) for b in bars_raw if isinstance(b, dict)]


def build_alpaca_provider(
    key_id: str | None, secret_key: str | None, *, feed: str = "iex"
) -> AlpacaProvider:
    return AlpacaProvider(key_id=key_id, secret_key=secret_key, feed=feed)
