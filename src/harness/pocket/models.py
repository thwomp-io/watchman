"""The Chat tab's contract — what `pocket_state` returns (exported to the console's generated types)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PocketLimit(BaseModel):
    utilization: float | None = None  # 0.0-1.0 of the plan window used
    resets_at: int | None = None  # unix seconds


class PocketSeal(BaseModel):
    ok: bool = False
    checked_at: str | None = None
    failures: list[str] = Field(default_factory=list)


class PocketStatus(BaseModel):
    state: str | None = None  # idle | working | sealed_fail
    actor: str | None = None  # the lane name the runner answers as
    mode: str | None = None  # None/"live" = the sealed runner; "replay" = a scripted demo, no agent
    model: str | None = None
    effort: str | None = None
    context_used: int | None = None
    context_max: int | None = None
    limits: dict[str, PocketLimit] = Field(default_factory=dict)  # five_hour, seven_day, …
    limit_status: str | None = None  # allowed | allowed_warning | rejected
    cost_usd: float | None = None  # list-price estimate across the conversation (not a bill)
    turn_cost_usd: float | None = None
    duration_ms: int | None = None
    turns: int | None = None
    session_id: str | None = None
    started_at: str | None = None
    turn_started_at: str | None = None
    cwd: str | None = None
    branch: str | None = None
    claude_version: str | None = None
    harness_version: str | None = None
    mcp: dict[str, str] = Field(default_factory=dict)
    seal: PocketSeal = Field(default_factory=PocketSeal)
    updated_at: str | None = None


class PocketEntry(BaseModel):
    seq: int
    ts: str
    role: str  # user | assistant | tool | result | denied | error
    text: str = ""
    tool: str | None = None
    id: str | None = None
    ok: bool | None = None
    truncated: bool | None = None


class PocketQueued(BaseModel):
    id: str
    ts: str
    text: str


class PocketSnapshot(BaseModel):
    online: bool
    status: PocketStatus
    entries: list[PocketEntry]
    live: str
    queued: list[PocketQueued]
