"""Alpaca market-data provider — the v0 (and sole) provider.

Read-only Market Data API v2 (https://data.alpaca.markets). Free tier: real-time IEX feed +
15-min-delayed SIP — plenty for a sounding-board. Auth via key-id/secret headers. The raw-GET
seam (`_raw_get`) is monkeypatched in tests so unit tests never hit the network.

Coverage caveat (handled, not hidden): Alpaca covers US-listed equities/ETFs. Mutual funds
and many OTC/grey-market ADRs are NOT covered -> returned Quote(available=False)
with a note, so callers can degrade gracefully (this is exactly why fund-proxy exists).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from harness._http import get_with_retry
from harness.finance.models import Bar, Quote
from harness.finance.providers.base import ProviderError

_DATA_BASE = "https://data.alpaca.markets"
_TRADING_BASE = "https://paper-api.alpaca.markets"  # contracts roster (read-only metadata; no orders)
_OPTION_PAGE_LIMIT = 1000  # data-API max page size for option snapshots
_CONTRACT_PAGE_LIMIT = 10000  # trading-API max page size for the contracts roster
# The contracts endpoint SILENTLY defaults its expiration window to ~1 week out (a multi-year ~900-contract
# chain comes back as only ~130 near-dated contracts) — an explicit far lte is load-bearing for LEAPS.
_CONTRACT_LOOKAHEAD_DAYS = 1200


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

    # --- seam for tests: the trading API (contracts roster) mirrors _raw_get on its own base ---
    def _raw_get_trading(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        resp = get_with_retry(f"{_TRADING_BASE}{path}", params=params, headers=self._headers())
        self.request_count += 1
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def fetch_option_chain_snapshots(self, symbol: str) -> dict[str, Any]:
        """Full option-chain snapshots for one underlying → raw {OCC symbol: snapshot}.

        Uses the INDICATIVE feed (the free tier — indicative quotes, not the OPRA consolidated
        tape; plenty for sentiment gauges). Follows `next_page_token` until the chain is exhausted
        (a liquid underlying runs well past one 1000-contract page)."""
        snaps: dict[str, Any] = {}
        params: dict[str, str | int] = {"feed": "indicative", "limit": _OPTION_PAGE_LIMIT}
        while True:
            raw = self._raw_get(f"/v1beta1/options/snapshots/{symbol.upper()}", params)
            page = raw.get("snapshots")
            if isinstance(page, dict):
                snaps.update(page)
            token = raw.get("next_page_token")
            if not token or not page:
                return snaps
            params = {**params, "page_token": str(token)}

    def fetch_option_open_interest(self, symbol: str) -> tuple[dict[str, int], str | None]:
        """Open interest per contract from the trading-API roster → ({OCC symbol: OI}, newest
        OI date).

        Read-only metadata off the paper-trading host (no order capability is touched). The
        explicit expiration window is load-bearing — without it the endpoint silently truncates to
        ~1 week of expiries. Contracts with no reported OI yet (fresh listings) are omitted, and OI
        itself is exchange-reported on a ~1-day lag (the returned date says so)."""
        today = date.today()
        oi: dict[str, int] = {}
        as_of: str | None = None
        params: dict[str, str | int] = {
            "underlying_symbols": symbol.upper(),
            "limit": _CONTRACT_PAGE_LIMIT,
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": (today + timedelta(days=_CONTRACT_LOOKAHEAD_DAYS)).isoformat(),
        }
        while True:
            raw = self._raw_get_trading("/v2/options/contracts", params)
            contracts = raw.get("option_contracts") or []
            if isinstance(contracts, list):
                for c in contracts:
                    if not (isinstance(c, dict) and c.get("symbol") and c.get("open_interest")):
                        continue
                    oi[str(c["symbol"])] = int(float(c["open_interest"]))
                    d = c.get("open_interest_date")
                    if isinstance(d, str) and (as_of is None or d > as_of):
                        as_of = d
            token = raw.get("next_page_token")
            if not token or not contracts:
                return oi, as_of
            params = {**params, "page_token": str(token)}


def build_alpaca_provider(
    key_id: str | None, secret_key: str | None, *, feed: str = "iex"
) -> AlpacaProvider:
    return AlpacaProvider(key_id=key_id, secret_key=secret_key, feed=feed)
