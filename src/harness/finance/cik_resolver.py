"""Ticker → SEC CIK resolution over the bundled canonical map (company_tickers.json).

A CIK (Central Index Key) is SEC's permanent primary key for a filer — every EDGAR API call is keyed
by CIK, not ticker (tickers get reused/reassigned; CIKs never change). SEC's authoritative ticker→CIK
phonebook (`company_tickers.json`) is served only from www.sec.gov, which WAF-blocks non-browser
clients regardless of UA — so we bundle it (fetched once via browser; refresh = re-download). This is
a pure static lookup (no network); the data.sec.gov XBRL APIs the EdgarProvider hits *are* open.

Ticker-only resolution by design: ETFs / mutual funds / foreign ADRs aren't US XBRL filers and aren't
in the map (so they're "not found"). A miss is honest (None); the caller offers a `--cik` override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from harness.finance.config.settings import COMPANY_TICKERS_PATH


@dataclass(frozen=True)
class CikEntry:
    cik: str  # zero-padded to 10 digits (EDGAR API form)
    title: str


class CikResolver:
    """Loads the bundled ticker→CIK map once (lazily) and resolves case-insensitively."""

    def __init__(self, map_path: Path | None = None) -> None:
        self._path = map_path or COMPANY_TICKERS_PATH
        self._map: dict[str, CikEntry] | None = None

    def _load(self) -> dict[str, CikEntry]:
        if self._map is None:
            raw = json.loads(self._path.read_text())
            out: dict[str, CikEntry] = {}
            # SEC shape: {"0": {"cik_str": int, "ticker": str, "title": str}, ...}
            for row in raw.values():
                if not isinstance(row, dict):
                    continue
                ticker = str(row.get("ticker", "")).upper()
                cik_str = row.get("cik_str")
                if ticker and cik_str is not None:
                    out[ticker] = CikEntry(cik=f"{int(cik_str):010d}", title=str(row.get("title", "")))
            self._map = out
        return self._map

    def lookup(self, symbol: str) -> CikEntry | None:
        return self._load().get(symbol.upper())

    def cik_for(self, symbol: str) -> str | None:
        entry = self.lookup(symbol)
        return entry.cik if entry else None
