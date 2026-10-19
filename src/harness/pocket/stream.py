"""Parse `claude -p --output-format stream-json --verbose --include-partial-messages` into chat state.

Deterministic and model-free: one event in, a list of effects out. The runner applies the effects to
the store; tests feed recorded lines. Event shapes were taken from the live CLI, not only the docs —
the docs do not list `rate_limit_event`, which carries the plan's 5-hour and 7-day utilization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

EXCERPT = 600  # characters of a tool result kept in the transcript (the full text stays in the CLI's log)


@dataclass
class Effect:
    kind: str  # entry | live | status | session | denied | done
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamState:
    live: str = ""
    tool_names: dict[str, str] = field(default_factory=dict)  # tool_use_id → tool name
    context_used: int = 0


def tool_label(name: str, raw: Any) -> str:
    """One line naming what a tool call did — the "working… (N steps)" detail row."""
    args = raw if isinstance(raw, dict) else {}
    if name == "Bash":
        return str(args.get("command", ""))[:200]
    if name in ("Read", "Glob", "Grep"):
        target = args.get("file_path") or args.get("pattern") or args.get("path") or ""
        return str(target)[:200]
    short = name.split("__")[-1] if name.startswith("mcp__") else name
    compact = json.dumps(args, separators=(",", ":"), default=str) if args else ""
    return f"{short}({compact[:160]})" if compact else short


def short_tool(name: str) -> str:
    return name.split("__")[-1] if name.startswith("mcp__") else name


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _context_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def consume(ev: dict[str, Any], st: StreamState) -> list[Effect]:
    t = ev.get("type")
    out: list[Effect] = []
    if t == "system" and ev.get("subtype") == "init":
        mcp = {str(s.get("name")): str(s.get("status")) for s in ev.get("mcp_servers") or []
               if isinstance(s, dict)}
        out.append(Effect("status", {
            "model": ev.get("model"), "permission_mode": ev.get("permissionMode"),
            "claude_version": ev.get("claude_code_version"), "cwd": ev.get("cwd"),
            "tools": len(ev.get("tools") or []), "mcp": mcp,
        }))
        if ev.get("session_id"):
            out.append(Effect("session", {"id": ev["session_id"]}))
    elif t == "stream_event":
        inner = ev.get("event") or {}
        if inner.get("type") == "message_start":
            st.live = ""
        delta = inner.get("delta") or {}
        if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            st.live += str(delta.get("text", ""))
            out.append(Effect("live", {"text": st.live}))
    elif t == "assistant":
        msg = ev.get("message") or {}
        used = _context_tokens(msg.get("usage"))
        if used:
            st.context_used = used + int((msg.get("usage") or {}).get("output_tokens") or 0)
            out.append(Effect("status", {"context_used": st.context_used}))
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                st.live = ""
                out.append(Effect("live", {"text": ""}))
                out.append(Effect("entry", {"role": "assistant", "text": str(block["text"])}))
            elif block.get("type") == "tool_use":
                name = str(block.get("name", "tool"))
                st.tool_names[str(block.get("id", ""))] = name
                out.append(Effect("entry", {
                    "role": "tool", "tool": short_tool(name), "id": block.get("id"),
                    "text": tool_label(name, block.get("input")),
                }))
    elif t == "user":
        content = (ev.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = str(block.get("tool_use_id", ""))
                text = _text_of(block.get("content"))
                out.append(Effect("entry", {
                    "role": "result", "id": tid, "tool": short_tool(st.tool_names.get(tid, "")),
                    "ok": not bool(block.get("is_error")), "text": text[:EXCERPT],
                    "truncated": len(text) > EXCERPT,
                }))
    elif t == "rate_limit_event":
        info = ev.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        limits = {k: {"utilization": v.get("utilization"), "resets_at": v.get("resetsAt")}
                  for k, v in windows.items() if isinstance(v, dict)}
        if not limits and info.get("rateLimitType"):
            limits[str(info["rateLimitType"])] = {"utilization": info.get("utilization"),
                                                  "resets_at": info.get("resetsAt")}
        out.append(Effect("status", {"limits": limits, "limit_status": info.get("status")}))
    elif t == "result":
        model_usage = ev.get("modelUsage") or {}
        window = max((int(m.get("contextWindow") or 0) for m in model_usage.values()
                      if isinstance(m, dict)), default=0)
        status: dict[str, Any] = {
            "turn_cost_usd": ev.get("total_cost_usd"), "turn_duration_ms": ev.get("duration_ms"),
            "num_turns": ev.get("num_turns"),
        }
        if window:
            status["context_max"] = window
        out.append(Effect("status", status))
        for d in ev.get("permission_denials") or []:
            if isinstance(d, dict):
                name = str(d.get("tool_name", ""))
                out.append(Effect("denied", {"tool": short_tool(name),
                                             "text": tool_label(name, d.get("tool_input"))}))
        if ev.get("is_error"):
            out.append(Effect("entry", {"role": "error",
                                        "text": str(ev.get("result") or ev.get("subtype") or "error")}))
        if ev.get("session_id"):
            out.append(Effect("session", {"id": ev["session_id"]}))
        out.append(Effect("done", {"ok": not ev.get("is_error")}))
    return out


def parse_line(line: str) -> dict[str, Any] | None:
    """A stream line → event dict; None for blank or non-JSON lines (stderr noise, warnings)."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    return ev if isinstance(ev, dict) else None
