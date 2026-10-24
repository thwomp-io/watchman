"""Size-capped copy-truncate rotation (`harness.logrotate`) and the serve's logging (no access lines by
default, a timestamp on every line, the harness's own warnings routed through it)."""

from __future__ import annotations

import gzip
import logging
import logging.config
from pathlib import Path
from typing import Any

import pytest

from harness import logrotate


@pytest.fixture
def pol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> logrotate.RotationPolicy:
    monkeypatch.setattr(logrotate, "overlay_get", lambda *a, **k: k.get("default"))
    return logrotate.RotationPolicy([str(tmp_path / "*.log")], cap_bytes=100, keep=2)


def test_under_the_cap_is_untouched(tmp_path: Path, pol: logrotate.RotationPolicy) -> None:
    (tmp_path / "a.log").write_text("small\n")
    assert logrotate.rotate(pol=pol) == []
    assert (tmp_path / "a.log").read_text() == "small\n"


def test_over_the_cap_gzips_then_truncates_in_place(tmp_path: Path, pol: logrotate.RotationPolicy) -> None:
    log = tmp_path / "a.log"
    log.write_text("x" * 150)
    inode = log.stat().st_ino
    assert logrotate.rotate(dry_run=True, pol=pol) == [str(log)] and log.stat().st_size == 150
    assert logrotate.rotate(pol=pol) == [str(log)]
    assert log.stat().st_size == 0 and log.stat().st_ino == inode  # same file, so the writer keeps it
    assert gzip.decompress((tmp_path / "a.log.1.gz").read_bytes()) == b"x" * 150


def test_an_append_writer_continues_at_the_start_with_no_hole(
    tmp_path: Path, pol: logrotate.RotationPolicy
) -> None:
    # launchd hands a service its log O_APPEND; this is that writer across a rotation
    log = tmp_path / "svc.log"
    with log.open("a") as writer:
        writer.write("old " * 50)
        writer.flush()
        logrotate.rotate(pol=pol)
        writer.write("new line\n")
        writer.flush()
    assert log.read_bytes() == b"new line\n"


def test_the_chain_shifts_and_stops_at_keep(tmp_path: Path, pol: logrotate.RotationPolicy) -> None:
    log = tmp_path / "a.log"
    for body in ("first", "second", "third"):
        log.write_text(body * 40)
        logrotate.rotate(pol=pol)
    assert gzip.decompress((tmp_path / "a.log.1.gz").read_bytes()).startswith(b"third")
    assert gzip.decompress((tmp_path / "a.log.2.gz").read_bytes()).startswith(b"second")
    assert not (tmp_path / "a.log.3.gz").exists()  # keep = 2
    assert [s.copies for s in logrotate.status(pol)] == [2]


def test_lowering_keep_trims_the_chain(tmp_path: Path, pol: logrotate.RotationPolicy) -> None:
    log = tmp_path / "a.log"
    for n in (1, 2, 3):
        (tmp_path / f"a.log.{n}.gz").write_bytes(gzip.compress(b"old"))
    log.write_text("x" * 150)
    logrotate.rotate_file(log, keep=1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.log", "a.log.1.gz"]


def test_globs_skip_rotated_copies_and_dedupe(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_text("x")
    (tmp_path / "a.log.1.gz").write_bytes(b"")
    pol = logrotate.RotationPolicy([str(tmp_path / "*"), str(tmp_path / "a.log")], 100, 2)
    assert [p.name for p in logrotate.matched(pol)] == ["a.log"]


def test_default_policy_is_the_state_dirs_run_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(logrotate, "overlay_get", lambda *a, **k: k.get("default"))
    pol = logrotate.policy()
    assert pol.globs == [str(tmp_path / "*.log")]
    assert pol.cap_bytes == int(logrotate.DEFAULT_CAP_MB * 1_048_576) and pol.keep == logrotate.DEFAULT_KEEP


def test_the_overlay_sets_paths_cap_and_keep(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {"paths": ["/var/log/x/*.log"], "cap_mb": 2, "keep": 3}
    monkeypatch.setattr(logrotate, "overlay_get", lambda *a, **k: cfg)
    pol = logrotate.policy()
    assert (pol.globs, pol.cap_bytes, pol.keep) == (["/var/log/x/*.log"], 2 * 1_048_576, 3)


# ————— hn bus serve's logging —————


def test_serve_logs_no_access_lines_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import uvicorn
    from typer.testing import CliRunner

    from harness.bus.cli import app

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("HARNESS_BUS_DB", str(tmp_path / "bus.db"))
    args = ["serve", "--host", "127.0.0.1", "--port", "1", "--token-file", str(tmp_path / "tok")]
    assert CliRunner().invoke(app, args).exit_code == 0
    assert seen["access_log"] is False
    assert CliRunner().invoke(app, [*args, "--access-log"]).exit_code == 0
    assert seen["access_log"] is True


def test_serve_lines_carry_a_timestamp_and_harness_warnings_ride_along(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from harness.bus.cli import serve_log_config

    cfg = serve_log_config()
    assert all(f["fmt"].startswith("%(asctime)s") for f in cfg["formatters"].values())
    logging.config.dictConfig(cfg)
    try:
        logging.getLogger("harness.bus.push").warning("web push to example failed (403)")
        err = capsys.readouterr().err
        assert "web push to example failed (403)" in err and err[:4].isdigit()  # dated
    finally:
        logging.getLogger("harness").handlers.clear()
        logging.getLogger("harness").propagate = True
