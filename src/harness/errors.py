"""Shared error types across harness lanes.

Extracted at consolidation time — `ProviderError` was defined verbatim in both
`harness.travel.providers.base` and `harness.finance.providers.base`. Each lane's `providers/base`
re-exports it from here, so every existing `from harness.<lane>.providers.base import ProviderError`
import path stays valid.
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Raised when a provider can't fulfil a request (missing key/credentials, upstream error)."""
