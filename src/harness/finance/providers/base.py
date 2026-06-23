"""Provider abstractions (structural Protocols, so each provider kind is swappable later).

Two distinct provider *kinds*: market data (live prices/bars — Alpaca) and fundamentals (reported
GAAP/XBRL financials — EDGAR). Separate protocols + factories, mirroring the travel lane's
multi-protocol shape, so a fundamentals source never gets shoehorned into the market-data interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from harness.errors import ProviderError  # re-exported for back-compat (was defined here)
from harness.finance.models import Bar, Fundamentals, Quote

__all__ = ["Bar", "Fundamentals", "FundamentalsProvider", "MarketDataProvider", "ProviderError", "Quote"]


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Return one Quote per requested symbol (preserving order). A symbol the provider can't
        cover comes back as Quote(available=False) — never dropped silently."""
        ...

    def get_bars(
        self, symbol: str, *, start: str, end: str | None = None, timeframe: str = "1Day"
    ) -> list[Bar]:
        """Return OHLCV bars for `symbol` over [start, end] at the given timeframe."""
        ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    name: str

    def get_fundamentals(self, symbol: str, cik: str, *, entity_name: str = "") -> Fundamentals:
        """Return reported GAAP/XBRL financials for `symbol` at the (already-resolved) `cik`,
        read-only. `entity_name` is a fallback display name if the XBRL responses carry none.
        Unreported concepts come back as empty series + a note (never fabricated)."""
        ...
