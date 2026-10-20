"""The console's scenario layer through the door: pack switching, the bundled-pack seal, and the
engine-owned dashboards (seed, save, reset). All state on tmp_path; the real app via TestClient."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from harness.bus.server import create_app
from harness.console import dashboards, scenario
from harness.console.api import console_routes
from harness.packs import bundled_packs_dir

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DEMO = bundled_packs_dir() / "demo-investor"
WRITES = frozenset({"set_active_pack", "save_dashboard", "reset_dashboard"})


@pytest.fixture()
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A 'real' corpus with a file the seal must never expose, plus sealed state/config/bus."""
    vault = tmp_path / "tracker"
    (vault / "finance").mkdir(parents=True)
    (vault / "finance" / "real-secret.md").write_text("# real holdings\n")
    monkeypatch.setenv("TRACKER_PATH", str(vault))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_BUS_DB", str(tmp_path / "bus.db"))
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "config"))
    return vault


def _client(writes: frozenset[str] = WRITES) -> TestClient:
    return TestClient(create_app(token=TOKEN, extra_routes=console_routes(TOKEN, writes=writes)))


def invoke(c: TestClient, cmd: str, args: dict | None = None):  # type: ignore[no-untyped-def]
    return c.post(f"/api/invoke/{cmd}", json=args or {}, headers=AUTH)


def test_writes_stay_gated_unless_the_serve_allows_them(real: Path) -> None:
    c = _client(writes=frozenset())
    assert invoke(c, "set_active_pack", {"pack": str(DEMO)}).status_code == 403
    assert invoke(c, "save_dashboard", {"dashboard": {"lane": "x"}}).status_code == 403
    # connection config is never writable from a browser, whatever the serve allows
    assert invoke(_client(), "set_bus_config").status_code == 403


def test_bundled_pack_seals_vault_dashboards_and_bus(real: Path, tmp_path: Path) -> None:
    c = _client()
    assert invoke(c, "set_active_pack", {"pack": str(DEMO)}).status_code == 200
    assert invoke(c, "get_active_pack").json() == str(DEMO)
    # the vault is the pack: the real corpus's file is unreachable, listed or read
    paths = {d["path"] for d in invoke(c, "list_vault_docs").json()}
    assert "finance/real-secret.md" not in paths and paths
    assert invoke(c, "read_doc", {"path": "finance/real-secret.md"}).status_code == 400
    # the pack's own dashboards replace the tab set, and nothing is seeded into the real config
    pack_lanes = {d["lane"] for d in dashboards.read_dir(DEMO / "dashboards")}
    assert {d["lane"] for d in invoke(c, "list_dashboards").json()} == pack_lanes
    assert not (tmp_path / "config" / "dashboards").exists()
    # the inbox reads the demo-scoped bus
    assert invoke(c, "get_config").json()["db_path"] == str(tmp_path / "state" / "demo-seal" / "bus.db")
    # pack dashboards are transient
    r = invoke(c, "save_dashboard", {"dashboard": {"lane": "finance", "widgets": []}})
    assert r.status_code == 400 and "demo pack" in r.json()["error"]


def test_spawn_env_seals_a_bundled_pack_and_blends_a_personal_one(real: Path, tmp_path: Path) -> None:
    scenario.set_active_pack(str(DEMO))
    env = scenario.spawn_env({"TRACKER_PATH": str(real)})
    seal = tmp_path / "state" / "demo-seal"
    assert env["TRACKER_PATH"] == str(DEMO) and env["WEIGHTS_PACK"] == str(DEMO)
    assert env["HARNESS_BUS_DB"] == str(seal / "bus.db") and env["HARNESS_CACHE_DIR"] == str(seal / "cache")
    personal = tmp_path / "mypack"
    (personal / "finance").mkdir(parents=True)
    scenario.set_active_pack(str(personal))
    env = scenario.spawn_env({"TRACKER_PATH": str(real)})
    assert env["TRACKER_PATH"] == str(real) and env["WEIGHTS_PACK"] == str(personal)
    assert "HARNESS_BUS_DB" not in env


def test_personal_pack_serves_its_lanes_and_falls_back_for_the_rest(real: Path, tmp_path: Path) -> None:
    personal = tmp_path / "mypack"
    (personal / "finance").mkdir(parents=True)
    (personal / "finance" / "note.md").write_text("# pack note\n")
    (real / "plans").mkdir()
    (real / "plans" / "p.md").write_text("# real plan\n")
    c = _client()
    invoke(c, "set_active_pack", {"pack": str(personal)})
    assert invoke(c, "read_doc", {"path": "finance/note.md"}).json() == "# pack note\n"
    # the pack provides finance/, so the real finance file is not reachable through it
    assert invoke(c, "read_doc", {"path": "finance/real-secret.md"}).status_code == 400
    assert invoke(c, "read_doc", {"path": "plans/p.md"}).json() == "# real plan\n"


