"""CLI adapter for the backup lane. Two verbs: `snapshot` (the standing job the
launchd timer runs) + `status` (the deterministic health read). Logic lives in service.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.console import Console

if TYPE_CHECKING:
    from harness.backup.service import BackupConfig

app = typer.Typer(no_args_is_help=True, help="age-encrypted offsite snapshots of the corpus + service state")
console = Console()


def _cfg() -> BackupConfig:
    """The overlay loader with the one error path every verb shares."""
    from harness.backup.service import BackupConfigError, load_config

    try:
        return load_config()
    except BackupConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None


@app.command()
def snapshot(
    notify: bool = typer.Option(
        False, "--notify", help="Publish the outcome to the bus (info on success, ALERT on failure)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what a run would read (with existence checks) and stop"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Build one snapshot (git bundles + consistent sqlite copies + config/state tars), seal it with
    age to the overlay's recipient, land it in the sync folder, prune past `keep`, log the run."""
    from harness.backup.service import plan, run_backup

    cfg = _cfg()
    if dry_run:
        for kind, source, present in plan(cfg):
            mark = "[green]ok[/green]" if present else "[red]MISSING[/red]"
            console.print(f"  {mark}  {kind:<6} {source}")
        return
    try:
        res = run_backup(cfg, notify=notify)
    except Exception as exc:  # the run logged + published already; surface it and exit non-zero
        console.print(f"[red]backup FAILED:[/red] {exc}")
        raise typer.Exit(1) from None
    if as_json:
        console.print_json(res.model_dump_json())
        return
    mb = res.bytes / 1_048_576
    console.print(f"[green]OK[/green] {res.snapshot} {mb:,.0f} MB · {res.entries} artifacts · {res.seconds}s")
    for w in res.warnings:
        console.print(f"  [yellow]warn[/yellow] {w}")
    if res.pruned:
        console.print(f"  pruned {len(res.pruned)} old file(s)")


@app.command()
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    """READ-ONLY health: prerequisites, the newest snapshot's freshness + integrity, footprint,
    the last run. Every FAIL line names its fix."""
    from harness.backup.service import status as _status

    cfg = _cfg()
    st = _status(cfg)
    if as_json:
        console.print_json(st.model_dump_json())
        raise typer.Exit(0 if st.ok else 1)
    console.print(f"\n[bold]BACKUP[/bold] → {st.snapshot_dir}")
    for c in st.checks:
        mark = "[green]OK[/green]  " if c.ok else "[red]FAIL[/red]"
        line = f"  {mark} {c.name}"
        if c.detail:
            line += f"  [dim]{c.detail}[/dim]"
        if not c.ok and c.fix:
            line += f"  → {c.fix}"
        console.print(line)
    console.print(f"  snapshots: {st.count}   footprint: {st.total_bytes / 1_048_576:,.0f} MB\n")
    raise typer.Exit(0 if st.ok else 1)
