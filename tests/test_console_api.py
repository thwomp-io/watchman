"""Web-console RPC door tests (phase 2) — the containment guard's tests PORTED WITH the guard
(the plan's explicit requirement: the directory-traversal protections are load-bearing), plus the
door's auth/gating semantics and the wire-shape parity the frontend depends on.

Same conventions as test_bus_server.py: the REAL app object through Starlette's TestClient, all
state on tmp_path via the standard env knobs (TRACKER_PATH / HARNESS_BUS_DB / HARNESS_CONFIG_DIR)
— no sockets, no real corpus, no ~/.config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from harness.bus.models import EventDraft
from harness.bus.server import create_app
from harness.bus.service import BusService
from harness.console.api import console_routes

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp corpus + sealed state/config dirs, wired through the standard env knobs."""
    vault = tmp_path / "tracker"
    (vault / "finance" / "market" / "takes").mkdir(parents=True)
    (vault / "finance" / "market" / "takes" / "2026-07-01-take.md").write_text("# Older take\n")
    (vault / "finance" / "market" / "takes" / "2026-07-03-take.md").write_text("# Newest take\n")
    (vault / "plans").mkdir()
    (vault / "plans" / "note.md").write_text("# A plan\nbody\n")
    (vault / "plans" / "pixel.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452")  # PNG magic + header start is plenty
    )
    (vault / "tmp").mkdir()
    (vault / "tmp" / "skipped.md").write_text("# never listed\n")
    # the escape target OUTSIDE the vault + a symlink pointing at it (the memory-symlink shape)
    outside = tmp_path / "outside.md"
    outside.write_text("# secret\n")
    (vault / "sneaky").symlink_to(tmp_path)
    monkeypatch.setenv("TRACKER_PATH", str(vault))
    monkeypatch.setenv("HARNESS_BUS_DB", str(tmp_path / "bus.db"))
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "config"))
    return vault


@pytest.fixture()
def client(vault: Path) -> TestClient:
    return TestClient(create_app(token=TOKEN, extra_routes=console_routes(TOKEN)))


def invoke(client: TestClient, cmd: str, args: dict | None = None, **kw):  # type: ignore[no-untyped-def]
    return client.post(f"/api/invoke/{cmd}", json=args or {}, **kw)


# ————— door semantics ——————————————————————————————————————————————————————————————————————————


def test_door_requires_bearer_token(client: TestClient) -> None:
    assert client.post("/api/invoke/unread_count", json={}).status_code == 401


def test_unknown_command_is_404(client: TestClient) -> None:
    assert invoke(client, "definitely_not_a_command", headers=AUTH).status_code == 404


def test_write_commands_are_gated_403(client: TestClient) -> None:
    for cmd in ("set_active_pack", "run_producer"):
        assert invoke(client, cmd, headers=AUTH).status_code == 403


def test_viz_commands_are_mirrored_as_of_phase_3(client: TestClient) -> None:
    # phase 2 gated these 501; phase 3 ported the discover/sniff — an empty vault lists empty
    assert invoke(client, "list_viz", headers=AUTH).status_code == 200


def test_bus_routes_still_mounted_beside_the_door(client: TestClient) -> None:
    # the D1 one-server property: /api/bus/* and /api/invoke/* answer from the same app
    assert client.get("/health").status_code == 200
    assert client.get("/api/bus/stats", headers=AUTH).status_code == 200


# ————— containment guard (ported WITH its protections) —————————————————————————————————————————


