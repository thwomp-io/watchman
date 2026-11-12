"""The calibration ledger: predictions logged before they resolve, then scored.

An entry is a claim, a probability, a resolve-by date and the test that will settle it, written
before the outcome is known. Resolving records 1 (happened) or 0 (didn't) and the Brier score,
(p − outcome)². An entry whose test turns out not to be evaluable as written is voided, never
re-judged on a substitute, and stays out of every score. The claim, the probability and the test are
fixed once logged: a changed view is a new entry.

The rows live in the shared history database; `calibration/ledger.jsonl` in the corpus is the export
version control carries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from harness.history_store import Row, Store, default_db_path

EXPORT_NAME = "ledger.jsonl"
BUCKETS: tuple[tuple[float, float], ...] = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))

# A test that leans on a figure someone else publishes should say what happens when it isn't.
_SOURCE_FIGURE = re.compile(r"\by/y\b|\bm/m\b|\bsettles?\b|\bprints?\b|\breports?\b|\bpublish", re.IGNORECASE)
_FALLBACK = re.compile(r"\belse\b|\botherwise\b|\bif not\b|\bif no\b|\bfallback\b|\bvoid if\b", re.IGNORECASE)


class Entry(Row):
    table = "calibration"

    ref: str = ""  # C001, C002, …
    owner: str = ""
    made: str = ""  # when the call was made, ISO
    source: str = ""  # where it was made (a note, a ticket)
    claim: str = ""
    p: float = 0.0
    resolves_by: str = ""  # YYYY-MM-DD
    test: str = ""
    outcome: int | None = None  # None while open or when voided
    resolved: str = ""  # YYYY-MM-DD it was resolved or voided
    brier: float | None = None
    voided: str = ""  # why the test couldn't be evaluated
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.outcome is None and not self.voided


class EntryError(ValueError):
    """An entry or a resolution that breaks the ledger's rules. The message says which."""


@dataclass(frozen=True)
class Bucket:
    low: float
    high: float
    n: int
    mean_p: float | None
    hit_rate: float | None


@dataclass(frozen=True)
class Score:
    owner: str
    n: int
    brier: float | None
    buckets: tuple[Bucket, ...]


