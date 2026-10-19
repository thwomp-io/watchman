"""`hn pocket replay` — a scripted, model-free conversation for demos and sample packs.

The replayer turns a script into the same stream-json events `claude -p` emits (init, text deltas,
tool calls and results, refusals, rate limits, the final result) and feeds them through the real
parser into the real store, so the CHAT tab renders exactly what a live runner would produce. Nothing
calls a model. The status carries `mode: replay`, and the console labels it as a scripted demo.

Two ways to drive it: answer whatever is typed into the composer with the next scripted turn, or
`--autoplay`, which posts the script's own user messages on a timer (hands-free, for recordings).

Script (JSON, a pack's `pocket/replay.json` by default):
  {"actor": "demo", "model": "claude-opus-5-5", "context_window": 1000000,
   "limits": {"five_hour": 0.18, "seven_day": 0.07},
   "turns": [{"user": "…", "reply": "markdown…", "context_used": 41000, "cost_usd": 0.3,
              "duration_ms": 14000,
              "steps": [{"tool": "mcp__harness__finance_quote", "input": {…}, "result": "…"},
                        {"tool": "Bash", "input": {"command": "…"}, "denied": true}]}]}
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from harness import __version__
from harness.pocket.store import PocketStore, now_iso
from harness.pocket.stream import StreamState, consume

REFUSAL = "Permission to use {tool} has been denied (read-only session)."
_RESET_IN = {"five_hour": 3 * 3600, "seven_day": 4 * 86400}


def default_script(tracker_path: Path) -> Path:
    return tracker_path / "pocket" / "replay.json"


def load_script(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("turns"), list) or not data["turns"]:
        raise ValueError(f"{path}: a replay script needs a non-empty `turns` list")
    for i, t in enumerate(data["turns"]):
        if not isinstance(t, dict) or not str(t.get("reply", "")).strip():
            raise ValueError(f"{path}: turn {i + 1} needs a `reply`")
    return data


def turn_events(script: dict[str, Any], turn: dict[str, Any],
                session: str) -> list[tuple[float, dict[str, Any]]]:
    """One scripted turn → (delay before, event) pairs, in the order the live CLI emits them."""
    out: list[tuple[float, dict[str, Any]]] = []
    tools = ["Read", "Grep", "Glob", "Bash"] + [s["tool"] for s in turn.get("steps", [])
                                                if str(s.get("tool", "")).startswith("mcp__")]
    out.append((0.3, {"type": "system", "subtype": "init", "model": script.get("model", "claude"),
                      "permissionMode": "dontAsk", "claude_code_version": "replay",
                      "cwd": script.get("cwd", "~/corpus"), "session_id": session, "tools": tools,
                      "mcp_servers": [{"name": "harness", "status": "connected"}]}))
    used = int(turn.get("context_used") or 30000)
    denials = []
    for n, step in enumerate(turn.get("steps", [])):
        tid = f"replay_{n}"
        name = str(step.get("tool", "Bash"))
        out.append((0.5, {"type": "assistant", "message": {
            "usage": {"input_tokens": 0, "cache_read_input_tokens": used, "output_tokens": 0},
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": step.get("input", {})}]}}))
        if step.get("denied"):
            denials.append({"tool_name": name, "tool_input": step.get("input", {})})
            text, err = REFUSAL.format(tool=name), True
        else:
            text, err = str(step.get("result", "")), not step.get("ok", True)
        out.append((0.7, {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": text}]}}))
    limits = script.get("limits") or {}
    out.append((0.1, {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed",
        "unifiedWindows": {k: {"utilization": v, "resetsAt": int(time.time()) + _RESET_IN.get(k, 86400)}
                           for k, v in limits.items()}}}))
    reply = str(turn["reply"])
    out.append((0.2, {"type": "stream_event", "event": {"type": "message_start"}}))
    words = reply.split(" ")
    for i in range(0, len(words), 3):
        chunk = " ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else "")
        out.append((0.06, {"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}}))
    out.append((0.1, {"type": "assistant", "message": {
        "usage": {"input_tokens": 0, "cache_read_input_tokens": used, "output_tokens": 400},
        "content": [{"type": "text", "text": reply}]}}))
    out.append((0.0, {"type": "result", "subtype": "success", "is_error": False, "session_id": session,
                      "total_cost_usd": turn.get("cost_usd", 0.2),
                      "duration_ms": turn.get("duration_ms", 12000),
                      "num_turns": len(turn.get("steps", [])) + 1,
                      "modelUsage": {script.get("model", "claude"):
                                     {"contextWindow": int(script.get("context_window") or 1_000_000)}},
                      "permission_denials": denials}))
    return out


class Replayer:
    def __init__(self, script: dict[str, Any], store: PocketStore | None = None, *,
                 speed: float = 1.0) -> None:
        self.script = script
        self.store = store or PocketStore()
        self.speed = max(0.05, speed)
        self.next_turn = 0

    def _sleep(self, s: float) -> None:
        if s > 0:
            time.sleep(s / self.speed)

    def start(self) -> None:
        st = self.store
        st.ensure()
        st.archive_conversation()
        st.update_status(state="idle", actor=self.script.get("actor", "pocket"), mode="replay",
                         harness_version=__version__, session_id=None,
                         seal={"ok": True, "checked_at": now_iso(), "failures": []})
        st.beat()

    def play(self, user_text: str) -> None:
        st = self.store
        turns = self.script["turns"]
        turn = turns[self.next_turn % len(turns)]
        self.next_turn += 1
        st.append({"role": "user", "text": user_text})
        session = st.status().get("session_id") or str(uuid.uuid4())
        st.update_status(state="working", turn_started_at=now_iso(),
                         cwd=self.script.get("cwd", "~/corpus"), branch="main", mode="replay")
        state = StreamState()
        for delay, ev in turn_events(self.script, turn, session):
            self._sleep(delay)
            st.beat()
            for eff in consume(ev, state):
                self._apply(eff)
        st.set_live("")
        st.update_status(state="idle")

    def _apply(self, eff: Any) -> None:
        st = self.store
        if eff.kind == "entry":
            st.append(eff.data)
        elif eff.kind == "denied":
            st.append({"role": "denied", **eff.data})
        elif eff.kind == "live":
            st.set_live(str(eff.data.get("text", "")))
        elif eff.kind == "session":
            st.update_status(session_id=eff.data["id"])
        elif eff.kind == "status":
            data = {k: v for k, v in eff.data.items() if v is not None}
            if "turn_cost_usd" in data:
                cur = st.status()
                data["cost_usd"] = round(float(cur.get("cost_usd") or 0)
                                         + float(data["turn_cost_usd"] or 0), 4)
                data["duration_ms"] = (int(cur.get("duration_ms") or 0)
                                       + int(data.get("turn_duration_ms") or 0))
                data["turns"] = int(cur.get("turns") or 0) + 1
            st.update_status(**data)

    def serve(self, *, autoplay: bool = False, gap: float = 4.0, once: bool = False) -> int:
        self.start()
        turns = self.script["turns"]
        last_beat = 0.0
        while True:
            if time.time() - last_beat > 5:
                self.store.beat()
                last_beat = time.time()
            if self.store.pop_flag("reset"):
                self.store.archive_conversation()
                self.next_turn = 0
            item = self.store.take()
            if item is None and autoplay:
                self._sleep(gap)
                item = {"text": str(turns[self.next_turn % len(turns)].get("user", ""))}
            if item:
                self.store.pop_flag("stop")
                self.play(str(item.get("text", "")))
                if once and self.next_turn >= len(turns):
                    return 0
                continue
            time.sleep(0.5)
