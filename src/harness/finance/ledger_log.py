"""The ledger's history: what portfolio.yaml used to say, and the evidence for each change.

portfolio.yaml holds what is true now. How it got there lives here, in three tables:

  fills    one row per buy applied by `log-fill`: the fill, and the holding before and after.
  syncs    one row per change made against a broker screen (a balance, a NAV, an order placed or
           cancelled), as the sentence that was written at the time.
  grades   one row per day a fund's proxy estimate was checked against its published NAV.

The rows live in the shared history database (`harness.history_store`), which is never committed:
after every write the whole history is exported as JSON lines beside the portfolio file
(`ledger-history/ledger.jsonl`), that export is what version control carries, and `rebuild`
reproduces the tables from it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import Field

from harness.history_store import Row, Store, default_db_path

EXPORT_DIR = "ledger-history"
EXPORT_NAME = "ledger.jsonl"


class _Row(Row):
    """What every ledger row carries."""

    date: str | None = None  # YYYY-MM-DD the row is about
    text: str = ""  # the sentence written at the time
    screen: str = ""  # the screenshot or other evidence id
    source: str = "verb"  # "verb", or "migrated" for history moved in from the yaml's comments


class FillRow(_Row):
    table = "fills"

    ts: str = ""  # local time of the fill, ISO with offset
    symbol: str = ""
    account: str = ""
    qty: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    shares_before: float = 0.0
    shares_after: float = 0.0
    avg_before: float = 0.0
    avg_after: float = 0.0
    basis_before: float = 0.0
    basis_after: float = 0.0
    data: dict[str, Any] = Field(default_factory=dict)  # the order it settled, the cash sweep, …


class SyncRow(_Row):
    table = "syncs"

    ts: str = ""  # when it was written, as written
    symbol: str | None = None  # the holding it concerns, when it concerns one
    path: str = ""  # where in the yaml it applies, e.g. "holdings/ABC/balance"


class GradeRow(_Row):
    table = "grades"

    fund: str = ""
    day: int | None = None  # the running count of graded days
    nav_before: float | None = None
    nav_after: float | None = None
    pct: float | None = None  # the NAV's move, in percent
    estimate_pct: float | None = None  # what the proxy said
    comparator: str = ""  # which estimate was graded (the written lean, the filled proxy, …)
    miss_pp: float | None = None  # |estimate − actual|, in percentage points


ROW_TYPES: tuple[type[_Row], ...] = (FillRow, SyncRow, GradeRow)


class Ledger(Store):
    """The history store for one portfolio file."""

    def __init__(self, db_path: Path, export_path: Path) -> None:
        super().__init__(db_path, export_path, ROW_TYPES)

    @classmethod
    def for_portfolio(cls, portfolio_path: Path) -> Ledger:
        return cls(default_db_path(), portfolio_path.parent / EXPORT_DIR / EXPORT_NAME)

    def rows(
        self,
        *,
        kinds: tuple[str, ...] | None = None,
        symbol: str | None = None,
        since: date | None = None,
        contains: str | None = None,
        limit: int | None = None,
    ) -> list[_Row]:
        """The rows matching every filter given, newest first.

        `symbol` matches a row about that symbol. A sync row also matches when its text names the
        symbol, since one sync line can cover several holdings; a fill or a grade is about one.
        `since` keeps rows dated on or after that day and leaves undated rows out.
        """
        sym = symbol.upper() if symbol else None
        needle = contains.lower() if contains else None
        chosen: list[_Row] = []
        for row_type in ROW_TYPES:
            if kinds and row_type.table not in kinds:
                continue
            for row in self.all(row_type):
                about = getattr(row, "symbol", None) or getattr(row, "fund", None)
                if sym and about != sym and not (isinstance(row, SyncRow) and sym in _words(row.text)):
                    continue
                if since and (row.date is None or row.date < since.isoformat()):
                    continue
                if needle and needle not in row.text.lower():
                    continue
                chosen.append(row)
        chosen.sort(key=lambda row: (row.date or "", row.id), reverse=True)
        return chosen[:limit] if limit else chosen


def _words(text: str) -> set[str]:
    """The upper-cased tokens of `text`, for matching a ticker as a whole word."""
    return {word.strip("()[]{}:;,.'\"/") for word in text.upper().split()}
