"""What the standing watch remembers between runs: three small JSON files in the cache directory.

- `SeenCache`: the headlines already shown, so a run reports only new ones.
- `EarningsDateCache`: each held stock's confirmed print date, asked for at most once a day.
- `ConsensusState`: each held stock's last consensus price target, the baseline a change is
  measured against.

All three load to an empty state when the file is missing or unreadable: losing one costs a repeat
headline or a refetch, never a failed run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import ClassVar, Self

from pydantic import BaseModel, Field

from harness.finance.models import ConsensusPT, EarningsDate, NewsItem
from harness.paths import cache_file

_SEEN_LIMIT = 2000


class _StateFile(BaseModel):
    """A model persisted as one JSON file under the cache directory."""

    filename: ClassVar[str]

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        path = path or cache_file(cls.filename)
        try:
            return cls.model_validate(json.loads(path.read_text()))
        except (OSError, ValueError):  # missing, unreadable, not JSON, or not this shape
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or cache_file(self.filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json())


def _fetched_within(entry: Mapping[str, object], today: date, ttl_days: int) -> bool:
    try:
        return (today - date.fromisoformat(str(entry["fetched"]))).days <= ttl_days
    except (KeyError, ValueError):
        return False


class SeenCache(_StateFile):
    """The URLs of headlines already reported. Bounded: the oldest age out."""

    filename = "news-seen.json"

    seen: list[str] = Field(default_factory=list)

    def filter_new(self, items: list[NewsItem]) -> list[NewsItem]:
        seen = set(self.seen)
        return [i for i in items if i.url and i.url not in seen]

    def mark(self, items: list[NewsItem]) -> None:
        seen = set(self.seen)
        seen.update(i.url for i in items if i.url)
        self.seen = sorted(seen)[-_SEEN_LIMIT:]


class EarningsDateCache(_StateFile):
    """Each symbol's confirmed earnings date, looked up at most once per `ttl_days`.

    The pulse runs many times a market day against an unofficial API; without this every run would
    sweep the whole book. The date moves on a quarter's timescale, so a day-old confirmed date still
    beats an estimate. A miss is cached like a hit: most holdings have no announced date most of
    the time, and refetching each of them every run would undo the point.
    """

    filename = "earnings-dates.json"

    # symbol → {"date", "confirmed", "fetched"} for a hit, {"miss": True, "fetched"} for a miss
    entries: dict[str, dict[str, str | bool]] = Field(default_factory=dict)

    def known(self, symbol: str, today: date, ttl_days: int = 1) -> bool:
        """Whether the symbol was looked up within the TTL, hit or miss, so the caller can skip the
        network. `fresh` then says which."""
        entry = self.entries.get(symbol.upper())
        return entry is not None and _fetched_within(entry, today, ttl_days)

    def fresh(self, symbol: str, today: date, ttl_days: int = 1) -> EarningsDate | None:
        """The cached date if it was fetched within the TTL; None for a stale entry or a cached miss."""
        entry = self.entries.get(symbol.upper())
        if not entry or entry.get("miss") or not _fetched_within(entry, today, ttl_days):
            return None
        try:
            return EarningsDate(
                symbol=symbol.upper(),
                report_date=date.fromisoformat(str(entry["date"])),
                confirmed=bool(entry["confirmed"]),
            )
        except (KeyError, ValueError):
            return None

    def put(self, found: EarningsDate, today: date) -> None:
        self.entries[found.symbol] = {
            "date": found.report_date.isoformat(),
            "confirmed": found.confirmed,
            "fetched": today.isoformat(),
        }

    def put_miss(self, symbol: str, today: date) -> None:
        self.entries[symbol.upper()] = {"miss": True, "fetched": today.isoformat()}


class ConsensusState(_StateFile):
    """Each symbol's last-seen consensus price target and rating mix.

    `diff` compares a fresh snapshot with the stored one and describes a material change. The first
    sighting of a symbol only stores the baseline: a baseline is not news.
    """

    filename = "ratings-consensus.json"

    # symbol → {"mean", "buy", "hold", "sell", "fetched"}
    entries: dict[str, dict[str, float | int | str]] = Field(default_factory=dict)

    def stale(self, symbol: str, today: date, ttl_days: int = 1) -> bool:
        entry = self.entries.get(symbol.upper())
        return not entry or not _fetched_within(entry, today, ttl_days)

    def diff(self, current: ConsensusPT, pt_move_pct: float = 2.0) -> str | None:
        """One line describing the change since the stored snapshot, or None when there is no
        stored snapshot or nothing material moved: the mean target by `pt_move_pct` or more, or the
        buy/hold/sell mix at all."""
        entry = self.entries.get(current.symbol)
        if not entry:
            return None
        try:
            prior_mean = float(entry["mean"])
            prior_mix = (int(entry["buy"]), int(entry["hold"]), int(entry["sell"]))
        except (KeyError, TypeError, ValueError):
            return None
        changes: list[str] = []
        if prior_mean > 0:
            move = (current.mean - prior_mean) / prior_mean * 100
            if abs(move) >= pt_move_pct:
                changes.append(f"consensus PT ${prior_mean:,.2f} → ${current.mean:,.2f} ({move:+.1f}%)")
        mix = (current.buy, current.hold, current.sell)
        if mix != prior_mix:
            changes.append(
                f"rating mix {prior_mix[0]}B/{prior_mix[1]}H/{prior_mix[2]}S"
                f" → {mix[0]}B/{mix[1]}H/{mix[2]}S"
            )
        return "; ".join(changes) or None

    def put(self, current: ConsensusPT, today: date) -> None:
        self.entries[current.symbol] = {
            "mean": current.mean,
            "buy": current.buy,
            "hold": current.hold,
            "sell": current.sell,
            "fetched": today.isoformat(),
        }
