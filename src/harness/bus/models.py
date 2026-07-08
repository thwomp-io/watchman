"""Pydantic models for the bus — the publish/read contract (spec: docs/BUS.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warn", "alert"]


class EventDraft(BaseModel):
    """Publish input. ``idempotency_key`` left blank derives ``producer:kind:subject:YYYY-MM-DD``
    (local date — matches the once-per-(kind,subject)-per-day semantics pulse established)."""

    producer: str
    lane: str
    kind: str
    subject: str = ""
    title: str
    body: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "info"
    idempotency_key: str = ""

    def resolved_key(self, day: str) -> str:
        return self.idempotency_key or f"{self.producer}:{self.kind}:{self.subject}:{day}"


class Event(BaseModel):
    """A stored event row. ``delivered_via`` markers are written by transports (the app appends
    "desktop" when it posts the native notification; a future ntfy transport appends its own —
    each transport writes ONLY its own marker)."""

    id: int
    created_at: str
    producer: str
    lane: str
    kind: str
    subject: str
    title: str
    body: str
    payload: dict[str, Any]
    severity: str
    read_at: str | None
    delivered_via: list[str]


class PublishResult(BaseModel):
    status: Literal["published", "duplicate"]
    event_id: int | None = None
    idempotency_key: str


class PushSubscription(BaseModel):
    """One browser/device push endpoint (the PushSubscription.toJSON() essentials + an operator
    label). Endpoint is the identity — a capability URL owned by the push service."""

    endpoint: str
    p256dh: str
    auth: str
    label: str = ""
    created_at: str = ""


class PushReport(BaseModel):
    """Outcome of one push fan-out. `skipped` names why nothing was attempted (gated severity,
    no subscriptions) — an honest answer for the test route, not an error."""

    sent: int = 0
    pruned: int = 0
    failed: int = 0
    skipped: str | None = None


class BusStats(BaseModel):
    total: int
    unread: int
    # alert+warn only — the badge count (severity doctrine; 0-default keeps old readers)
    urgent_unread: int = 0
    by_lane: dict[str, int]
    by_kind: dict[str, int]
    db_path: str
    db_bytes: int
    schema_version: str
