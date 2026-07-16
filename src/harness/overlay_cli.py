"""`hn config` — the user-overlay surface.

The overlay (`harness.yaml`) is the ONE sanctioned home for bespoke-install settings — per-lane
`global_settings` the engine consults so personal nouns never enter code or shipped defaults.
This group is deliberately read-only: the file is the interface (edit it in your vault); the
Settings panel renders it; commands consume it via `harness.settings.overlay_get`.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from harness.settings import _PACKAGED_OVERLAY, overlay_path, user_overlay

app = typer.Typer(no_args_is_help=True, help="The user overlay (harness.yaml) — show what's set.")
console = Console()


@app.command()
def show(as_json: bool = typer.Option(False, "--json")) -> None:
    """The resolved overlay: which file won the precedence + every lane's global_settings."""
    path = overlay_path()
    data = user_overlay()
    source = (
        "packaged template (no user overlay yet — scaffold <tracker>/config/harness.yaml)"
        if path == _PACKAGED_OVERLAY
        else str(path)
    )
    if as_json:
        console.print_json(json.dumps({"path": str(path), "source": source, "overlay": data}))
        return
    console.print(f"[bold]User overlay[/bold] — {source}")
    def add_rows(table: Table, node: object, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                add_rows(table, v, f"{prefix}{k}.")
        else:
            shown = "[dim]unset[/dim]" if node in (None, [], "") else str(node)
            table.add_row(prefix.rstrip("."), shown)

    for lane, block in data.items():
        gs = block.get("global_settings", {}) if isinstance(block, dict) else {}
        table = Table(title=f"{lane} · global_settings")
        table.add_column("Setting")
        table.add_column("Value")
        add_rows(table, gs)
        console.print(table)
    console.print(
        "[dim]  • Read-only surface — the file is the interface. Precedence: pack > "
        "tracker-resident > packaged template.[/dim]"
    )
