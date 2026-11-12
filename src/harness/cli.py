"""The root CLI: `hn` (also `harness`).

It holds no logic of its own. Each command group is a Typer app defined beside the code it drives
(`harness/<group>/cli`), and this file mounts them under their names.

A group is imported when it is first asked for, not when this file loads: most invocations are one
command spawned by a dashboard widget or an agent, and importing every lane to run one verb more
than doubled the start time. `hn --help` still lists them all, at the cost of importing them all.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import typer

from harness import __version__
from harness._cli import ReportingGroup, run
from harness.scaffold import init as init_cmd

# Where each group's Typer app lives, in the order `hn --help` lists them: the three lanes, then
# the tools around them.
GROUPS: dict[str, str] = {
    "travel": "harness.travel.cli:app",
    "finance": "harness.finance.cli:app",
    "career": "harness.career.cli:app",
    "bus": "harness.bus.cli:app",  # the event bus and its HTTP server
    "backup": "harness.backup.cli:app",
    "packs": "harness.packs:packs_app",  # the bundled sample packs
    "console": "harness.console.demo:app",  # the web console's sealed demo
    "beads": "harness.beads.cli:app",  # a read-only board over an issue-tracker export
    "config": "harness.overlay_cli:app",  # the user's settings overlay (harness.yaml)
    "media": "harness.media.cli:app",  # transcripts and frames from a video
    "pocket": "harness.pocket.cli:app",  # the read-only phone chat with an agent
    "places": "harness.places.cli:app",  # local place search
    "calibration": "harness.calibration.cli:app",  # scored predictions
}


def group_app(name: str) -> typer.Typer:
    """The Typer app behind a group in `GROUPS`, importing its module."""
    module, _, attr = GROUPS[name].partition(":")
    sub: typer.Typer = getattr(importlib.import_module(module), attr)
    return sub


class _RootGroup(ReportingGroup):
    """The root command group: the commands registered on it directly, plus every group in `GROUPS`,
    each built on first use."""

    def list_commands(self, ctx: Any) -> list[str]:
        # Plain commands first, then the groups, then any group an extension registered directly.
        registered = super().list_commands(ctx)
        plain = [name for name in registered if not hasattr(self.commands[name], "commands")]
        return [*plain, *GROUPS, *(name for name in registered if name not in plain)]

    def get_command(self, ctx: Any, name: str) -> Any:
        if name not in GROUPS:
            return super().get_command(ctx, name)
        group = typer.main.get_group(group_app(name))
        group.name = name
        return group


# Piped spawns on Windows hand Python a legacy-codepage stdout/stderr (cp1252), which can't
# encode characters the toolkit legitimately emits (the ≈ in estimate labels, the → in
# allocation labels) — one such glyph kills an entire command with UnicodeEncodeError. Force
# UTF-8 on both streams; errors="replace" guarantees a degraded glyph is never fatal.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A closed/detached stream (capture harnesses, unusual hosts) must never break import.
            pass

app = typer.Typer(
    cls=_RootGroup,
    add_completion=False,
    help="harness — a personal agentic-harness toolkit. One CLI of domain submodules.",
    no_args_is_help=True,
    # Failures are reported by `main` below, in one shape. Typer's own handler would print a
    # boxed traceback instead, which neither a person nor a spawning console can use.
    pretty_exceptions_enable=False,
)

# ————— the private extension seam —————
# An install may run a permanent `personal` branch (a worktree) that carries ONLY new
# files on top of main — bespoke, never-published lanes under `harness.private`. This hook is the
# one thing main ships for that: if `harness.private` is importable, its `register(app)` mounts the
# extra lanes; absent (every public install, this branch), nothing happens. Why a seam and not a
# fork: the public repo stays a transform of main and the personal branch merges main with no
# shared-file conflicts. Verbs mounted here are documented on the branch that carries them.
PRIVATE_LANES: list[str] = []


def mount_private_lanes(root: typer.Typer) -> list[str]:
    """Mount `harness.private.register(root)` lanes when the module exists. Returns the lane names."""
    try:
        private = importlib.import_module("harness.private")
    except ImportError:
        return []
    register = getattr(private, "register", None)
    if not callable(register):
        return []
    names = register(root)
    mounted = [str(n) for n in names] if isinstance(names, list | tuple) else []
    PRIVATE_LANES.extend(mounted)
    return mounted


mount_private_lanes(app)

# `hn init <dir>` — scaffold a new corpus (top-level, not lane-scoped).
app.command(name="init")(init_cmd)


@app.command()
def version() -> None:
    """Print the harness version."""
    typer.echo(f"harness {__version__}")


def main() -> None:
    """The `hn` / `harness` entry point."""
    run(app)


if __name__ == "__main__":
    main()
