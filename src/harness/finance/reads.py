"""JSON reads shared by a CLI verb's `--json` and its MCP twin — one payload shape, two skins.

Each function is the deterministic gather behind a market-take step: the follow-the-sun board
(`global`), the macro calendar (`calendar`), and the article behind a wire headline matched by text
(`wire read --match`). Interpretation stays with the agent.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict
from typing import Any

from harness.errors import ProviderError


def global_payload(sections: list[str] | None = None) -> dict[str, Any]:
    """`hn finance global --json`: index futures, rates + the Treasury cash curve, Asia/Europe, oil,
    metals, FX. A Treasury outage is a note, never a failed read; unknown sections raise."""
    from harness.finance.providers.global_provider import (
        GLOBAL_RAILS,
        rail_scalars,
        rail_sessions,
        resolve_rails,
    )
    from harness.finance.providers.treasury_provider import curve_scalars, fetch_yield_curve

    bad = [s for s in (sections or []) if s not in GLOBAL_RAILS]
    if bad:
        raise ProviderError(f"unknown section(s): {', '.join(bad)} (valid: {', '.join(GLOBAL_RAILS)})")
    resolved = resolve_rails(sections or None)
    curve, curve_note = None, ""
    if not sections or "rates" in sections:
        try:
            curve = fetch_yield_curve()
        except ProviderError as e:
            curve_note = f"treasury curve unavailable: {e}"
    scalars = rail_scalars(resolved.rails)
    if curve is not None:
        scalars.update(curve_scalars(curve))
    return {
        "as_of": _dt.datetime.now(_dt.UTC).isoformat(),
        "scalars": scalars,
        "sessions": rail_sessions(resolved.rails),
        "rails": {sec: [asdict(q) for q in cards] for sec, cards in resolved.rails.items()},
        "curve": curve.model_dump() if curve is not None else None,
        "notes": resolved.notes + ([curve_note] if curve_note else []),
    }


def calendar_payload(days: int = 14, show_all: bool = False, refresh: bool = False) -> dict[str, Any]:
    """`hn finance calendar --json`: releases (actual/consensus once printed), FOMC, auctions."""
    from harness.finance.calendar import build_calendar

    cal = build_calendar(days, refresh=refresh)
    events = [e for e in cal.events if show_all or e.tier == "high" or e.source == "treasury"]
    return {"start": cal.start, "days": cal.days, "notes": cal.notes,
            "events": [{**asdict(e), "label": e.label} for e in events]}


def wire_read_match(match: str, max_chars: int = 6000) -> dict[str, Any]:
    """The body behind the NEWEST wire headline containing `match` — the article must already be on the
    configured feeds, so this reaches no URL the caller supplies (unlike reading an arbitrary link)."""
    from harness.finance.providers.article_provider import read_article
    from harness.finance.service import FinanceService

    needle = match.strip().lower()
    if not needle:
        raise ProviderError("match text is empty")
    hits = [it for it in FinanceService().wire(limit=8).items if needle in it.title.lower() and it.url]
    if not hits:
        raise ProviderError(f"no wire headline contains {match!r} (the wire keeps each feed's 8 newest)")
    art = asdict(read_article(hits[0].url))
    if max_chars and isinstance(art.get("body"), str) and len(art["body"]) > max_chars:
        art["body"] = art["body"][:max_chars]
        art["truncated"] = True
    return {"matched": {"source": hits[0].source, "published": hits[0].published, "title": hits[0].title},
            "also": [{"source": h.source, "published": h.published, "title": h.title} for h in hits[1:4]],
            "article": art}
