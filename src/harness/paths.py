"""Sealable filesystem roots — the env knobs a host sets to redirect state.

Every root the engine reads or writes OUTSIDE the corpus has one env override, so a sandboxed /
demo-sealed instance never touches the operator's real files: `TRACKER_PATH` (corpus) ·
`HARNESS_STATE_DIR` (pulse logs, bus db — `bus.store.harness_state_dir`) · `HARNESS_BUS_DB` ·
`HARNESS_CONFIG_DIR` (.env, VAPID keys) · and, from this module, **`HARNESS_CACHE_DIR`** for the
provider caches (news seen-cache, earnings dates, ratings consensus, hotel/food sweeps, contact
sheets). Why the cache needed its own knob: under a demo seal the seen-cache was still the REAL
one, so a demo `watch` marked real headlines seen and the next real pulse missed them. The host
(commands.rs `tool_command`) passes every knob on every spawn; the checklist is
`grep -rn "Path.home()" src/` — only settings defaults and these helpers may hit.
"""

from __future__ import annotations

import os
from pathlib import Path


def harness_cache_dir() -> Path:
    """`HARNESS_CACHE_DIR` if set (expanded), else `~/.cache/harness`. Resolved at CALL time so a
    test or a host that sets the env after import still lands in the sealed dir."""
    env = os.environ.get("HARNESS_CACHE_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".cache" / "harness"


def cache_file(*parts: str) -> Path:
    """`harness_cache_dir() / parts…` — the one-liner every cache site uses."""
    return harness_cache_dir().joinpath(*parts)


def harness_config_dir() -> Path:
    """`HARNESS_CONFIG_DIR` if set (expanded), else `~/.config/harness` — the user-config home
    (.env, dashboards, the browser profile the career fill runs under). Resolved at call time.
    The full config-dir seal (every reader routed through here) is still open; this helper is
    its first resident so new config-adjacent state never adds a stray home path."""
    env = os.environ.get("HARNESS_CONFIG_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".config" / "harness"


# ————— node subprojects (viz/, bus-app/) — resolve to an INSTALLED tree, whichever checkout runs ——
# `hn` may run from a git worktree's tool env, and a worktree carries no node_modules (the
# README's one-time `npm install` was done on main) — so a node-backed verb would resolve a tree
# with no deps and die in a Node stack trace. One resolver for both: an explicit env override →
# this checkout → the main checkout when this is a git worktree; the caller's MARKERS decide
# "installed"; nothing is ever auto-installed — a miss refuses by naming every path looked in.

REPO_ROOT = Path(__file__).resolve().parents[2]  # src/harness/paths.py → the checkout root


def git_main_checkout(root: Path) -> Path | None:
    """For a git WORKTREE, the main checkout it hangs off: `<root>/.git` is a file
    `gitdir: <main>/.git/worktrees/<name>`. None for a normal checkout (or no git)."""
    dotgit = root / ".git"
    if not dotgit.is_file():
        return None
    try:
        first = dotgit.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = Path(first[len("gitdir:") :].strip()).expanduser()
    # …/<main>/.git/worktrees/<name> → parents[2] = <main>
    if gitdir.parent.name == "worktrees" and gitdir.parents[1].name == ".git":
        return gitdir.parents[2]
    return None


def node_tree_candidates(subdir: str, env_var: str, *, repo_root: Path | None = None) -> list[Path]:
    """Where a node subproject may live, in order: `$env_var`, `<this checkout>/<subdir>`, then
    `<main checkout>/<subdir>` when this checkout is a worktree."""
    root = repo_root or REPO_ROOT
    out: list[Path] = []
    env = os.environ.get(env_var, "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(root / subdir)
    main = git_main_checkout(root)
    if main is not None:
        out.append(main / subdir)
    return out


def resolve_node_tree(
    subdir: str, env_var: str, markers: tuple[str, ...], *, repo_root: Path | None = None
) -> tuple[Path | None, list[Path]]:
    """(the first candidate carrying every marker, or None) + the candidates looked in — the
    caller's refusal names them. Markers are relative paths (`render.js`,
    `node_modules/jsdom/package.json`): a tree with the script but no deps is NOT installed."""
    cands = node_tree_candidates(subdir, env_var, repo_root=repo_root)
    for c in cands:
        if all((c / m).exists() for m in markers):
            return c, cands
    return None, cands


# The standard tool dirs a GUI / launchd / global-install process does NOT inherit: launchd hands
# a job /usr/bin:/bin:/usr/sbin:/sbin, so anything Homebrew or uv installed is invisible to it and
# a bare `age` / `git` / `uv` lookup fails only when the job runs unattended, and silently.
# Resolve tools through here — never by editing a plist's environment (autostart managers
# rewrite plists; the env belongs to code).
TOOL_DIRS: tuple[str, ...] = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def augmented_path(current: str | None = None) -> str:
    """`current` (default: this process's PATH) with every existing TOOL_DIRS entry prepended once."""
    cur = os.environ.get("PATH", "") if current is None else current
    parts = cur.split(":") if cur else []
    prepend = [
        d for d in (str(Path(t).expanduser()) for t in TOOL_DIRS) if Path(d).is_dir() and d not in parts
    ]
    return ":".join([*prepend, *parts])


def which_tool(name: str) -> str | None:
    """`shutil.which` over the augmented PATH — the absolute path, or None when truly absent."""
    import shutil

    return shutil.which(name, path=augmented_path())