def test_read_doc_reads_a_vault_file(client: TestClient) -> None:
    r = invoke(client, "read_doc", {"path": "plans/note.md"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json().startswith("# A plan")


def test_dotdot_escape_is_rejected(client: TestClient) -> None:
    r = invoke(client, "read_doc", {"path": "../outside.md"}, headers=AUTH)
    assert r.status_code == 400
    assert "escapes the vault" in r.json()["error"]


def test_symlink_escape_is_rejected(client: TestClient) -> None:
    # resolves THROUGH the symlink to a canonical path outside the vault → must be refused
    r = invoke(client, "read_doc", {"path": "sneaky/outside.md"}, headers=AUTH)
    assert r.status_code == 400
    assert "escapes the vault" in r.json()["error"]


def test_read_image_returns_data_uri_and_rejects_unknown_types(client: TestClient) -> None:
    r = invoke(client, "read_image", {"path": "plans/pixel.png"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json().startswith("data:image/png;base64,")
    assert invoke(client, "read_image", {"path": "plans/note.md"}, headers=AUTH).status_code == 400


# ————— vault listings ——————————————————————————————————————————————————————————————————————————


def test_list_vault_docs_skips_symlinks_and_skip_dirs(client: TestClient) -> None:
    r = invoke(client, "list_vault_docs", headers=AUTH)
    assert r.status_code == 200
    paths = [d["path"] for d in r.json()]
    assert "plans/note.md" in paths and "plans/pixel.png" in paths
    assert not any(p.startswith(("tmp/", "sneaky/")) for p in paths)
    note = next(d for d in r.json() if d["path"] == "plans/note.md")
    assert note["title"] == "A plan" and note["kind"] == "doc" and note["area"] == "plans"


def test_list_vault_dir_is_newest_first_and_missing_dir_is_empty(client: TestClient) -> None:
    r = invoke(client, "list_vault_dir", {"path": "finance/market/takes"}, headers=AUTH)
    names = [d["name"] for d in r.json()]
    assert names == ["2026-07-03-take", "2026-07-01-take"]
    assert invoke(client, "list_vault_dir", {"path": "no/such/dir"}, headers=AUTH).json() == []


# ————— bus commands through the door (wire-shape parity) ———————————————————————————————————————


def test_list_events_carries_payload_json_string(client: TestClient) -> None:
    svc = BusService()
    svc.publish(
        EventDraft(producer="t", lane="finance", kind="day_move", title="x", payload={"a": 1})
    )
    svc.close()
    r = invoke(client, "list_events", {"unreadOnly": True, "limit": 10}, headers=AUTH)
    assert r.status_code == 200
    (event,) = r.json()
    # the webview contract (bus::Event / remote.rs): payload rides as the payload_json STRING
    assert "payload" not in event
    assert json.loads(event["payload_json"]) == {"a": 1}
    assert invoke(client, "unread_count", headers=AUTH).json() == 1
    assert invoke(client, "ack_events", {"ids": [event["id"]]}, headers=AUTH).json() == 1
    assert invoke(client, "unread_count", headers=AUTH).json() == 0
    meta = invoke(client, "distinct_meta", headers=AUTH).json()
    assert meta == {"lanes": ["finance"], "kinds": ["day_move"]}


# ————— config / dashboards / widgets ———————————————————————————————————————————————————————————


def _write_dashboard(config_dir: Path) -> None:
    dash = {
        "lane": "finance",
        "group": "Finance",
        "title": "Core",
        "widgets": [
            {
                "id": "note",
                "title": "Note",
                "kind": "doc",
                "source": {"type": "file", "path": "plans/note.md"},
                "symbols": [],
            },
            {
                "id": "evil",
                "title": "Evil",
                "kind": "stat",
                "source": {"type": "command", "cmd": "python3", "args": ["-c", "print(1)"], "cwd": ""},
                "symbols": [],
            },
            {
                "id": "chart",
                "title": "Chart",
                "kind": "viz",
                "source": {
                    "type": "command", "cmd": "uv", "args": ["run", "hn", "finance", "bars"], "cwd": "",
                },
                "symbols": ["AAA"],
            },
        ],
    }
    (config_dir / "dashboards").mkdir(parents=True)
    (config_dir / "dashboards" / "finance.json").write_text(json.dumps(dash))


def test_dashboards_listed_from_config_dir(client: TestClient, tmp_path: Path) -> None:
    assert invoke(client, "list_dashboards", headers=AUTH).json() == []  # fresh machine → empty
    _write_dashboard(tmp_path / "config")
    (dash,) = invoke(client, "list_dashboards", headers=AUTH).json()
    assert dash["lane"] == "finance" and len(dash["widgets"]) == 3


def test_run_widget_file_source_rides_the_guard(client: TestClient, tmp_path: Path) -> None:
    _write_dashboard(tmp_path / "config")
    r = invoke(client, "run_widget", {"lane": "finance", "id": "note"}, headers=AUTH)
    assert r.status_code == 200 and r.json().startswith("# A plan")


def test_run_widget_rejects_non_hn_commands(client: TestClient, tmp_path: Path) -> None:
    # belt-and-braces over config trust: even a config-declared widget can only spawn the engine
    _write_dashboard(tmp_path / "config")
    r = invoke(client, "run_widget", {"lane": "finance", "id": "evil"}, headers=AUTH)
    assert r.status_code == 400
    assert "not allowlisted" in r.json()["error"]


def test_run_widget_rejects_undeclared_symbols(client: TestClient, tmp_path: Path) -> None:
    _write_dashboard(tmp_path / "config")
    r = invoke(
        client, "run_widget", {"lane": "finance", "id": "chart", "symbol": "ZZZ"}, headers=AUTH
    )
    assert r.status_code == 400
    assert "not in widget config" in r.json()["error"]
    assert invoke(client, "run_widget", {"lane": "x", "id": "y"}, headers=AUTH).status_code == 400


def test_app_config_reads_bus_app_json(client: TestClient, tmp_path: Path) -> None:
    # the native registry's REAL filename (config.rs config_path) — pins the 0.77.0 wrong-name bug
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "bus-app.json").write_text(json.dumps({"surfaces": [{"id": "s", "label": "S"}]}))
    (surface,) = invoke(client, "list_surfaces", headers=AUTH).json()
    assert surface["id"] == "s"


def test_get_config_and_version_answer_server_side(client: TestClient) -> None:
    from harness import __version__

    assert invoke(client, "app_version", headers=AUTH).json() == __version__
    cfg = invoke(client, "get_config", headers=AUTH).json()
    assert cfg["bus_source"].startswith("served: ") and cfg["producers"] == []


def test_packs_answer_honestly_empty(client: TestClient) -> None:
    assert invoke(client, "list_packs", headers=AUTH).json() == []
    assert invoke(client, "get_active_pack", headers=AUTH).json() is None


# ————— phase 3: viz mirrors + multi-console UI mounts ——————————————————————————————————————————


def test_list_viz_discovers_colocated_json_and_sniffs_types(client: TestClient, vault: Path) -> None:
    d = vault / "finance" / "research"
    (d / "visuals").mkdir(parents=True)
    (d / "bench.json").write_text(json.dumps({"title": "Bench", "axes": [], "rows": []}))
    (d / "trend.json").write_text(json.dumps({"series": [{"points": []}]}))
    (vault / "plans" / "orphan.json").write_text("{}")  # no visuals/ sibling → not a candidate
    entries = invoke(client, "list_viz", headers=AUTH).json()
    by_name = {e["name"]: e for e in entries}
    assert by_name["bench"]["viz_type"] == "matrix" and by_name["bench"]["supported"]
    assert by_name["trend"]["viz_type"] == "line" and by_name["bench"]["title"] == "Bench"
    assert "orphan" not in by_name


def test_read_viz_rides_the_guard_and_knows_live_ids(client: TestClient, vault: Path) -> None:
    d = vault / "plans" / "visuals"
    d.mkdir()
    (vault / "plans" / "flow.json").write_text('{"nodes": [], "links": []}')
    r = invoke(client, "read_viz", {"path": "plans/flow.json"}, headers=AUTH)
    assert r.status_code == 200 and json.loads(r.json()) == {"nodes": [], "links": []}
    assert invoke(client, "read_viz", {"path": "live:nope"}, headers=AUTH).status_code == 400
    assert invoke(client, "read_viz", {"path": "../outside.md"}, headers=AUTH).status_code == 400


def test_ui_mounts_serve_default_and_named_variants(vault: Path, tmp_path: Path) -> None:
    from harness.console.api import console_routes, ui_mounts

    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html>default console</html>")
    (tmp_path / "next").mkdir()
    (tmp_path / "next" / "index.html").write_text("<html>ab candidate</html>")
    specs = [str(tmp_path / "dist"), f"next={tmp_path / 'next'}"]
    app = create_app(token=TOKEN, extra_routes=[*console_routes(TOKEN), *ui_mounts(specs)])
    c = TestClient(app)
    assert "default console" in c.get("/").text
    assert "ab candidate" in c.get("/ui/next/").text
    # the API still wins over the root mount (route order: door before static)
    assert c.post("/api/invoke/unread_count", json={}).status_code == 401
    assert c.get("/health").status_code == 200


def test_ui_mounts_reject_two_root_consoles(tmp_path: Path) -> None:
    from harness.console.api import ui_mounts

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(ValueError, match="only one bare DIR"):
        ui_mounts([str(tmp_path / "a"), str(tmp_path / "b")])


# ————— o992: the spawn cache (dedupe + TTL) ————————————————————————————————————————————————————


def test_cached_spawn_dedupes_concurrent_and_serves_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading as th
    import time as _time

    from harness.console import api

    monkeypatch.setattr(api, "_spawn_cache", {})
    monkeypatch.setattr(api, "_inflight", {})
    calls = {"n": 0}

    def slow() -> str:
        calls["n"] += 1
        _time.sleep(0.15)
        return "payload"

    results: list[str] = []
    threads = [
        th.Thread(target=lambda: results.append(api._cached_spawn(("k",), slow))) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["payload"] * 4
    assert calls["n"] == 1  # four concurrent callers, ONE subprocess

    assert api._cached_spawn(("k",), slow) == "payload"
    assert calls["n"] == 1  # warm TTL hit, no respawn
    api._spawn_cache[("k",)] = (0.0, "stale")  # force expiry
    assert api._cached_spawn(("k",), slow) == "payload"
    assert calls["n"] == 2  # expired → re-executed


def test_cached_spawn_never_caches_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.console import api

    monkeypatch.setattr(api, "_spawn_cache", {})
    monkeypatch.setattr(api, "_inflight", {})
    calls = {"n": 0}

    def boom() -> str:
        calls["n"] += 1
        raise api.CommandError("nope")

    for _ in range(2):
        with pytest.raises(api.CommandError):
            api._cached_spawn(("bad",), boom)
    assert calls["n"] == 2  # a failure is retried next call, never served warm
