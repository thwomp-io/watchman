"""Root Typer CLI — the thin `harness` (shorthand `hn`) entry point.

Mounts each domain's noun-group sub-app verbatim (`travel <verb>`, `finance <verb>`, `career <verb>`)
via ``add_typer`` — the noun-group sub-commands pattern. The root holds no domain logic; it only
composes. New domains mount here with one line.
"""

from __future__ import annotations

import sys

import typer

from harness import __version__
from harness.backup.cli import app as backup_app
from harness.bus.cli import app as bus_app
from harness.career.cli import app as career_app
from harness.finance.cli import app as finance_app
from harness.packs import packs_app
from harness.scaffold import init as init_cmd
from harness.travel.cli import app as travel_app

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
    add_completion=False,
    help="harness — a personal agentic-harness toolkit. One CLI of domain submodules.",
    no_args_is_help=True,
)

app.add_typer(travel_app, name="travel")
app.add_typer(finance_app, name="finance")
app.add_typer(career_app, name="career")
app.add_typer(bus_app, name="bus")
app.add_typer(backup_app, name="backup")
app.add_typer(packs_app, name="packs")

# `hn console` — the served web console's own verbs (the sealed demo).
from harness.console.demo import app as console_app  # noqa: E402

app.add_typer(console_app, name="console")

# the beads lane: a read-only board over the tracker's .beads/issues.jsonl export —
# imported lazily-in-place like the others; zero bd/Dolt dependency.
from harness.beads.cli import app as beads_app  # noqa: E402

app.add_typer(beads_app, name="beads")

# `hn config` — the user overlay (harness.yaml) surface.
from harness.overlay_cli import app as config_app  # noqa: E402

app.add_typer(config_app, name="config")

# `hn media` — read a video the agent cannot watch.
from harness.media.cli import app as media_app  # noqa: E402

app.add_typer(media_app, name="media")

# `hn pocket` — the read-only phone chat with an agent lane (runner + operator verbs).
from harness.pocket.cli import app as pocket_app  # noqa: E402

app.add_typer(pocket_app, name="pocket")


# `hn places` — ad-hoc local place search near a ZIP / place (cross-lane; the travel lane's SerpAPI client).
from harness.places.cli import app as places_app  # noqa: E402

app.add_typer(places_app, name="places")


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
    import importlib

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


if __name__ == "__main__":
    app()