class Calibration(Store):
    def __init__(self, db_path: Path, export_path: Path) -> None:
        super().__init__(db_path, export_path, (Entry,))

    @classmethod
    def for_corpus(cls, tracker_path: Path) -> Calibration:
        return cls(default_db_path(), tracker_path / "calibration" / EXPORT_NAME)

    def entries(self) -> list[Entry]:
        return self.all(Entry)

    def get(self, ref: str) -> Entry:
        found = next((e for e in self.entries() if e.ref.upper() == ref.upper()), None)
        if found is None:
            raise EntryError(f"no entry {ref}")
        return found

    def log(self, *, owner: str, claim: str, p: float, resolves_by: str, test: str, made: str,
            source: str = "") -> tuple[Entry, list[str]]:
        """Add an entry. Returns it with any warnings about how its test is written."""
        missing = [name for name, value in (("claim", claim), ("resolves_by", resolves_by), ("test", test),
                                            ("owner", owner)) if not value.strip()]
        if missing:
            raise EntryError(f"an entry needs {', '.join(missing)}")
        if not 0.0 <= p <= 1.0:
            raise EntryError("p is a probability between 0 and 1")
        try:
            date.fromisoformat(resolves_by)
        except ValueError as e:
            raise EntryError("resolves_by must look like 2026-11-30") from e
        warnings = []
        if _SOURCE_FIGURE.search(test) and not _FALLBACK.search(test):
            warnings.append(
                "the test names a published figure and no fallback: say what settles it if the source "
                "omits the figure, or the entry may have to be voided"
            )
        numbers = [int(e.ref[1:]) for e in self.entries() if re.fullmatch(r"C\d+", e.ref)]
        entry = Entry(ref=f"C{max(numbers, default=0) + 1:03d}", owner=owner.strip(), made=made,
                      source=source.strip(), claim=claim.strip(), p=p, resolves_by=resolves_by,
                      test=test.strip())
        self.add(entry)
        return self.get(entry.ref), warnings

    def due(self, today: date) -> list[Entry]:
        """Open entries whose resolve-by date has arrived, soonest first."""
        return sorted(
            (e for e in self.entries() if e.is_open and e.resolves_by <= today.isoformat()),
            key=lambda e: (e.resolves_by, e.id),
        )

    def resolve(self, ref: str, *, outcome: int | None, on: date, void: str = "", note: str = "") -> Entry:
        """Record how an entry came out: 1, 0, or void with a reason. An entry resolves once."""
        entry = self.get(ref)
        if not entry.is_open:
            if entry.voided:
                state = f"voided ({entry.voided})"
            else:
                state = f"resolved {entry.outcome} on {entry.resolved}"
            raise EntryError(f"{entry.ref} is already {state}: a resolution is not edited")
        if void.strip():
            if outcome is not None:
                raise EntryError("an entry is voided or it has an outcome, not both")
            entry = entry.model_copy(update={
                "voided": void.strip(), "resolved": on.isoformat(), "note": note or entry.note,
            })
        elif outcome in (0, 1):
            entry = entry.model_copy(update={
                "outcome": outcome, "brier": round((entry.p - outcome) ** 2, 4),
                "resolved": on.isoformat(), "note": note or entry.note,
            })
        else:
            raise EntryError("an outcome is 1 (it happened) or 0 (it didn't); or void it with a reason")
        self.update(entry)
        return entry

    def scores(self) -> list[Score]:
        """One score per owner over their resolved entries, then one for everyone together."""
        resolved = [e for e in self.entries() if e.outcome is not None]
        owners = sorted({e.owner for e in resolved})
        return [_score(owner, [e for e in resolved if e.owner == owner]) for owner in owners] + (
            [_score("all", resolved)] if len(owners) > 1 else []
        )


def _score(owner: str, entries: list[Entry]) -> Score:
    buckets = []
    for i, (low, high) in enumerate(BUCKETS):
        last = i == len(BUCKETS) - 1
        inside = [e for e in entries if low <= e.p < high or (last and e.p == high)]
        buckets.append(Bucket(
            low, high, len(inside),
            round(sum(e.p for e in inside) / len(inside), 3) if inside else None,
            round(sum(e.outcome or 0 for e in inside) / len(inside), 3) if inside else None,
        ))
    brier = round(sum(e.brier or 0.0 for e in entries) / len(entries), 4) if entries else None
    return Score(owner, len(entries), brier, tuple(buckets))


# ---- proposing an outcome from a number the ledger can read ----------------------------------------

_COMPARE = re.compile(r"\bNAV\s*(<=|>=|<|>)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_BAND = re.compile(
    r"between\s*([+−-]?\d+(?:\.\d+)?)\s*%?\s*and\s*([+−-]?\d+(?:\.\d+)?)\s*%", re.IGNORECASE
)


def _number(text: str) -> float:
    return float(text.replace("−", "-"))


def propose(test: str, *, nav: float | None, pct: float | None) -> tuple[int, str] | None:
    """The outcome a NAV test implies, with the arithmetic, or None when the test isn't one of the
    two shapes this reads: `NAV < 41.20` (also >, <=, >=) against the struck NAV, and
    `between −0.60% and −0.30%` against the day's move. A proposal is for a person to confirm."""
    if (m := _COMPARE.search(test)) and nav is not None:
        op, level = m.group(1), float(m.group(2))
        holds = {"<": nav < level, ">": nav > level, "<=": nav <= level, ">=": nav >= level}[op]
        return int(holds), f"NAV {nav:.2f} {op} {level:.2f} is {'true' if holds else 'false'}"
    if (m := _BAND.search(test)) and pct is not None:
        low, high = sorted((_number(m.group(1)), _number(m.group(2))))
        holds = low <= pct <= high
        return int(holds), f"{pct:+.2f}% is {'inside' if holds else 'outside'} {low:+.2f}% to {high:+.2f}%"
    return None
