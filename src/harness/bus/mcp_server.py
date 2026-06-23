"""MCP server adapter (FastMCP) for the bus — thin over BusService. Tools pre-namespaced `bus_*`
(the root mount's prefix guard leaves them as-is). Read+publish+ack; delivery markers stay
transport-side (docs/BUS.md)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from harness.bus.models import EventDraft, Severity
from harness.bus.service import BusService

mcp = FastMCP("harness-bus")


@mcp.tool()
def bus_list(
    unread_only: bool = False,
    lane: str = "",
    kind: str = "",
    since: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List bus events newest-first (the standing agents' human-event layer). Filters: unread_only,
    lane (finance/career/...), kind, since (ISO lower bound), limit."""
    events = BusService().list_events(
        unread_only=unread_only, lane=lane or None, kind=kind or None, since=since or None,
        limit=limit,
    )
    return [e.model_dump() for e in events]


@mcp.tool()
def bus_ack(ids: list[int]) -> int:
    """Mark bus events read by ID (idempotent). Returns rows changed."""
    return BusService().ack(ids)


@mcp.tool()
def bus_stats() -> dict[str, Any]:
    """Bus health: totals, unread, by-lane/kind counts, db path + schema version."""
    return BusService().stats().model_dump()


@mcp.tool()
def bus_publish(
    title: str,
    lane: str,
    kind: str,
    subject: str = "",
    body: str = "",
    severity: str = "info",
    producer: str = "agent",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Publish one event to the bus (bus-side dedup via idempotency key — default
    producer:kind:subject:date gives once-per-day semantics)."""
    if severity not in ("info", "warn", "alert"):
        raise ValueError("severity must be info | warn | alert")
    sev: Severity = severity  # type: ignore[assignment]  # validated just above
    draft = EventDraft(
        producer=producer, lane=lane, kind=kind, subject=subject, title=title, body=body,
        severity=sev, idempotency_key=idempotency_key,
    )
    return BusService().publish(draft).model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
