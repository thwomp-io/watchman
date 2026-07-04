"""Map a PulseReport to bus EventDrafts. Pure — no I/O; frozen-fixture tested.

Severity is a kind-level judgment encoded once here (deterministic-core: the rule is stated, not
model-decided): trap/filing = act-worthy alerts; day/index moves = warn; calendar nearness = info.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from harness.bus.models import EventDraft, Severity

if TYPE_CHECKING:
    from harness.finance.models import NewsItem
    from harness.finance.watch import PulseReport

SEVERITY_BY_KIND: dict[str, Severity] = {
    "trap_proximity": "alert",
    "filing_drop": "alert",
    "day_move": "warn",
    "index_move": "warn",
    "macro_soon": "info",
    "print_soon": "info",
}


def _headline_key(symbol: str, basis: str) -> str:
    """Per-headline idempotency key: a stable short hash of the url (or title fallback), so the
    SAME catalyst never double-publishes but MULTIPLE distinct catalysts on one ticker the same day
    all surface — unlike the once-per-(kind,subject)-per-day default. The seen-cache delta-filters
    upstream; this is the durable cross-run belt (idempotent even if the cache resets)."""
    h = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]  # noqa: S324 — dedup id, not security
    return f"finance.catalyst:{symbol}:{h}"


def events_from_catalysts(
    items: list[NewsItem], ref_dirs: dict[str, str] | None = None
) -> list[EventDraft]:
    """Single-name catalyst headlines (the watch fresh-news delta) → bus events for the Inbox WIRE
    band (option B, 2026-06-30 — the doctrine-clean 'why is it moving' context layer beneath the
    act/watch alerts; the standing agent fetches headlessly, the Inbox just reads the bus).

    Scope = symbol-tagged single-name catalysts only (held/watchlist names; per-ticker gnews + Yahoo
    + broad-feed held-hits). Un-tagged macro/thesis wire (symbol == "") stays the News-tab deep-
    reader's job; SEC filings (source == "sec.gov") ride their own kind/band. kind=`catalyst`,
    severity=`info`. ``ref_dirs`` (symbol → research dir, existence-checked by the caller) adds the
    payload.ref deep-link."""
    refs = ref_dirs or {}
    drafts: list[EventDraft] = []
    for it in items:
        if not it.symbol or it.source == "sec.gov":
            continue
        payload: dict[str, object] = {
            "url": it.url, "source": it.source, "published": it.published,
        }
        ref_dir = refs.get(it.symbol)
        if ref_dir:
            payload["ref"] = {"zone": "vault", "dir": ref_dir}
        body = " · ".join(p for p in (it.source, it.published) if p)
        drafts.append(
            EventDraft(
                producer="finance.pulse",
                lane="finance",
                kind="catalyst",
                subject=it.symbol,
                title=f"{it.symbol} — {it.title}",
                body=body,
                payload=payload,
                severity="info",
                idempotency_key=_headline_key(it.symbol, it.url or it.title),
            )
        )
    return drafts


def events_from_pulse(
    rep: PulseReport, ref_dirs: dict[str, str] | None = None
) -> list[EventDraft]:
    """One EventDraft per flag. The idempotency key is left blank → the bus derives
    ``finance.pulse:{kind}:{symbol}:{date}`` — exactly the once-per-day-per-(kind,symbol)
    semantics previously enforced producer-side (the bus now owns it).

    ``ref_dirs`` (symbol → vault-relative research dir, existence-checked by the caller — this stays
    pure/no-I/O) adds a ``payload.ref`` deep-link the bus-app Inbox renders as a "go to →" jump.
    Absent symbol → no ref (no dead links)."""
    refs = ref_dirs or {}
    drafts: list[EventDraft] = []
    for flag in rep.flags:
        payload: dict[str, object] = {"flag": flag.model_dump(), "as_of": rep.as_of}
        order = next((o for o in rep.orders if o.symbol == flag.symbol), None)
        if order is not None:
            payload["order"] = order.model_dump()
        if flag.kind == "index_move":
            payload["indexes"] = [q.model_dump() for q in rep.indexes]
        ref_dir = refs.get(flag.symbol)
        if ref_dir:
            payload["ref"] = {"zone": "vault", "dir": ref_dir}
        drafts.append(
            EventDraft(
                producer="finance.pulse",
                lane="finance",
                kind=flag.kind,
                subject=flag.symbol,
                title=f"{flag.symbol} — {flag.kind.replace('_', ' ')}",
                body=flag.message,
                payload=payload,
                severity=SEVERITY_BY_KIND.get(flag.kind, "warn"),
            )
        )
    return drafts
