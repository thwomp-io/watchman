"""Alpaca market-data provider — the v0 (and sole) provider.

Read-only Market Data API v2 (https://data.alpaca.markets). Free tier: real-time IEX feed +
15-min-delayed SIP — plenty for research use. Auth via key-id/secret headers. The raw-GET
seam (`_raw_get`) is monkeypatched in tests so unit tests never hit the network.

Coverage caveat (handled, not hidden): Alpaca covers US-listed equities/ETFs. Mutual funds
and many OTC/grey-market ADRs are NOT covered -> returned Quote(available=False)
with a note, so callers can degrade gracefully (this is exactly why fund-proxy exists).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx

from harness._http import describe_http_error, get_with_retry
from harness.finance.models import AuctionPrint, Bar, ExtendedTrade, Quote, SessionClose
from harness.finance.providers.base import ProviderError
from harness.finance.sessions import CLOSE, ET

_DATA_BASE = "https://data.alpaca.markets"
_TRADING_BASE = "https://paper-api.alpaca.markets"  # contracts roster (read-only metadata; no orders)
_OPTION_PAGE_LIMIT = 1000  # data-API max page size for option snapshots
_CONTRACT_PAGE_LIMIT = 10000  # trading-API max page size for the contracts roster
# The contracts endpoint SILENTLY defaults its expiration window to ~1 week out (a multi-year ~900-contract
# chain comes back as only ~130 near-dated contracts) — an explicit far lte is load-bearing for LEAPS.
_CONTRACT_LOOKAHEAD_DAYS = 1200


def bars_feed(feed: str) -> str:
    """The feed to request from the bars endpoint. Alpaca's `delayed_sip` exists only for snapshots and
    latest quotes; the bars endpoint answers 400 "invalid feed: delayed_sip" (verified 2026-10-27). On
    the free plan `sip` bars are the same delayed consolidated data, so bars ask for `sip`."""
    return "sip" if feed == "delayed_sip" else feed


# The free plan serves SIP bars only from 15 minutes back ("subscription does not permit querying
# recent SIP data"); a minute of margin covers clock skew.
_SIP_DELAY = timedelta(minutes=16)


def delayed_end(end: str | None, now: datetime) -> str:
    """`delayed_sip` bars end no later than 16 minutes ago: an absent end, or one at or after the cutoff
    (a date-only end counts as reaching through that day), becomes the cutoff (RFC 3339, UTC).

    "Through that day" means midnight US Eastern, which is 04:00-05:00 UTC the next morning: comparing
    the bare date with the cutoff's UTC date let an evening request for today through, and Alpaca refused
    it ("subscription does not permit querying recent SIP data")."""
    cutoff = now - _SIP_DELAY
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not end:
        return stamp
    if len(end) == 10:  # YYYY-MM-DD
        through = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(0), tzinfo=ET)
        return stamp if through > cutoff else end
    given = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if given.tzinfo is None:
        given = given.replace(tzinfo=UTC)
    return stamp if given > cutoff else end


_AUCTION_MAX_PAGES = 5  # 10,000 trades a page: the first minute after the close fits in one for most names


def _alpaca_reason(e: httpx.HTTPError) -> str:
    """Alpaca's own one-line reason from an error body ({"message": "..."}), or ''."""
    if not isinstance(e, httpx.HTTPStatusError):
        return ""
    try:
        msg = e.response.json().get("message")
    except (ValueError, AttributeError):
        return ""
    return f" — {msg}" if isinstance(msg, str) and msg else ""


def _bar_session(bar: dict[str, Any]) -> date | None:
    """A daily bar's session date: its timestamp is midnight New York time (e.g. 04:00Z in summer)."""
    t = bar.get("t")
    if not isinstance(t, str):
        return None
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET).date()


def _session_close(symbol: str, snap: Any, session: date, feed: str) -> SessionClose | None:
    """The close for `session` from a snapshot's dailyBar / prevDailyBar, whichever is dated that day."""
    if not isinstance(snap, dict):
        return None
    daily, prev = snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
    for bar, before in ((daily, prev), (prev, None)):
        if bar.get("c") is not None and _bar_session(bar) == session:
            prior = before.get("c") if before else None
            return SessionClose(
                symbol=symbol, close=float(bar["c"]), session=session.isoformat(), source=feed,
                prior_close=float(prior) if prior is not None else None,
            )
    return None


