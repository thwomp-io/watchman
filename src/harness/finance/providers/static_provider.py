"""Static-quote provider — offline market data from a corpus/pack fixture.

The clone-and-run path: when no Alpaca keys are configured but a corpus (or loaded weight pack) ships a
`finance/quotes.json`, the finance dashboards render from those fictional last-prices — no live calls, no
keys. It implements the same `MarketDataProvider` protocol, so every quote-based call site (net worth,
positions, concentration, day-moves, pulse, and the whole-market `market` snapshot) works unchanged.

Scope (deliberate): only point-in-time quotes are fixtured. Historical OHLCV bars are NOT — `get_bars`
returns `[]`, so the position-chart widget degrades gracefully offline (the agreed "graceful rest").

Fixture format — `{ "SYM": {"price": 1.0, "prev_close": 0.99, ...}, ... }`. A bare number
(`"SYM": 1.0`) is accepted as just the price. Fields mirror the `Quote` model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.finance.models import Bar, Quote
from harness.finance.providers.base import ProviderError

_OPTIONAL_FLOATS = ("prev_close", "day_open", "day_high", "day_low")


class StaticQuoteProvider:
    name = "static"

    def __init__(self, quotes_path: Path) -> None:
        self._path = Path(quotes_path)
        self._quotes = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProviderError(f"static quotes unreadable ({self._path}): {e}") from e
        if not isinstance(raw, dict):
            raise ProviderError(f"static quotes must be a JSON object of symbol→quote ({self._path})")
        # a bare number is shorthand for {"price": n}
        return {
            str(k).upper(): (v if isinstance(v, dict) else {"price": v})
            for k, v in raw.items()
        }

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        out: list[Quote] = []
        for sym in symbols:
            q = self._quotes.get(sym.upper())
            price = q.get("price") if q is not None else None
            if q is None or price is None:
                out.append(Quote(symbol=sym.upper(), available=False, feed="static",
                                 note="no static quote (offline demo data)"))
                continue
            kwargs: dict[str, Any] = {
                k: float(q[k]) for k in _OPTIONAL_FLOATS if q.get(k) is not None
            }
            if q.get("volume") is not None:
                kwargs["volume"] = int(q["volume"])
            out.append(Quote(symbol=sym.upper(), available=True, price=float(price),
                             as_of=q.get("as_of"), feed="static", note="offline demo data", **kwargs))
        return out

    def get_bars(
        self, symbol: str, *, start: str, end: str | None = None, timeframe: str = "1Day"
    ) -> list[Bar]:
        # No historical fixture offline — the position chart degrades gracefully (empty series).
        return []


def build_static_provider(quotes_path: Path) -> StaticQuoteProvider:
    return StaticQuoteProvider(quotes_path)
