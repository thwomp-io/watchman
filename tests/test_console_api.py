"""Web-console RPC door tests — directory-traversal containment (the protections are
load-bearing), the door's auth/gating semantics, and the wire-shape parity the frontend
depends on.

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
    # the escape target OUTSIDE the vault + a symlink pointing at it (a symlink resolving outside the vault)
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
    # incl. the Settings-panel writes: a served browser never rewrites the host's
    # bus wiring — connection config is the native console's to edit.
    for cmd in ("set_active_pack", "run_producer", "set_bus_config", "test_bus_connection"):
        assert invoke(client, cmd, headers=AUTH).status_code == 403


def test_get_config_carries_the_settings_read_model(client: TestClient) -> None:
    # the Rust get_config contract-mirror: served flavor = mode "served",
    # no bus_url to configure, token never present in any payload, host paths withheld.
    r = invoke(client, "get_config", headers=AUTH)
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["mode"] == "served"
    assert cfg["bus_url"] is None
    assert cfg["bus_token_set"] is False
    assert cfg["config_path"] is None
    assert "bus_token" not in cfg
    assert isinstance(cfg.get("surfaces"), list)
    assert isinstance(cfg.get("live_viz"), list)
    assert "tracker_path" in cfg


def test_get_user_overlay_is_served_read_only_text(client: TestClient) -> None:
    # the Personal tabs' source: raw yaml text, host path withheld; parsing is
    # the webview's job (one js-yaml parser client-side).
    r = invoke(client, "get_user_overlay", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["path"] is None
    assert isinstance(body["text"], str)
    assert body["source"] in ("user overlay", "packaged template")


def test_viz_commands_are_mirrored_as_of_phase_3(client: TestClient) -> None:
    # the door mirrors the read-only viz discover/sniff — an empty vault lists empty
    assert invoke(client, "list_viz", headers=AUTH).status_code == 200


def test_bus_routes_still_mounted_beside_the_door(client: TestClient) -> None:
    # the one-server property: /api/bus/* and /api/invoke/* answer from the same app
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
            {
                "id": "dynchart",
                "title": "Dynamic chart",
                "kind": "viz",
                "source": {
                    "type": "command", "cmd": "uv", "args": ["run", "hn", "finance", "bars"], "cwd": "",
                },
                "symbols": ["AAA"],
                "symbols_from": "portfolio",
            },
            {
                "id": "visitbank",
                "title": "Visit bank",
                "kind": "bank",
                "source": {
                    "type": "command", "cmd": "uv",
                    "args": ["run", "hn", "travel", "bank", "--visit", "{symbol}"], "cwd": "",
                },
                "symbols": [],
                "symbols_from": "visit",
            },
            {
                "id": "roles",
                "title": "Openings",
                "kind": "roles",
                "source": {
                    "type": "command", "cmd": "uv",
                    "args": ["run", "hn", "career", "roles", "--company", "{symbol}", "--json"], "cwd": "",
                },
                "symbols": [],
                "symbols_from": "company",
            },
        ],
    }
    (config_dir / "dashboards").mkdir(parents=True)
    (config_dir / "dashboards" / "finance.json").write_text(json.dumps(dash))


def test_dashboards_listed_from_config_dir(client: TestClient, tmp_path: Path) -> None:
    assert invoke(client, "list_dashboards", headers=AUTH).json() == []  # fresh machine → empty
    _write_dashboard(tmp_path / "config")
    (dash,) = invoke(client, "list_dashboards", headers=AUTH).json()
    assert dash["lane"] == "finance" and len(dash["widgets"]) == 6


def test_dashboards_passthrough_preserves_unknown_and_studio_keys(
    client: TestClient, tmp_path: Path
) -> None:
    # THE CONFIG-SCHEMA CONTRACT TEST, python half: this door is a
    # raw-json passthrough, so Studio keys (layout/owner) — and any future key — must survive
    # verbatim. The rust half (dash.rs kitchen_sink test) pins the typed-serde side; a field that
    # passes here but vanishes there means the two parsers (desktop Rust, Python web server) disagree.
    dash_dir = tmp_path / "config" / "dashboards"
    dash_dir.mkdir(parents=True, exist_ok=True)
    kitchen_sink = {
        "lane": "contract",
        "title": "Kitchen Sink",
        "owner": "user",
        "order": 7,
        "widgets": [
            {
                "id": "everything",
                "title": "Every field",
                "kind": "stat",
                "source": {"type": "command", "cmd": "uv", "args": ["run"], "cwd": "~"},
                "refresh": "market10m",
                "span": 3,
                "signed": False,
                "rows": 2,
                "layout": {"x": 1, "y": 2, "w": 3, "h": 4},
                "some_future_key": "must-survive",
            }
        ],
    }
    (dash_dir / "contract.json").write_text(json.dumps(kitchen_sink))
    (dash,) = invoke(client, "list_dashboards", headers=AUTH).json()
    assert dash["owner"] == "user" and dash["order"] == 7
    widget = dash["widgets"][0]
    assert widget["layout"] == {"x": 1, "y": 2, "w": 3, "h": 4}
    assert widget["signed"] is False
    assert widget["some_future_key"] == "must-survive"


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


def test_run_widget_symbols_from_uses_the_ticker_grammar(
    client: TestClient, tmp_path: Path
) -> None:
    # symbols_from widgets trade the closed list for a closed GRAMMAR: any
    # well-formed ticker passes validation (the spawn still fails later without a live engine —
    # 400 either way, but the ERROR distinguishes the paths); injection shapes are rejected
    # at the grammar.
    _write_dashboard(tmp_path / "config")
    for bad in ("zzz", "--help", "A B", "../x", "TOOLONGTICKR"):
        r = invoke(
            client, "run_widget", {"lane": "finance", "id": "dynchart", "symbol": bad},
            headers=AUTH,
        )
        assert r.status_code == 400
        assert "ticker grammar" in r.json()["error"], bad
    # a well-formed ticker clears the grammar (whatever the engine then does)
    r = invoke(
        client, "run_widget", {"lane": "finance", "id": "dynchart", "symbol": "ZZZ"},
        headers=AUTH,
    )
    assert "ticker grammar" not in str(r.json())


def test_run_widget_trip_picker_uses_the_slug_grammar(client: TestClient, tmp_path: Path) -> None:
    # a visit/trip picker widget admits vault slugs only: no flags, spaces, paths or uppercase
    _write_dashboard(tmp_path / "config")
    for bad in ("--help", "a b", "../x", "Sample", "-x", "x" * 81, "a/b"):
        r = invoke(
            client, "run_widget", {"lane": "finance", "id": "visitbank", "symbol": bad}, headers=AUTH,
        )
        assert r.status_code == 400
        assert "slug grammar" in r.json()["error"], bad
    for ok in ("auto", "2030-05-01-sample-visit"):
        r = invoke(
            client, "run_widget", {"lane": "finance", "id": "visitbank", "symbol": ok}, headers=AUTH,
        )
        assert "slug grammar" not in str(r.json()), ok


def test_run_widget_company_pick_uses_the_slug_grammar(client: TestClient, tmp_path: Path) -> None:
    # Career ▸ Openings follows the `company` scope: "auto" or a company slug, never the ticker grammar
    _write_dashboard(tmp_path / "config")
    for bad in ("--help", "Acme Cloud", "../x"):
        r = invoke(client, "run_widget", {"lane": "finance", "id": "roles", "symbol": bad}, headers=AUTH)
        assert r.status_code == 400 and "slug grammar" in r.json()["error"], bad
    for ok in ("auto", "acme-cloud"):
        r = invoke(client, "run_widget", {"lane": "finance", "id": "roles", "symbol": ok}, headers=AUTH)
        assert "grammar" not in str(r.json()), ok


def test_list_portfolio_symbols_reads_the_seed(client: TestClient) -> None:
    # in-process resolver: quotable holdings only, deduped, basis-desc — off whatever corpus
    # TRACKER_PATH points at (the fixture vault has no portfolio.yaml → the packaged NEUTRAL
    # template resolves → an EMPTY list, which is itself the honest fresh-install answer).
    r = invoke(client, "list_portfolio_symbols", headers=AUTH)
    assert r.status_code == 200
    # the BARE list — the door mirrors the native Vec<String> shape exactly (a {"symbols": …}
    # wrapper here made the webview silently fall back to the static list)
    assert isinstance(r.json(), list)


def test_app_config_reads_bus_app_json(client: TestClient, tmp_path: Path) -> None:
    # the native registry's REAL filename (config.rs config_path) — pins a past wrong-filename regression
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


# ————— the spawn cache (dedupe + TTL) ————————————————————————————————————————————————————


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


# ————— door defense-in-depth ————————————————————————————————————————————————


def test_read_doc_refuses_hidden_paths_and_non_text_types(client: TestClient, vault: Path) -> None:
    # Inside the vault, so the containment guard alone would have served every one of these.
    (vault / ".git").mkdir()
    (vault / ".git" / "config").write_text("[core]\n")
    (vault / ".beads").mkdir()
    (vault / ".beads" / "issues.jsonl").write_text("{}\n")
    (vault / "plans" / "tool").write_text("#!/bin/sh\n")
    for bad, why in (
        (".git/config", "hidden"),
        (".beads/issues.jsonl", "hidden"),
        ("plans/../.git/config", "hidden"),
        ("plans/tool", "no extension"),
        ("plans/pixel.png", "not a readable"),
    ):
        r = invoke(client, "read_doc", {"path": bad}, headers=AUTH)
        assert r.status_code == 400, bad
        assert why in r.json()["error"], bad
    # the allowed text types still read (json = the viz data twins the File widgets need)
    (vault / "plans" / "data.json").write_text('{"ok": true}')
    assert invoke(client, "read_doc", {"path": "plans/data.json"}, headers=AUTH).status_code == 200
    assert invoke(client, "read_doc", {"path": "plans/note.md"}, headers=AUTH).status_code == 200
    # the File-widget path rides the same gate
    _write_dashboard(vault.parent / "config")
    assert invoke(client, "run_widget", {"lane": "finance", "id": "note"}, headers=AUTH).status_code == 200


def test_list_events_limit_is_capped_at_the_bus_ceiling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, int] = {}
    orig = BusService.list_events

    def spy(self: BusService, **kw):  # type: ignore[no-untyped-def]
        seen["limit"] = kw["limit"]
        return orig(self, **kw)

    monkeypatch.setattr(BusService, "list_events", spy)
    assert invoke(client, "list_events", {"limit": 10000}, headers=AUTH).status_code == 200
    assert seen["limit"] == 500
    invoke(client, "list_events", {"limit": 7}, headers=AUTH)
    assert seen["limit"] == 7


def test_oversized_invoke_body_is_413(client: TestClient) -> None:
    big = b"x" * (64 * 1024 + 1)
    r = client.post("/api/invoke/read_doc", content=big, headers={**AUTH, "content-type": "application/json"})
    assert r.status_code == 413
    # a declared oversize is refused on the header alone (before the body is read)
    r = client.post(
        "/api/invoke/read_doc", content=b"{}",
        headers={**AUTH, "content-type": "application/json", "content-length": str(10**7)},
    )
    assert r.status_code == 413
    # and a normal small body is untouched by the cap
    assert invoke(client, "read_doc", {"path": "plans/note.md"}, headers=AUTH).status_code == 200


def test_spawn_cache_key_carries_the_active_pack(client: TestClient, tmp_path: Path) -> None:
    from harness.console.api import _spawn_key

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "bus-app.json"
    cfg.write_text(json.dumps({"active_pack": str(tmp_path / "packs" / "a")}))
    key_a = _spawn_key("hn", ["finance", "market", "--json"], "")
    cfg.write_text(json.dumps({"active_pack": str(tmp_path / "packs" / "b")}))
    key_b = _spawn_key("hn", ["finance", "market", "--json"], "")
    cfg.write_text(json.dumps({}))
    key_none = _spawn_key("hn", ["finance", "market", "--json"], "")
    assert len({key_a, key_b, key_none}) == 3, "a pack switch must be a different spawn identity"
    assert key_a[:-1] == key_b[:-1] == key_none[:-1]  # only the pack segment differs


def test_private_seam_lanes_pass_the_spawn_allowlist(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A lane mounted by harness.private (the personal branch) is spawnable through the door without
    editing the public allowlist; an unmounted lane still 400s."""
    from harness import cli
    from harness.console import api

    monkeypatch.setattr(cli, "PRIVATE_LANES", ["bespoke"])
    api._validate_tool("uv", ["run", "hn", "bespoke", "list", "--json"])
    with pytest.raises(api.CommandError, match="not allowlisted"):
        api._validate_tool("uv", ["run", "hn", "stranger", "list"])