def test_set_active_pack_refuses_a_dir_that_is_not_a_pack(real: Path, tmp_path: Path) -> None:
    c = _client()
    r = invoke(c, "set_active_pack", {"pack": str(tmp_path / "state")})
    assert r.status_code == 400 and "not a pack" in r.json()["error"]
    invoke(c, "set_active_pack", {"pack": str(DEMO)})
    assert invoke(c, "set_active_pack", {"pack": None}).status_code == 200
    assert invoke(c, "get_active_pack").json() is None


def test_set_active_pack_keeps_every_other_registry_key(real: Path, tmp_path: Path) -> None:
    reg = tmp_path / "config" / "bus-app.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"surfaces": [{"id": "s"}], "producers": ["p"]}))
    scenario.set_active_pack(str(DEMO))
    saved = json.loads(reg.read_text())
    assert saved["surfaces"] == [{"id": "s"}] and saved["producers"] == ["p"]


def test_save_banks_the_replaced_file_and_reset_restores_the_default(real: Path, tmp_path: Path) -> None:
    d = tmp_path / "config" / "dashboards"
    c = _client()
    invoke(c, "list_dashboards")  # seeds the defaults
    r = invoke(c, "save_dashboard", {"dashboard": {"lane": "finance", "title": "Mine", "widgets": []}})
    assert r.status_code == 200
    saved = json.loads((d / "finance.json").read_text())
    assert saved["owner"] == "user" and saved["title"] == "Mine"
    assert len(list((d / ".backups").glob("finance-*.json"))) == 1
    default = invoke(c, "reset_dashboard", {"lane": "finance"}).json()
    assert default["owner"] == "default" and json.loads((d / "finance.json").read_text()) == default


def test_save_rejects_a_lane_that_would_escape_the_dir(real: Path, tmp_path: Path) -> None:
    r = invoke(_client(), "save_dashboard", {"dashboard": {"lane": "../evil", "widgets": []}})
    assert r.status_code == 400 and "invalid lane" in r.json()["error"]
    assert not (tmp_path / "evil.json").exists()


def test_backups_keep_the_newest_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(1, 100))
    monkeypatch.setattr(dashboards.time, "time", lambda: next(clock))
    for i in range(13):
        dashboards.save_to(tmp_path, {"lane": "news", "title": str(i), "widgets": []})
    kept = sorted((tmp_path / ".backups").glob("news-*.json"))
    assert len(kept) == dashboards.BACKUPS_KEPT


def test_every_packaged_default_has_a_unique_lane() -> None:
    lanes = [d["lane"] for _, d in dashboards.packaged_defaults()]
    assert len(lanes) == len(set(lanes)) and len(lanes) >= 20


def test_demo_stage_points_every_knob_into_its_scratch_root(tmp_path: Path) -> None:
    from harness.console.demo import stage

    root, env, token = stage(DEMO)
    try:
        for key in ("TRACKER_PATH", "HARNESS_CONFIG_DIR", "HARNESS_STATE_DIR", "HARNESS_CACHE_DIR",
                    "HARNESS_BUS_DB"):
            assert Path(env[key]).is_relative_to(root), key
        assert not any((root / "corpus").iterdir())  # the empty floor: no corpus to fall back to
        assert json.loads((root / "config" / "bus-app.json").read_text()) == {"active_pack": str(DEMO)}
        assert (root / "config" / ".env").read_text().strip().startswith("#")  # no provider keys
        assert len(token) >= 24 and (root / "token").stat().st_mode & 0o777 == 0o600
    finally:
        import shutil

        shutil.rmtree(root)


def test_demo_mode_switches_between_sample_packs_only(
    real: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_CONSOLE_DEMO", "1")
    personal = tmp_path / "mypack"
    (personal / "finance").mkdir(parents=True)
    c = _client(writes=frozenset({"set_active_pack"}))
    for target in (str(personal), str(real), None):
        r = invoke(c, "set_active_pack", {"pack": target})
        assert r.status_code == 400 and "sample packs only" in r.json()["error"]
    assert invoke(c, "set_active_pack", {"pack": str(bundled_packs_dir() / "demo-growth")}).status_code == 200
    cfg = invoke(c, "get_config").json()
    assert cfg["demo"] is True and cfg["writes"] == ["set_active_pack"]


def test_packs_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HARNESS_PACKS_DIR", str(tmp_path))
    assert bundled_packs_dir() == tmp_path


def test_process_alive_tracks_a_real_process() -> None:
    import subprocess
    import sys

    from harness.bus.cli import _process_alive

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _process_alive(proc.pid)
    finally:
        proc.kill()
        proc.wait()
    assert not _process_alive(proc.pid)
