"""Print-scorecard registry reader — the deterministic data layer behind the Watchman PRINTS tab.

The registry (`finance/reference/print-scorecards.yaml`, tracker-resident; pack-overridable) is a
THIN machine projection appended by the operating agent at grade time — one entry per print event.
The rich record stays in the card/take markdown the entry points at (`card_doc` is the DocPopup
target). This module only loads, validates, and sorts; no synthesis, no model in the loop
(determinism doctrine).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The grade enum — the spellbook's banner colors key on these exactly.
GRADES = ("GREAT", "GOOD", "OK", "BAD", "DISASTER", "PENDING")


class ScorecardError(ValueError):
    """A malformed registry — raised with the offending entry named (fail loud, never guess)."""


@dataclass(frozen=True)
class Scorecard:
    symbol: str
    period: str
    print_date: _dt.date
    grade: str
    headline: str
    card_doc: str
    price_reaction: float | None = None
    held: bool = False
    key_facts: list[str] = field(default_factory=list)
    take_doc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "period": self.period,
            "print_date": self.print_date.isoformat(),
            "grade": self.grade,
            "headline": self.headline,
            "card_doc": self.card_doc,
            "price_reaction": self.price_reaction,
            "held": self.held,
            "key_facts": list(self.key_facts),
            "take_doc": self.take_doc,
        }


def _parse_entry(raw: dict[str, Any], idx: int) -> Scorecard:
    def need(key: str) -> Any:
        if key not in raw or raw[key] in (None, ""):
            raise ScorecardError(f"scorecards[{idx}]: missing required field '{key}'")
        return raw[key]

    grade = str(need("grade")).upper()
    if grade not in GRADES:
        raise ScorecardError(
            f"scorecards[{idx}] ({raw.get('symbol', '?')}): grade '{grade}' not in {GRADES}"
        )
    raw_date = need("print_date")
    if isinstance(raw_date, _dt.date):
        print_date = raw_date
    else:
        try:
            print_date = _dt.date.fromisoformat(str(raw_date))
        except ValueError as e:
            raise ScorecardError(
                f"scorecards[{idx}] ({raw.get('symbol', '?')}): bad print_date '{raw_date}'"
            ) from e
    reaction = raw.get("price_reaction")
    if reaction is not None and not isinstance(reaction, (int, float)):
        raise ScorecardError(
            f"scorecards[{idx}] ({raw.get('symbol', '?')}): price_reaction must be numeric or null"
        )
    facts = raw.get("key_facts") or []
    if not isinstance(facts, list) or not all(isinstance(f, str) for f in facts):
        raise ScorecardError(
            f"scorecards[{idx}] ({raw.get('symbol', '?')}): key_facts must be a list of strings"
        )
    return Scorecard(
        symbol=str(need("symbol")).upper(),
        period=str(need("period")),
        print_date=print_date,
        grade=grade,
        headline=str(need("headline")),
        card_doc=str(need("card_doc")),
        price_reaction=float(reaction) if reaction is not None else None,
        held=bool(raw.get("held", False)),
        key_facts=[str(f) for f in facts],
        take_doc=str(raw["take_doc"]) if raw.get("take_doc") else None,
    )


def load_scorecards(path: Path) -> list[Scorecard]:
    """Load + validate the registry. Newest print first (PENDING entries sort with their date,
    which is the *scheduled* print date — an upcoming card naturally leads the book)."""
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("scorecards")
    if entries is None:
        raise ScorecardError("registry has no top-level 'scorecards:' list")
    if not isinstance(entries, list):
        raise ScorecardError("'scorecards:' must be a list")
    cards = [_parse_entry(e, i) for i, e in enumerate(entries)]
    cards.sort(key=lambda c: (c.print_date, c.symbol), reverse=True)
    return cards


def to_contract(cards: list[Scorecard]) -> dict[str, Any]:
    """The dashboard/JSON contract: sorted cards + a by-grade summary for at-a-glance chips."""
    by_grade: dict[str, int] = {}
    for c in cards:
        by_grade[c.grade] = by_grade.get(c.grade, 0) + 1
    return {
        "scorecards": [c.to_dict() for c in cards],
        "summary": {"total": len(cards), "by_grade": by_grade},
    }