def test_article_card_fetches_only_the_events_own_link(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.bus.models import EventDraft
    from harness.finance.providers import article_provider as ap

    monkeypatch.setenv("HARNESS_CACHE_DIR", str(tmp_path / "cache"))
    fetched: list[str] = []

    def fake_card(url: str, **_: object) -> dict[str, str]:
        fetched.append(url)
        return {"url": url, "image": "i"}

    monkeypatch.setattr(ap, "article_card", fake_card)
    svc = BusService()
    res, bare = svc.publish_many([
        EventDraft(producer="t", lane="finance", kind="catalyst", subject="ACME", title="ACME — x",
                   idempotency_key="t:a", payload={"url": "https://news.google.com/rss/articles/a",
                                                    "resolved_url": "https://pub.example/a"}),
        EventDraft(producer="t", lane="finance", kind="catalyst", subject="ACME", title="ACME — y",
                   idempotency_key="t:b", payload={"url": "javascript:alert(1)"}),
    ])
    svc.close()
    card = invoke(client, "article_card", {"id": res.event_id}, headers=AUTH).json()
    assert card == {"url": "https://pub.example/a", "image": "i"} and fetched == ["https://pub.example/a"]
    nolink = invoke(client, "article_card", {"id": bare.event_id}, headers=AUTH).json()
    assert "no article link" in nolink["error"] and fetched == ["https://pub.example/a"]  # never fetched
    assert invoke(client, "article_card", {"id": 99999}, headers=AUTH).status_code >= 400
    # the door takes an event id, never a URL
    assert invoke(client, "article_card", {"url": "https://evil.example"}, headers=AUTH).status_code >= 400


def test_uv_run_hn_resolves_the_installed_hn_on_the_augmented_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under launchd the serve's PATH is /usr/bin:/bin:… — the door must still find the installed
    `hn` (via the augmented path), never fall back to `uv run` in the source checkout (a different
    install, without the operator's private lanes)."""
    from typing import Any

    from harness.console import api

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hn = bin_dir / "hn"
    hn.write_text("#!/bin/sh\necho '{\"ok\": true}'\n")
    hn.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(api, "augmented_path", lambda p: f"{bin_dir}:{p}")
    seen: list[list[str]] = []
    real_run = api.subprocess.run

    def spy(argv: list[str], **kw: Any) -> Any:
        seen.append(list(argv))
        return real_run(argv, **kw)

    monkeypatch.setattr(api.subprocess, "run", spy)
    out = api._execute_json_command("uv", ["run", "hn", "career", "campaign", "--json"], "", "career")
    assert seen[0] == [str(hn), "career", "campaign", "--json"]
    assert "ok" in out


def test_visit_schedules_are_discovered_live_entries_newest_first(
    client: TestClient, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.console import api

    for slug, title in (("2030-05-01-sample", "Spring visit"), ("2030-09-01-sample", "Fall visit")):
        d = vault / "travel" / "visits" / slug
        (d / "visuals").mkdir(parents=True)
        (d / "schedule.json").write_text(json.dumps({"title": title, "items": []}))
    (vault / "travel" / "visits" / "2030-10-01-empty").mkdir()  # no schedule.json → no entry
    trip = vault / "travel" / "trips" / "2030-07-01-trip"
    trip.mkdir(parents=True)
    (trip / "schedule.json").write_text("{}")
    entries = invoke(client, "list_viz", headers=AUTH).json()
    sched = [e for e in entries if e["viz_type"] == "visit-schedule"]
    assert [e["path"] for e in sched] == [  # newest first across visits and trips
        "live:schedule/visit/2030-09-01-sample", "live:schedule/trip/2030-07-01-trip",
        "live:schedule/visit/2030-05-01-sample"]
    assert sched[0]["doc"] == "travel · LIVE" and sched[0]["title"] == "Fall visit"
    # listed once: never also as the raw schedule.json vault file
    # (the fixture vault's `sneaky` symlink re-lists files under other prefixes — out of scope here)
    raw = [p for p in (e["path"] for e in entries) if p.startswith("travel/") and p.endswith("schedule.json")]
    assert not raw, raw
    seen: list[list[str]] = []
    monkeypatch.setattr(api, "_run_json_command",
                        lambda cmd, args, cwd, label: seen.append(args) or '{"days": []}')
    r = invoke(client, "read_viz", {"path": "live:schedule/visit/2030-09-01-sample"}, headers=AUTH)
    assert r.status_code == 200
    assert seen[0] == ["run", "hn", "travel", "schedule", "--scope", "visit", "--visit",
                       "2030-09-01-sample", "--json"]
    for bad in ("live:schedule/visit/--help", "live:schedule/house/x", "live:schedule/visit/a b"):
        assert invoke(client, "read_viz", {"path": bad}, headers=AUTH).status_code == 400, bad
