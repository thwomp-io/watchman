"""Pocket agent — the stream parser, the store, the seal's rules, the door, and a full runner turn.

Fixture events mirror the live CLI's stream-json shapes (system/init, stream_event deltas, assistant
tool_use, user tool_result, rate_limit_event, result) with fictional values. The runner turn drives a
fake `claude` script, so no model, network or real state is touched.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from harness.bus.server import create_app
from harness.bus.service import BusService
from harness.console.api import console_routes
from harness.pocket import seal
from harness.pocket.config import PocketConfig
from harness.pocket.runner import Runner
from harness.pocket.store import PocketStore
from harness.pocket.stream import StreamState, consume, parse_line, tool_label

INIT = {"type": "system", "subtype": "init", "model": "claude-test-1", "permissionMode": "dontAsk",
        "claude_code_version": "9.9.9", "cwd": "/corpus", "session_id": "sess-1",
        "tools": ["Read", "Grep", "Glob", "Bash", "mcp__harness__finance_quote"],
        "mcp_servers": [{"name": "harness", "status": "connected"}]}
DELTA_START = {"type": "stream_event", "event": {"type": "message_start"}}


def delta(text: str) -> dict[str, Any]:
    return {"type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}


TOOL_USE = {"type": "assistant", "message": {
    "usage": {"input_tokens": 10, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 990,
              "output_tokens": 50},
    "content": [{"type": "tool_use", "id": "tu1", "name": "mcp__harness__finance_quote",
                 "input": {"symbols": ["ACME"]}}]}}
TOOL_RESULT = {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "tu1", "is_error": False,
     "content": [{"type": "text", "text": '{"symbol":"ACME","price":12.5}'}]}]}}
TEXT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ACME is $12.50."}]}}
RATE = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed", "rateLimitType": "five_hour",
    "unifiedWindows": {"five_hour": {"utilization": 0.42, "resetsAt": 2000000000},
                       "seven_day": {"utilization": 0.1, "resetsAt": 2000500000}}}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-1",
          "total_cost_usd": 0.12, "duration_ms": 3400, "num_turns": 2,
          "modelUsage": {"claude-test-1": {"contextWindow": 200000}},
          "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "touch x"}}]}


def run_stream(events: list[dict[str, Any]]) -> list[Any]:
    st = StreamState()
    out = []
    for ev in events:
        out.extend(consume(ev, st))
    return out


# ————— the stream parser ——————————————————————————————————————————————————————————————————————


def test_stream_turn_yields_entries_status_and_denials() -> None:
    effs = run_stream([INIT, DELTA_START, delta("ACME "), delta("is"), TOOL_USE, TOOL_RESULT, TEXT,
                       RATE, RESULT])
    kinds = [(e.kind, e.data.get("role")) for e in effs]
    assert ("entry", "tool") in kinds and ("entry", "result") in kinds and ("entry", "assistant") in kinds
    live = [e.data["text"] for e in effs if e.kind == "live"]
    assert live[:2] == ["ACME ", "ACME is"] and live[-1] == ""  # streamed, then cleared on the final text
    tool = next(e.data for e in effs if e.data.get("role") == "tool")
    assert tool["tool"] == "finance_quote" and tool["text"] == 'finance_quote({"symbols":["ACME"]})'
    result = next(e.data for e in effs if e.data.get("role") == "result")
    assert result["tool"] == "finance_quote" and result["ok"] is True and "12.5" in result["text"]
    status: dict[str, Any] = {}
    for e in effs:
        if e.kind == "status":
            status.update(e.data)
    assert status["model"] == "claude-test-1" and status["mcp"] == {"harness": "connected"}
    assert status["context_used"] == 10 + 4000 + 990 + 50 and status["context_max"] == 200000
    assert status["limits"]["five_hour"] == {"utilization": 0.42, "resets_at": 2000000000}
    assert status["limits"]["seven_day"]["utilization"] == 0.1
    assert status["turn_cost_usd"] == 0.12
    denied = [e.data for e in effs if e.kind == "denied"]
    assert denied == [{"tool": "Bash", "text": "touch x"}]
    assert effs[-1].kind == "done" and effs[-1].data["ok"] is True


def test_stream_error_result_and_long_tool_output() -> None:
    long = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "x", "is_error": True, "content": "e" * 5000}]}}
    effs = run_stream([long, {"type": "result", "is_error": True, "result": "boom"}])
    res = next(e.data for e in effs if e.data.get("role") == "result")
    assert res["ok"] is False and res["truncated"] is True and len(res["text"]) == 600
    assert any(e.data.get("role") == "error" and e.data["text"] == "boom" for e in effs)
    assert effs[-1].data["ok"] is False


def test_parse_line_skips_noise() -> None:
    assert parse_line("Warning: no stdin data received") is None
    assert parse_line("{not json") is None
    assert parse_line(json.dumps(INIT))["subtype"] == "init"  # type: ignore[index]


def test_tool_label_names_the_call() -> None:
    assert tool_label("Bash", {"command": "bd list --status open"}) == "bd list --status open"
    assert tool_label("Read", {"file_path": "/corpus/a.md"}) == "/corpus/a.md"
    assert tool_label("mcp__harness__bus_stats", {}) == "bus_stats"


# ————— the store ———————————————————————————————————————————————————————————————————————————————


def test_store_queue_flags_transcript_and_reset(tmp_path: Path) -> None:
    st = PocketStore(tmp_path / "pocket")
    a, b = st.enqueue("first"), st.enqueue("second")
    assert [q["text"] for q in st.queued()] == ["first", "second"]
    assert st.take()["id"] == a["id"] and st.take()["id"] == b["id"] and st.take() is None  # type: ignore[index]
    st.append({"role": "user", "text": "hi"})
    st.append({"role": "assistant", "text": "hello"})
    assert [e["seq"] for e in st.entries()] == [1, 2] and [e["seq"] for e in st.entries(since=1)] == [2]
    st.update_status(state="idle", actor="lane", session_id="s", cost_usd=1.0)
    st.request("stop")
    assert st.pop_flag("stop") and not st.pop_flag("stop")
    moved = st.archive_conversation()
    assert moved is not None and moved.exists() and not st.transcript.exists()
    kept = st.status()
    assert kept.get("actor") == "lane" and "session_id" not in kept and "cost_usd" not in kept


def test_store_snapshot_matches_contract_and_heartbeat(tmp_path: Path) -> None:
    st = PocketStore(tmp_path / "pocket")
    assert st.snapshot()["online"] is False
    st.beat()
    st.update_status(state="working", limits={"five_hour": {"utilization": 0.5, "resets_at": 1}},
                     unknown_key="dropped")
    snap = st.snapshot()
    assert snap["online"] is True and snap["status"]["state"] == "working"
    assert snap["status"]["limits"]["five_hour"]["utilization"] == 0.5
    assert "unknown_key" not in snap["status"]


# ————— the seal ————————————————————————————————————————————————————————————————————————————————


def cfg_for(tmp_path: Path, **kw: Any) -> PocketConfig:
    (tmp_path / "corpus").mkdir(exist_ok=True)
    return PocketConfig(cwd=tmp_path / "corpus", actor="lane", **kw)


def test_settings_doc_denies_secrets_in_both_forms_and_sandboxes(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = cfg_for(tmp_path, deny_read=[tmp_path / "extra"], network_domains=["api.example.test"])
    doc = seal.settings_doc(cfg)
    deny = doc["permissions"]["deny"]
    for p in (tmp_path / "cfg", tmp_path / "extra"):
        # `//abs` is the absolute form; a single leading slash would be settings-relative
        assert f"Read(/{p})" in deny and f"Read(/{p}/**)" in deny
        assert str(p) in doc["sandbox"]["filesystem"]["denyRead"]
    for t in ("Write", "Edit", "WebFetch", "Bash(git push *)", "Bash(bd close *)",
              "mcp__harness__finance_wire_read", "mcp__harness__bus_publish"):
        assert t in deny
    sb = doc["sandbox"]
    assert sb["enabled"] and sb["failIfUnavailable"] and not sb["autoAllowBashIfSandboxed"]
    assert "excludedCommands" not in sb  # an excluded command would also lose denyRead
    assert sb["network"] == {"allowedDomains": ["api.example.test"], "strictAllowlist": True}
    allow = doc["permissions"]["allow"]
    assert "mcp__harness__finance_quote" in allow and "mcp__harness__finance_wire_read" not in allow


def test_overlay_allowing_a_denied_mcp_tool_stays_denied(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path, mcp_tools=["finance_quote", "bus_publish"])
    allow = seal.settings_doc(cfg)["permissions"]["allow"]
    assert "mcp__harness__bus_publish" not in allow


def test_argv_and_env_are_sealed(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path, add_dirs=[tmp_path / "skills"])
    cmd = seal.argv(cfg, tmp_path / "s.json", mcp_path=tmp_path / "m.json", resume="sess-1")
    for flag in ("-p", "--restricted", "--strict-mcp-config", "--include-partial-messages"):
        assert flag in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob,Bash"
    assert cmd[cmd.index("--resume") + 1] == "sess-1" and "--session-id" not in cmd
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path / "skills")
    env = seal.env(cfg, {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t",
                         "CLAUDECODE": "1"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDECODE" not in env
    assert env["BEADS_ACTOR"] == "lane"


def test_verify_static_flags_each_hole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seal, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(seal, "which_tool", lambda name: f"/bin/{name}")
    cfg = cfg_for(tmp_path)
    cmd = seal.argv(cfg, tmp_path / "s.json")
    doc = seal.settings_doc(cfg)
    good = seal.verify_static(cfg, cmd, doc, seal.env(cfg, {"PATH": ""}), auth="claude.ai")
    assert good.ok, good.failures()
    bad_cmd = [a for a in cmd if a != "--restricted"]
    bad_env = {**seal.env(cfg, {"PATH": ""}), "ANTHROPIC_API_KEY": "k"}
    blanket = cfg.model_copy(update={"allow": ["Bash(*)"]})
    rep = seal.verify_static(blanket, bad_cmd, {"permissions": {"deny": []}, "sandbox": {}}, bad_env,
                             auth="api_key")
    failed = " | ".join(rep.failures())
    for hole in ("--restricted", "write tools denied", "secret paths denied", "sandbox enabled",
                 "blanket Bash", "API-billing key", "subscription auth"):
        assert hole in failed


# ————— the door —————————————————————————————————————————————————————————————————————————————————


@pytest.fixture()
def door(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_BUS_DB", str(tmp_path / "bus.db"))
    monkeypatch.setenv("TRACKER_PATH", str(tmp_path / "tracker"))
    (tmp_path / "tracker").mkdir()
    return TestClient(create_app(token="t", db_path=tmp_path / "bus.db", extra_routes=console_routes("t")))


def call(client: TestClient, cmd: str, args: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/invoke/{cmd}", json=args or {}, headers={"Authorization": "Bearer t"})


def test_door_send_queues_and_state_reads(door: TestClient, tmp_path: Path) -> None:
    r = call(door, "pocket_send", {"text": "  what's up\x07  "})
    assert r.status_code == 200 and r.json()["text"] == "what's up"  # control chars stripped
    snap = call(door, "pocket_state", {"since": 0}).json()
    assert snap["online"] is False and [q["text"] for q in snap["queued"]] == ["what's up"]
    assert call(door, "pocket_stop").json() is True
    assert (tmp_path / "state" / "pocket" / "stop").exists()


def test_door_send_refuses_bad_input_and_needs_the_token(door: TestClient) -> None:
    assert call(door, "pocket_send", {"text": "   "}).status_code == 400
    assert call(door, "pocket_send", {"text": "x" * 9000}).status_code == 400
    assert call(door, "pocket_send", {"text": 5}).status_code == 400
    assert call(door, "pocket_state", {"since": -1}).status_code == 400
    for _ in range(5):
        call(door, "pocket_send", {"text": "q"})
    assert call(door, "pocket_send", {"text": "one too many"}).status_code == 400
    assert door.post("/api/invoke/pocket_send", json={"text": "hi"}).status_code == 401


# ————— a full runner turn against a fake claude ——————————————————————————————————————————————————

FAKE = '''#!{python}
import json, sys, os
prompt = sys.stdin.read()
args = sys.argv[1:]
with open(os.environ["FAKE_ARGS"], "a") as f:
    f.write(json.dumps({{"args": args, "prompt": prompt, "actor": os.environ.get("BEADS_ACTOR"),
                        "api_key": "ANTHROPIC_API_KEY" in os.environ}}) + "\\n")
for ev in json.loads({events!r}):
    print(json.dumps(ev), flush=True)
'''


def test_runner_turn_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_BUS_DB", str(tmp_path / "bus.db"))
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-child")
    fake = tmp_path / "claude"
    fake.write_text(FAKE.format(python=sys.executable,
                                events=json.dumps([INIT, delta("hi"), TOOL_USE, TOOL_RESULT, TEXT, RATE,
                                                   RESULT])))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(seal, "claude_bin", lambda: str(fake))
    monkeypatch.setattr(seal, "auth_method", lambda env: "claude.ai")  # prepare() would spawn the fake
    store = PocketStore(tmp_path / "pocket")
    runner = Runner(cfg_for(tmp_path), store)
    runner.prepare()
    out = runner.run_turn("price of ACME?")
    assert out["ok"] is True
    roles = [e["role"] for e in store.entries()]
    assert roles == ["user", "tool", "result", "assistant", "denied"]
    status = store.status()
    assert status["session_id"] == "sess-1" and status["state"] == "idle" and status["turns"] == 1
    assert status["cost_usd"] == 0.12 and status["limits"]["five_hour"]["utilization"] == 0.42
    call1 = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[0])
    assert call1["prompt"] == "price of ACME?" and call1["actor"] == "lane" and call1["api_key"] is False
    assert "--session-id" in call1["args"]  # the first turn names a new session …
    runner.run_turn("and now?")
    call2 = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[1])
    assert call2["args"][call2["args"].index("--resume") + 1] == "sess-1"  # … later turns resume it
    svc = BusService(tmp_path / "bus.db")
    try:
        kinds = [e.kind for e in svc.list_events(lane="pocket", limit=50)]
    finally:
        svc.close()
    assert kinds.count("pocket_turn") == 2 and "pocket_denied" in kinds


# ————— sanctioned writes (the take writer) —————————————————————————————————————————————————————————


def test_take_writer_is_allowed_only_through_mcp_writes(tmp_path: Path) -> None:
    rule = "mcp__harness__finance_take_write"
    reads_only = cfg_for(tmp_path, mcp_tools=["finance_quote", "finance_take_write"])
    # listing it as a read is not granting it
    assert rule not in seal.settings_doc(reads_only)["permissions"]["allow"]
    granted = cfg_for(tmp_path, mcp_writes=["finance_take_write"])
    assert rule in seal.settings_doc(granted)["permissions"]["allow"]
    assert "mcp__harness__finance_research" in seal.settings_doc(granted)["permissions"]["deny"]


def test_verify_static_refuses_unsanctioned_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seal, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(seal, "which_tool", lambda name: f"/bin/{name}")
    bad = cfg_for(tmp_path, mcp_writes=["bus_publish"])
    rep = seal.verify_static(bad, seal.argv(bad, tmp_path / "s.json"), seal.settings_doc(bad),
                             seal.env(bad, {"PATH": ""}), auth="claude.ai")
    assert "only sanctioned write tools: bus_publish" in " | ".join(rep.failures())
    ok = cfg_for(tmp_path, mcp_writes=["finance_take_write"])
    rep = seal.verify_static(ok, seal.argv(ok, tmp_path / "s.json"), seal.settings_doc(ok),
                             seal.env(ok, {"PATH": ""}), auth="claude.ai")
    assert rep.ok, rep.failures()
    # a write rule smuggled into the allow list without mcp_writes is caught
    doc = seal.settings_doc(ok)
    doc["permissions"]["allow"].append("mcp__harness__bus_ack")
    rep = seal.verify_static(ok, seal.argv(ok, tmp_path / "s.json"), doc, seal.env(ok, {"PATH": ""}),
                             auth="claude.ai")
    assert "no write tool allowed outside mcp_writes: mcp__harness__bus_ack" in " | ".join(rep.failures())


def test_mcp_doc_sets_the_author_from_the_host(tmp_path: Path) -> None:
    doc = seal.mcp_doc(cfg_for(tmp_path))
    assert doc["mcpServers"]["harness"]["env"] == {"HARNESS_TAKE_AUTHOR": "lane"}


# ————— replay (the sample packs' scripted CHAT) ———————————————————————————————————————————————————

PACKS = Path(__file__).resolve().parents[2] / "samples" / "packs"


@pytest.mark.parametrize("pack", sorted(p.name for p in PACKS.iterdir() if (p / "pocket").is_dir()))
def test_every_pack_replay_script_loads(pack: str) -> None:
    from harness.pocket.replay import load_script

    data = load_script(PACKS / pack / "pocket" / "replay.json")
    assert data["turns"] and all(t.get("user") for t in data["turns"])


def test_replay_plays_a_turn_through_the_real_parser(tmp_path: Path) -> None:
    from harness.pocket.replay import Replayer

    script = {"actor": "demo", "model": "claude-test-1", "context_window": 200000,
              "limits": {"five_hour": 0.3, "seven_day": 0.1},
              "turns": [{"user": "q", "reply": "Answer **here**.", "context_used": 5000, "cost_usd": 0.2,
                         "steps": [{"tool": "mcp__harness__finance_quote", "input": {"symbols": ["X"]},
                                    "result": "{}"},
                                   {"tool": "Bash", "input": {"command": "touch x"}, "denied": True}]}]}
    store = PocketStore(tmp_path / "pocket")
    rp = Replayer(script, store, speed=1000)
    rp.start()
    rp.play("what's up?")
    roles = [e["role"] for e in store.entries()]
    assert roles == ["user", "tool", "result", "tool", "result", "assistant", "denied"]
    assert store.entries()[0]["text"] == "what's up?"  # the typed text, not the script's
    st = store.status()
    assert st["mode"] == "replay" and st["actor"] == "demo" and st["state"] == "idle"
    assert st["context_max"] == 200000 and st["limits"]["five_hour"]["utilization"] == 0.3
    assert st["turns"] == 1 and st["cost_usd"] == 0.2 and store.live() == ""
    assert PocketStore(tmp_path / "pocket").snapshot()["status"]["mode"] == "replay"


def test_replay_refuses_a_script_without_turns(tmp_path: Path) -> None:
    from harness.pocket.replay import load_script

    bad = tmp_path / "r.json"
    bad.write_text('{"turns": []}')
    with pytest.raises(ValueError, match="turns"):
        load_script(bad)
