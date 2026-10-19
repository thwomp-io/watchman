"""The backup lane: overlay → config, the work tree (bundles / sqlite / tars + excludes), sealing
with age (skipped where age is absent, e.g. CI), retention, and the status read with an injectable
clock. Every test runs against tmp_path + a sealed state dir — never the operator's real files."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from harness.backup import service as svc
from harness.settings import BaseToolkitSettings

HAS_AGE = shutil.which("age") is not None
RECIPIENT = "age1ava7tpwlzwd53z5sq47crhluyjuqn398649nrcf78afef564wcjseclusf"  # a public key — safe to pin


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TRACKER_PATH", str(tmp_path / "tracker"))
    monkeypatch.delenv("WEIGHTS_PACK", raising=False)
    # a hook-scoped run (pre-push) must not leak its git env into the temp repos the test creates
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run = ["git", "-C", str(path)]
    subprocess.run([*run, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*run, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*run, "config", "user.name", "t"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run([*run, "add", "."], check=True)
    subprocess.run([*run, "commit", "-q", "-m", "init"], check=True)


def _cfg(tmp_path: Path, **over: object) -> svc.BackupConfig:
    base: dict[str, object] = dict(sync_dir=tmp_path / "sync", recipient=RECIPIENT, keep=2)
    base.update(over)
    return svc.BackupConfig(**base)  # type: ignore[arg-type]


# ————— config —————


def test_load_config_reads_the_overlay_and_expands_paths(sealed: Path) -> None:
    cfg_dir = sealed / "tracker" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "harness.yaml").write_text(
        "backup:\n  global_settings:\n    sync_dir: ~/Sync\n    recipient: " + RECIPIENT + "\n"
        "    keep: 3\n    repos: ['~/projects/x']\n    exclude: ['*.log']\n    sync_process: SyncApp\n"
    )
    cfg = svc.load_config(BaseToolkitSettings())
    assert cfg.sync_dir == Path("~/Sync").expanduser()
    assert cfg.snapshot_dir.name == "backup"
    assert cfg.keep == 3 and cfg.repos == [Path("~/projects/x").expanduser()]
    assert cfg.exclude == ["*.log"] and cfg.sync_process == "SyncApp"


def test_load_config_names_the_missing_keys(sealed: Path) -> None:
    with pytest.raises(svc.BackupConfigError, match="sync_dir, recipient"):
        svc.load_config(BaseToolkitSettings())  # the packaged template is all-null


def test_load_config_rejects_a_private_identity_as_recipient(sealed: Path) -> None:
    cfg_dir = sealed / "tracker" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "harness.yaml").write_text(
        "backup:\n  global_settings:\n    sync_dir: /tmp/s\n    recipient: AGE-SECRET-KEY-1XYZ\n"
    )
    with pytest.raises(svc.BackupConfigError, match="PUBLIC key"):
        svc.load_config(BaseToolkitSettings())


# ————— the work tree —————


def test_excluded_matches_basename_relpath_and_subtree() -> None:
    pats = [".beads/backup", "*.log", "engine-venv"]
    assert svc.excluded(".beads/backup/x.darc", pats)
    assert svc.excluded("logs/pulse.log", pats)
    assert svc.excluded("engine-venv/bin/python", pats)
    assert not svc.excluded(".beads/issues.jsonl", pats)
    assert not svc.excluded("notes/backup-plan.md", pats)


def test_slug_is_absolute_and_collision_safe() -> None:
    assert svc.slug_for(Path("/opt/app/etc/db")) == "opt__app__etc__db"
    assert svc.slug_for(Path("/a/x/db.sqlite")) != svc.slug_for(Path("/b/x/db.sqlite"))


def test_build_snapshot_bundles_copies_and_tars_with_excludes(
    sealed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(sealed)  # NOT a git checkout — the launchd cwd; bundle verify must not depend on cwd
    repo = sealed / "repo"
    _git_repo(repo)
    state = sealed / "statedir"
    (state / "keep").mkdir(parents=True)
    (state / "keep" / "a.json").write_text("{}")
    (state / "noise.log").write_text("x")
    (state / "engine-venv").mkdir()
    (state / "engine-venv" / "big").write_text("x" * 100)
    db = sealed / "live.db"
    with sqlite3.connect(db) as c:
        c.execute("create table t(v)")
        c.execute("insert into t values ('row')")
    cfg = _cfg(
        sealed,
        repos=[repo, sealed / "missing-repo"],
        dirs=[state],
        sqlite=[db],
        exclude=["*.log", "engine-venv"],
    )
    work = sealed / "work"
    work.mkdir()

    m = svc.build_snapshot(cfg, work, now=datetime(2026, 9, 7, 7, 0))

    kinds = sorted(e.kind for e in m.entries)
    assert kinds == ["dir", "repo", "sqlite"]
    assert any("missing-repo" in w for w in m.warnings)
    bundle = next(work.glob("repos/*.bundle"))
    subprocess.run(["git", "-C", str(repo), "bundle", "verify", str(bundle)], check=True, capture_output=True)
    with sqlite3.connect(next(work.glob("sqlite/*.sqlite"))) as c:
        assert c.execute("select v from t").fetchone() == ("row",)
    with tarfile.open(next(work.glob("dirs/*.tar"))) as tf:
        names = tf.getnames()
    assert "statedir/keep/a.json" in names
    assert not any(n.endswith("noise.log") or "engine-venv" in n for n in names)
    assert (work / "MANIFEST.txt").read_text().startswith("backup manifest")
    assert "HEAD" in next(e.note for e in m.entries if e.kind == "repo")


# ————— sealing + retention —————


@pytest.mark.skipif(not HAS_AGE, reason="age not installed")
def test_seal_and_land_writes_an_age_file_plus_sidecars(sealed: Path) -> None:
    work = sealed / "work"
    work.mkdir()
    (work / "MANIFEST.txt").write_text("backup manifest · test\n")
    cfg = _cfg(sealed)
    final = svc.seal_and_land(work, cfg, "2026-09-07-0700")
    assert final == cfg.snapshot_dir / "backup-2026-09-07-0700.tar.age"
    assert final.read_bytes().startswith(svc.AGE_HEADER)
    sha_side, man_side = svc.sidecars(final)
    assert sha_side.read_text().split()[0] == svc.sha256_of(final)
    assert man_side.read_text().startswith("backup manifest")
    assert not list(sealed.glob("backup-*.tar")), "plaintext tar must not survive the seal"


def test_prune_keeps_the_newest_and_drops_sidecars(tmp_path: Path) -> None:
    d = tmp_path / "backup"
    d.mkdir()
    for stamp in ("2026-08-01-0700", "2026-08-08-0700", "2026-08-15-0700"):
        (d / f"backup-{stamp}.tar.age").write_bytes(svc.AGE_HEADER)
        (d / f"backup-{stamp}.sha256").write_text("x")
        (d / f"backup-{stamp}.manifest.txt").write_text("x")
    removed = svc.prune(d, keep=2)
    assert {p.name for p in removed} == {
        "backup-2026-08-01-0700.tar.age",
        "backup-2026-08-01-0700.sha256",
        "backup-2026-08-01-0700.manifest.txt",
    }
    assert len(list(d.glob(svc.SNAPSHOT_GLOB))) == 2
    assert svc.prune(d, keep=0) == []


# ————— status —————


def _fake_snapshot(d: Path, stamp: str, *, good_sha: bool = True) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    snap = d / f"backup-{stamp}.tar.age"
    snap.write_bytes(svc.AGE_HEADER + b"payload")
    (d / f"backup-{stamp}.sha256").write_text(
        (svc.sha256_of(snap) if good_sha else "0" * 64) + f"  {snap.name}\n"
    )
    return snap


def test_status_is_fresh_and_verified(sealed: Path) -> None:
    cfg = _cfg(sealed, max_age_days=9)
    cfg.sync_dir.mkdir()
    snap = _fake_snapshot(cfg.snapshot_dir, "2026-09-07-0700")
    now = datetime.fromtimestamp(snap.stat().st_mtime).astimezone() + timedelta(days=1)
    st = svc.status(
        cfg, now=now, age_bin="age" if HAS_AGE else "git"
    )  # any resolvable binary satisfies the check
    assert st.newest is not None and st.newest.sha_ok and st.newest.header_ok
    assert st.ok, [c for c in st.checks if not c.ok]
    assert st.count == 1


def test_status_flags_stale_and_tampered(sealed: Path) -> None:
    cfg = _cfg(sealed, max_age_days=9)
    cfg.sync_dir.mkdir()
    snap = _fake_snapshot(cfg.snapshot_dir, "2026-08-01-0700", good_sha=False)
    now = datetime.fromtimestamp(snap.stat().st_mtime).astimezone() + timedelta(days=30)
    st = svc.status(cfg, now=now, age_bin="git")
    failed = {c.name for c in st.checks if not c.ok}
    assert any(n.startswith("newest snapshot fresh") for n in failed)
    assert "sha256 sidecar matches" in failed
    assert not st.ok
    stale = next(c for c in st.checks if c.name.startswith("newest snapshot fresh"))
    assert stale.fix  # every FAIL names its fix


def test_status_with_no_snapshot_points_at_the_verb(sealed: Path) -> None:
    cfg = _cfg(sealed)
    st = svc.status(cfg, now=datetime.now().astimezone(), age_bin="git")
    assert not st.ok
    assert any(c.name == "a snapshot exists" and c.fix == "hn backup snapshot" for c in st.checks)


def test_run_log_round_trip(sealed: Path) -> None:
    assert svc.last_run() is None
    svc._append_run_log({"ts": "2026-09-07T07:00:00", "status": "ok"})
    assert svc.last_run() == {"ts": "2026-09-07T07:00:00", "status": "ok"}
    assert svc.run_log_path().is_relative_to(sealed / "state")


# ————— the unattended run (launchd's minimal PATH) —————


def test_a_missing_tool_reaches_the_run_log_and_the_alert(
    sealed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preflight used to raise before the try: three launchd runs failed with no log line and
    no Inbox event. A missing tool is a logged, alerted failure like any other."""
    published: list[tuple[bool, str]] = []
    monkeypatch.setattr(svc, "_publish", lambda ok, title, _body, _stamp: published.append((ok, title)))
    with pytest.raises(svc.BackupConfigError):
        svc.run_backup(_cfg(sealed), notify=True, age_bin="no-such-age-binary")
    last = svc.last_run()
    assert last is not None and last["status"] == "fail" and "no-such-age-binary" in str(last["error"])
    assert published == [(False, "backup snapshot FAILED")]


@pytest.mark.skipif(not HAS_AGE, reason="age not installed")
def test_tools_resolve_under_a_launchd_minimal_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.paths import which_tool

    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")  # exactly what launchd hands a job
    assert shutil.which("age") is None or shutil.which("age", path="/usr/bin:/bin") is not None
    assert which_tool("age") is not None  # found via the tool dirs, not the process PATH
