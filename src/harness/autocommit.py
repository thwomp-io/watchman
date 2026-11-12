"""Commit what a scheduled run wrote into the corpus repository.

A standing agent that writes files (a morning report, a refreshed cache, a run log) otherwise leaves
the repository dirty until a person commits for it, and a dirty tree blocks that person's next
`git pull --rebase`.

The rules, which make this safe beside people and other agents working in the same checkout:

- Only the agent's own path is staged and committed (`git commit -- <path>`), so work another
  session has staged is never swept into the agent's commit.
- The push is fast-forward only and best-effort. An unattended run never pulls or rebases: the
  working tree is often dirty with someone's edits, and a rebase that stashes and fails to reapply
  would strand them. If the remote has moved, the commit stays local and goes out with the next
  interactive push. Nothing is ever forced.
- Git trouble is reported, never raised: the run that produced the files must not fail on the
  state of the repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from harness.paths import which_tool

_TIMEOUT_S = 120


@dataclass(frozen=True)
class CommitOutcome:
    note: str  # one line for the run log: "commit:ok(3) push:ok", "commit:clean", …
    committed: int = 0  # files in the commit; 0 when there was nothing to commit or it failed
    pushed: bool = False
    failed: bool = False  # git refused a step other than the push

    @property
    def needs_attention(self) -> bool:
        """Something a person should know about: a failed step, or a commit that didn't go out."""
        return self.failed or (self.committed > 0 and not self.pushed)


def commit_path(repo: Path, rel: str, message: str) -> CommitOutcome:
    """Commit everything under `rel` (relative to `repo`) with `message`, then try to push.

    `message` may contain `{n}`, replaced by the number of files committed.
    """
    git = which_tool("git") or "git"  # a scheduler's PATH may not include it

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, "-C", str(repo), *args], capture_output=True, text=True, timeout=_TIMEOUT_S
        )

    try:
        status = run("status", "--porcelain", "--", rel)
        if status.returncode != 0:
            reason = (status.stderr.strip() or "git status failed")[:80]
            return CommitOutcome(f"commit:skip ({reason})", failed=True)
        if not status.stdout.strip():
            return CommitOutcome("commit:clean")
        if run("add", "--", rel).returncode != 0:
            return CommitOutcome("commit:add-failed", failed=True)
        # Counted after the add: `status` shows a wholly untracked directory as one line.
        staged = run("diff", "--cached", "--name-only", "--", rel)
        n = len(staged.stdout.strip().splitlines()) or 1
        if run("commit", "-m", message.format(n=n), "--", rel).returncode != 0:
            return CommitOutcome("commit:commit-failed", failed=True)
        if run("push", "--quiet").returncode != 0:
            return CommitOutcome(
                f"commit:ok({n}) push:deferred (remote moved or unreachable — rides the next session push)",
                committed=n,
            )
        return CommitOutcome(f"commit:ok({n}) push:ok", committed=n, pushed=True)
    except Exception as e:  # noqa: BLE001 — see the module docstring: reported, never raised
        return CommitOutcome(f"commit:error ({str(e)[:80]})", failed=True)
