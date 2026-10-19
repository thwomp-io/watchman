"""The sealable cache root: HARNESS_CACHE_DIR redirects every provider cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.finance.watch import EarningsDateCache, SeenCache
from harness.paths import cache_file, harness_cache_dir


def test_cache_dir_defaults_to_home_and_honors_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("HARNESS_CACHE_DIR", raising=False)
    assert harness_cache_dir() == Path.home() / ".cache" / "harness"
    monkeypatch.setenv("HARNESS_CACHE_DIR", str(tmp_path / "sealed"))
    assert harness_cache_dir() == tmp_path / "sealed"
    assert cache_file("food", "x.json") == tmp_path / "sealed" / "food" / "x.json"


def test_seen_and_earnings_caches_land_under_the_sealed_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Resolved at call time: the env is set AFTER import, as a host sets it before a spawn.
    monkeypatch.setenv("HARNESS_CACHE_DIR", str(tmp_path / "sealed"))
    SeenCache().save()
    EarningsDateCache().save()
    assert (tmp_path / "sealed" / "news-seen.json").exists()
    assert (tmp_path / "sealed" / "earnings-dates.json").exists()
    assert not (Path.home() / ".cache" / "harness" / "sealed").exists()


def test_config_dir_defaults_to_home_and_honors_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from harness.paths import harness_config_dir

    monkeypatch.delenv("HARNESS_CONFIG_DIR", raising=False)
    assert harness_config_dir() == Path.home() / ".config" / "harness"
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "cfg"))
    assert harness_config_dir() == tmp_path / "cfg"


def test_no_stray_home_paths_outside_the_helpers() -> None:
    # The seal checklist from paths.py's docstring, as a test: every Path.home() in src/ is a settings
    # default, the state-dir helper, the cache helper, or the door's PATH augmentation.
    root = Path(__file__).resolve().parents[1] / "src" / "harness"
    # seal.py (pocket agent): its Path.home() calls name the operator's REAL secret dirs (~/.ssh,
    # ~/.aws, …) to DENY reads of them — the inverse of state, which must be sealable.
    allowed = {"settings.py", "paths.py", "store.py", "api.py", "seal.py"}
    hits = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "Path.home()" in p.read_text(encoding="utf-8") and p.name not in allowed
    ]
    assert not hits, f"cache/state paths must go through harness.paths / bus.store: {hits}"


def test_augmented_path_prepends_existing_tool_dirs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness import paths

    real = tmp_path / "bin"
    real.mkdir()
    monkeypatch.setattr(paths, "TOOL_DIRS", (str(real), str(tmp_path / "missing")))
    got = paths.augmented_path("/usr/bin:/bin")
    assert got == f"{real}:/usr/bin:/bin"  # existing dir first, missing dir skipped
    assert paths.augmented_path(got) == got  # already present → not duplicated


def test_which_tool_finds_a_binary_outside_the_process_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness import paths

    tool = tmp_path / "age"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setattr(paths, "TOOL_DIRS", (str(tmp_path),))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert paths.which_tool("age") == str(tool)
