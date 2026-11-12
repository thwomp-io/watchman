"""The shared commit step for scheduled runs, against real throwaway repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.autocommit import commit_path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _repo(tmp_path: Path, *, remote: bool) -> Path:
    root = tmp_path / "corpus"
    (root / "out").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "README.md").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    if remote:
        bare = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        _git(root, "remote", "add", "origin", str(bare))
        _git(root, "push", "-q", "-u", "origin", "main")
    return root


def test_commits_only_its_own_path_and_pushes(tmp_path: Path) -> None:
    root = _repo(tmp_path, remote=True)
    (root / "out/a.json").write_text("{}\n")
    (root / "out/b.json").write_text("{}\n")
    (root / "someone-elses.md").write_text("in progress\n")
    _git(root, "add", "someone-elses.md")  # another session's staged work

    outcome = commit_path(root, "out", "agent: run ({n} file(s))")

    assert (outcome.committed, outcome.pushed, outcome.needs_attention) == (2, True, False)
    assert _git(root, "log", "-1", "--format=%s").stdout.strip() == "agent: run (2 file(s))"
    committed = _git(root, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["out/a.json", "out/b.json"]
    assert "A  someone-elses.md" in _git(root, "status", "--porcelain").stdout  # still staged, not ours
    assert _git(root, "rev-parse", "HEAD").stdout == _git(root, "rev-parse", "origin/main").stdout


def test_an_unpushable_commit_stays_local_and_asks_for_attention(tmp_path: Path) -> None:
    root = _repo(tmp_path, remote=False)
    (root / "out/a.json").write_text("{}\n")

    outcome = commit_path(root, "out", "agent: run")

    assert outcome.committed == 1 and not outcome.pushed and outcome.needs_attention
    assert "push:deferred" in outcome.note
    assert commit_path(root, "out", "agent: run").note == "commit:clean"  # nothing left to do


def test_a_directory_that_is_not_a_repository_is_reported_not_raised(tmp_path: Path) -> None:
    outcome = commit_path(tmp_path, "out", "agent: run")
    assert outcome.failed and outcome.note.startswith("commit:skip")