# A day's move at least this large (%) is checked for a split before it's believed. Snapshot bars aren't
# split-adjusted, so on a split's ex-date the prior close is the pre-split price (a 3:1 split read as
# -70%). The smallest common split, 5:4, moves a price 20%; a real move this size just finds no split.
_SPLIT_SUSPECT_PCT = 15.0


def _ratio_label(r: float) -> str:
    """3.0 -> '3:1', 0.1 -> '1:10', 1.25 -> '5:4' (as a split ratio is written)."""
    from fractions import Fraction

    f = Fraction(r).limit_denominator(100)
    return f"{f.numerator}:{f.denominator}"


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
        try:
            resp = get_with_retry(f"{_DATA_BASE}{path}", params=params, headers=self._headers())
        except httpx.HTTPError as e:  # one line for the caller (an unknown symbol is an HTTP 400)
            raise ProviderError(f"alpaca {path}: {describe_http_error(e)}{_alpaca_reason(e)}") from None
        self.request_count += 1
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def get_splits(self, symbol: str, since: str) -> list[tuple[str, float]]:
        """Forward/reverse splits with an ex-date on or after `since` → [(ex_date, new/old)].
        The corporate-actions endpoint on the same data host."""
        raw = self._raw_get(
            "/v1/corporate-actions",
            {"symbols": symbol.upper(), "types": "forward_split,reverse_split", "start": since},
        )
        ca = raw.get("corporate_actions") or {}
        out: list[tuple[str, float]] = []
        for kind in ("forward_splits", "reverse_splits"):
            for row in ca.get(kind) or []:
                try:
                    out.append((str(row["ex_date"]), float(row["new_rate"]) / float(row["old_rate"])))
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue
        return sorted(out)

    def _ex_date_splits(self, suspects: dict[str, date]) -> dict[str, float]:
        """symbol -> new/old for a split whose ex-date is that symbol's given session: the one day a
        snapshot's prior close is still the pre-split price. One corporate-actions call for every suspect;
        a failure adjusts nothing (the move then reads as the feed gave it)."""
        if not suspects:
            return {}
        try:
            raw = self._raw_get("/v1/corporate-actions", {
                "symbols": ",".join(sorted(suspects)), "types": "forward_split,reverse_split",
                "start": min(suspects.values()).isoformat(), "end": max(suspects.values()).isoformat(),
            })
        except ProviderError:
            return {}
        ca = raw.get("corporate_actions") or {}
        out: dict[str, float] = {}
        for kind in ("forward_splits", "reverse_splits"):
            for row in ca.get(kind) or []:
                try:
                    sym, ex = str(row["symbol"]).upper(), date.fromisoformat(str(row["ex_date"]))
                    ratio = float(row["new_rate"]) / float(row["old_rate"])
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue
                if suspects.get(sym) == ex and ratio > 0:
                    out[sym] = out.get(sym, 1.0) * ratio
        return out

    @staticmethod
    def _split_suspect(price: float | None, prev: float | None) -> bool:
        return bool(price and prev and abs(price / prev - 1.0) * 100.0 >= _SPLIT_SUSPECT_PCT)

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
        suspects: dict[str, date] = {}
        for sym in symbols:
            snap = snaps.get(sym.upper()) if isinstance(snaps, dict) else None
            q = self._quote_from_snapshot(sym.upper(), snap)
            out.append(q)
            day = _bar_session((snap or {}).get("dailyBar") or {}) if isinstance(snap, dict) else None
            if day is not None and self._split_suspect(q.price, q.prev_close):
                suspects[q.symbol] = day
        ratios = self._ex_date_splits(suspects)
        for q in out:
            if q.symbol in ratios and q.prev_close:
                r = ratios[q.symbol]
                q.prev_close = q.prev_close / r
                why = f"prior close adjusted for a {_ratio_label(r)} split (ex-date {suspects[q.symbol]})"
                q.note = f"{q.note} · {why}" if q.note else why
        return out

    def get_session_closes(self, symbols: list[str], session: date) -> dict[str, SessionClose]:
        """The official close of the regular `session`, from snapshot daily bars. `delayed_sip` first:
        its daily bar closes at the consolidated official close (verified 2026-10-28, matching a broker's
        close to the cent), and the daily bar excludes extended hours. IEX's daily bar is the
        fallback (IEX's own last regular-session trade, a few cents off). The bar is picked by DATE, so
        a pre-open read the next morning still gets the prior session."""
        out: dict[str, SessionClose] = {}
        want = [s.upper() for s in symbols]
        for feed in ("delayed_sip", "iex"):
            missing = [s for s in want if s not in out]
            if not missing:
                break
            try:
                snaps = self._raw_get("/v2/stocks/snapshots", {"symbols": ",".join(missing), "feed": feed})
            except ProviderError:
                continue
            for sym in missing:
                close = _session_close(sym, snaps.get(sym), session, feed)
                if close is not None:
                    out[sym] = close
        suspects = {s: session for s, c in out.items() if self._split_suspect(c.close, c.prior_close)}
        for sym, r in self._ex_date_splits(suspects).items():
            prior = out[sym].prior_close
            if prior:
                out[sym].prior_close = prior / r
        return out

    def get_extended_trades(self, symbols: list[str], session: date) -> dict[str, ExtendedTrade]:
        """The latest `delayed_sip` trade per symbol when it printed after the session's 16:00 ET close
        (the consolidated tape: IEX alone sees a sliver of extended-hours volume)."""
        cutoff = datetime.combine(session, CLOSE, tzinfo=ET)
        snaps = self._raw_get(
            "/v2/stocks/snapshots", {"symbols": ",".join(s.upper() for s in symbols), "feed": "delayed_sip"}
        )
        out: dict[str, ExtendedTrade] = {}
        for sym in (s.upper() for s in symbols):
            snap = snaps.get(sym)
            trade = (snap.get("latestTrade") or {}) if isinstance(snap, dict) else {}
            t, p = trade.get("t"), trade.get("p")
            if not isinstance(t, str) or p is None:
                continue
            if datetime.fromisoformat(t.replace("Z", "+00:00")) > cutoff:
                out[sym] = ExtendedTrade(symbol=sym, price=float(p), t=t)
        return out

    def get_closing_auction(self, symbol: str, session: date) -> AuctionPrint | None:
        """The closing cross from the SIP trade tape: the LARGEST trade carrying condition "6" (Closing
        Print) in the first minute after 16:00 ET. Other venues tag small prints "6" too (QQQ 10/30: Arca
        139 @ 690.59 beside Nasdaq's 1,377,942 @ 690.49), NYSE prints its cross "6" without "X", and the
        official-close re-report ("M") isn't counted (verified 2026-10-30 on Nasdaq- and NYSE-listed
        names). The free plan serves SIP trades from 15 minutes back."""
        start = datetime.combine(session, CLOSE, tzinfo=ET).astimezone(UTC)
        params: dict[str, str | int] = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (start + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed": "sip",
            "limit": 10000,
        }
        best: dict[str, Any] | None = None
        for _ in range(_AUCTION_MAX_PAGES):
            raw = self._raw_get(f"/v2/stocks/{symbol.upper()}/trades", params)
            for t in raw.get("trades") or []:
                if isinstance(t, dict) and "6" in (t.get("c") or []) and t.get("p") is not None:
                    if best is None or int(t.get("s") or 0) > int(best.get("s") or 0):
                        best = t
            token = raw.get("next_page_token")
            if not token:
                break
            params["page_token"] = str(token)
        if best is None:
            return None
        return AuctionPrint(symbol=symbol.upper(), price=float(best["p"]), size=int(best.get("s") or 0),
                            exchange=str(best.get("x") or ""))

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
            "feed": bars_feed(self._feed),
            "limit": 10000,
            # split-adjusted bars: Alpaca defaults to RAW, which renders a stock split as a giant
            # fake drawdown and can seed a bogus move-day. Request split-adjustment so a split is
            # not misread as a real move. Dividends stay raw (price history, not total return).
            "adjustment": "split",
        }
        if self._feed == "delayed_sip":
            end = delayed_end(end, datetime.now(UTC))
        if end:
            params["end"] = end
        raw = self._raw_get(f"/v2/stocks/{symbol.upper()}/bars", params)
        bars_raw = raw.get("bars") or []
        if not isinstance(bars_raw, list):
            return []
        return [Bar.model_validate(b) for b in bars_raw if isinstance(b, dict)]

    # --- seam for tests: the trading API (contracts roster) mirrors _raw_get on its own base ---
    def _raw_get_trading(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            resp = get_with_retry(f"{_TRADING_BASE}{path}", params=params, headers=self._headers())
        except httpx.HTTPError as e:  # one line for the caller (an unknown symbol is an HTTP 400)
            raise ProviderError(f"alpaca {path}: {describe_http_error(e)}{_alpaca_reason(e)}") from None
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
