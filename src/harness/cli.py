"""Root Typer CLI — the thin `harness` (shorthand `hn`) entry point.

Mounts each domain's noun-group sub-app verbatim (`travel <verb>`, `finance <verb>`, `career <verb>`)
via ``add_typer`` — the noun-group sub-commands pattern. The root holds no domain logic; it only
composes. New domains mount here with one line.
"""

from __future__ import annotations

import typer

from harness import __version__
from harness.bus.cli import app as bus_app
from harness.career.cli import app as career_app
from harness.finance.cli import app as finance_app
from harness.packs import packs_app
from harness.scaffold import init as init_cmd
from harness.travel.cli import app as travel_app

app = typer.Typer(
    add_completion=False,
    help="harness — a personal agentic-harness toolkit. One CLI of domain submodules.",
    no_args_is_help=True,
)

app.add_typer(travel_app, name="travel")
app.add_typer(finance_app, name="finance")
app.add_typer(career_app, name="career")
app.add_typer(bus_app, name="bus")
app.add_typer(packs_app, name="packs")

# `hn init <dir>` — scaffold a new corpus (top-level, not lane-scoped).
app.command(name="init")(init_cmd)


@app.command()
def version() -> None:
    """Print the harness version."""
    typer.echo(f"harness {__version__}")


if __name__ == "__main__":
    app()
