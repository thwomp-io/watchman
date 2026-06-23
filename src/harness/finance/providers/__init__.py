"""Provider registry / factory.

Two kinds, each sole + behind its protocol: **market data** (Alpaca — free tier: real-time IEX +
15-min-delayed SIP) and **fundamentals** (EDGAR — keyless, UA-only). The factories keep call sites
provider-agnostic so a viable alternative could slot in later without touching the service or
adapters. READ-ONLY by design — no execution/trading provider here
"""

from __future__ import annotations

from harness.finance.config.settings import Settings, get_settings
from harness.finance.providers.alpaca_provider import build_alpaca_provider
from harness.finance.providers.base import FundamentalsProvider, MarketDataProvider, ProviderError
from harness.finance.providers.edgar_provider import build_edgar_provider

__all__ = [
    "FundamentalsProvider",
    "MarketDataProvider",
    "ProviderError",
    "get_fundamentals_provider",
    "get_market_data_provider",
]


def get_market_data_provider(
    name: str = "alpaca", *, settings: Settings | None = None, feed: str = "iex"
) -> MarketDataProvider:
    settings = settings or get_settings()
    if name == "alpaca":
        # Keyless clone-and-run: no Alpaca keys but a static-quote fixture is available (a demo pack /
        # scaffolded corpus that ships finance/quotes.json) → render from it instead of erroring. Keys
        # always win when present; no keys + no fixture falls through to Alpaca (which raises a clear
        # "add your keys" error on first call).
        if not settings.has_alpaca_keys and (quotes := settings.quotes_path) is not None:
            from harness.finance.providers.static_provider import build_static_provider

            return build_static_provider(quotes)
        return build_alpaca_provider(
            settings.alpaca_api_key_id, settings.alpaca_api_secret_key, feed=feed
        )
    if name == "static":
        from harness.finance.providers.static_provider import build_static_provider

        if (quotes := settings.quotes_path) is None:
            raise ProviderError("static provider requested but no finance/quotes.json fixture found")
        return build_static_provider(quotes)
    raise ProviderError(f"unknown market-data provider: {name!r}")


def get_fundamentals_provider(name: str = "edgar", *, recent: int = 6) -> FundamentalsProvider:
    """Fundamentals source factory. EDGAR is sole + keyless (UA-only); behind the protocol so a paid
    alternative could slot in later without touching the service/adapters."""
    if name == "edgar":
        return build_edgar_provider(recent=recent)
    raise ProviderError(f"unknown fundamentals provider: {name!r}")
