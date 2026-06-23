"""The `.env` resolution must find keys from ANY working directory (the global-CLI invariant) —
not just when run from the repo. Guards the candidate set behind BaseToolkitSettings."""

from __future__ import annotations

from pathlib import Path

from harness.settings import _env_files


def test_env_files_cover_config_repo_and_cwd() -> None:
    files = _env_files()
    # the portable user-config home (multi-device-friendly)
    assert str(Path.home() / ".config" / "harness" / ".env") in files
    # the repo-root .env (editable install — resolvable from any cwd)
    assert any(f.endswith("/.env") and "/.config/" not in f and f != ".env" for f in files)
    # the CWD fallback (running from the repo / legacy)
    assert ".env" in files
    # every candidate is absolute or the bare cwd name — never a surprise relative path
    assert all(f == ".env" or Path(f).is_absolute() for f in files)
