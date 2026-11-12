"""Corpus-facing verbs: generated company profiles and D3 diagrams rendered into role-hunt/."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from harness.career.cli.common import console
from harness.career.config import get_settings, role_hunt_root
from harness.career.service import CareerService
from harness.errors import HarnessError
from harness.viz import KNOWN_TYPES, VizError, render_diagram

_NOTES_SENTINEL = "<!-- ⬇ HAND-EDITED NOTES BELOW — PRESERVED ON REGEN ⬇ -->"


def company_profiles(
    write: bool = typer.Option(
        False, "--write", help="Write role-hunt/companies/{slug}.md (else dry-run summary)"
    ),
) -> None:
    """Generate per-company profile docs for the matrix-present companies (the Hiring-Map deep-link
    targets) — assembled from the corpus (watchlist tier/ATS + target-map axes/comp +
    fit-profiles excerpt + the latest scan's openings). The auto block is regenerated each run; a
    hand-edited notes section below the sentinel is PRESERVED (non-destructive). Read-only research."""
    svc = CareerService(get_settings().tracker_path, role_hunt_root=role_hunt_root(get_settings()))
    profiles = svc.company_profiles()
    if not profiles:
        console.print("[yellow]no scan twin yet — run `hn career openings --write` first.[/yellow]")
        return
    base = role_hunt_root(get_settings()) / "companies"
    for d in profiles:
        auto = CareerService.company_profile_md(d)
        path = base / f"{d['slug']}.md"
        notes = ""
        if path.exists():
            old = path.read_text()
            if _NOTES_SENTINEL in old:
                notes = old.split(_NOTES_SENTINEL, 1)[1]
        if not notes.strip():
            notes = "\n\n## Notes\n\n_TBD — hand-edit; preserved across regens._\n"
        content = auto + "\n" + _NOTES_SENTINEL + notes
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    verb = "wrote" if write else "would write"
    console.print(
        f"{verb} [bold]{len(profiles)}[/bold] company profiles → role-hunt/companies/ "
        f"({', '.join(d['slug'] for d in profiles[:6])}{'…' if len(profiles) > 6 else ''})"
    )
    if not write:
        console.print("[dim]dry-run — pass --write to generate. Hand-edited notes are preserved.[/dim]")


def viz(
    diagram: str = typer.Argument(..., help="Diagram type (matrix | compare | rank-bar | …)"),
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Vault path under tracker/ — SVG written to {dest}/visuals/ (e.g. 'role-hunt')",
    ),
    data_file: str = typer.Option(..., "--data", help="Path to the diagram's JSON data"),
    name: str = typer.Option(..., "--name", help="Output file stem"),
    theme: str = typer.Option(
        "light", "--theme", help="Render theme: light (default) | instrument (the bus-app console palette)"
    ),
) -> None:
    """Render a D3 diagram into the career corpus ({dest}/visuals/{name}.svg) + print the embed.

    Reuses the shared render engine — all harness diagram types (the target-map heatmap is
    `matrix`; finalist radars are `compare`). Read-only artifact; nothing here applies anywhere.
    """
    if diagram not in KNOWN_TYPES:
        raise HarnessError(f"unknown diagram type {diagram!r}; known: {', '.join(KNOWN_TYPES)}")
    try:
        data = json.loads(Path(data_file).read_text())
    except (OSError, ValueError) as e:
        raise HarnessError(f"could not read --data {data_file!r}: {e}") from e
    tracker = get_settings().tracker_path
    out = tracker / dest / "visuals" / f"{name}.svg"
    try:
        written = render_diagram(diagram, data, out, theme=theme)
    except VizError as e:
        raise HarnessError(str(e)) from e
    rel = written.resolve().relative_to(tracker.resolve())
    console.print(f"wrote {written}")
    console.print("\nObsidian embed (paste into the doc):")
    console.print(f"![[{rel.as_posix()}|640]]", markup=False)  # markup=False: keep [[...]] literal
