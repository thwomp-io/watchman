"""CLI adapter for the beads lane. Read-only over bd's state; the one write this
lane performs is into its OWN derived-cache dir (the generated ticket docs)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help="Read-only board over the tracker's beads export")
console = Console()

TICKETS_REL = "ops/beads"


@app.command()
def board(
    limit: int = typer.Option(15, "--limit", help="Ready-queue rows to include"),
    as_json: bool = typer.Option(False, "--json", help="Emit the board contract (the BACKLOG tab source)"),
    section: str = typer.Option(
        "", "--section", help="Emit ONE top-level contract section (e.g. `tree` — the live-viz "
        "source for the family tree; implies --json)"
    ),
) -> None:
    """The backlog board: counts + P1s + the presence board (in_progress = who's on what) +
    an honest ready approximation + shipped-this-week + the family tree. Reads
    `.beads/issues.jsonl` (the passive export) — state changes stay with `bd`; this is a glance
    surface. Side effect: keeps the vault's generated ticket docs in sync (ops/beads/) so every
    row's hyperlink resolves — mtime-stamped, so an unmoved export costs one stat call."""
    from harness.beads.board import build_board
    from harness.beads.tickets import sync_tickets
    from harness.finance.config.settings import get_settings

    tracker = get_settings().tracker_path
    path = tracker / ".beads" / "issues.jsonl"
    tickets_rel = TICKETS_REL
    try:
        sync_tickets(path, tracker / TICKETS_REL)
    except OSError:
        # unwritable tracker (a read-only bundled pack): PRE-GENERATED docs still link fine —
        # only a corpus with no docs at all drops the column
        if not (tracker / TICKETS_REL).is_dir():
            tickets_rel = ""
    b = build_board(path, ready_limit=limit, tickets_rel=tickets_rel)

    if section:
        payload = b.model_dump(mode="json")
        if section not in payload:
            raise typer.BadParameter(f"unknown section '{section}' — one of: {', '.join(payload)}")
        console.print_json(data=payload[section])
        return
    if as_json:
        console.print_json(b.model_dump_json())
        return

    console.print(
        f"\n[bold]BEADS BOARD[/bold] — {b.total} issues · [bold]{b.open} open[/bold] · "
        f"{b.in_progress} in progress · {b.deferred} deferred · {b.closed_7d} closed this week · "
        f"[dim]export {b.exported_ago or 'age unknown'} (passive — bd is live truth)[/dim]\n"
    )
    for title, rows in (
        ("In progress — the presence board", b.presence),
        ("P1 — open", b.p1_open),
        (f"Ready (top {limit})", b.ready),
        ("Shipped this week", b.shipped_7d),
    ):
        if not rows:
            continue
        t = Table(title=title)
        for col in ("ID", "Pri", "Type", "Title", "Assignee", "Labels", "Updated"):
            t.add_column(col)
        for r in rows:
            t.add_row(r.id, r.priority, r.type, r.title[:70], r.assignee, r.labels, r.updated)
        console.print(t)
    console.print(
        f"[dim]tree: {len(b.tree.beads)} beads · {len(b.tree.edges)} edges · "
        f"{b.tree.omitted} quiet singles off-tree[/dim]"
    )
    for n in b.notes:
        console.print(f"[dim]  • {n}[/dim]")


@app.command()
def tickets(
    force: bool = typer.Option(False, "--force", help="Re-render every doc even if the export is unmoved"),
) -> None:
    """Render/refresh the per-bead ticket docs (`ops/beads/<id>.md` in the tracker vault) —
    Jira-shaped generated views of the beads db. bd stays truth: the docs carry a GENERATED
    sentinel, dependency wikilinks, the comments thread, and the close reason as the resolution.
    The board command runs this sync implicitly; use this verb for a forced full re-render."""
    from harness.beads.tickets import sync_tickets
    from harness.finance.config.settings import get_settings

    tracker = get_settings().tracker_path
    path = tracker / ".beads" / "issues.jsonl"
    res = sync_tickets(path, tracker / TICKETS_REL, force=force)
    if res.skipped:
        console.print("[dim]export unmoved — ticket docs already current (use --force to re-render)[/dim]")
        return
    console.print(
        f"tickets synced → {TICKETS_REL}/: [bold]{res.written} written[/bold] · "
        f"{res.unchanged} unchanged · {res.pruned} pruned"
    )
