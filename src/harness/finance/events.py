"""Map a PulseReport to bus EventDrafts. Pure — no I/O; frozen-fixture tested.

Severity is a kind-level judgment encoded once here (deterministic-core: the rule is stated, not
model-decided): trap/filing = act-worthy alerts; day/index moves = warn; calendar nearness = info.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.bus.models import EventDraft, Severity

if TYPE_CHECKING:
    from harness.finance.watch import PulseReport

SEVERITY_BY_KIND: dict[str, Severity] = {
    "trap_proximity": "alert",
    "filing_drop": "alert",
    "day_move": "warn",
    "index_move": "warn",
    "macro_soon": "info",
    "print_soon": "info",
}


def events_from_pulse(
    rep: PulseReport, ref_dirs: dict[str, str] | None = None
) -> list[EventDraft]:
    """One EventDraft per flag. The idempotency key is left blank → the bus derives
    ``finance.pulse:{kind}:{symbol}:{date}`` — exactly the once-per-day-per-(kind,symbol)
    semantics pulse-flags.json enforced producer-side (which Phase 3 retires).

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
