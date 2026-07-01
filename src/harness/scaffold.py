"""`hn init` — scaffold a new harness corpus.

The analog of a `tracker init`: a fresh user runs it to lay down the dirs + template config/weights
files the toolkit and their agent expect, then fills them in (by hand, or by letting their agent build
the corpus from conversation — the `corpus-operator` skill). The skeleton it copies lives in
`templates/corpus/` (the single canonical definition of "what a harness corpus looks like"); the demo
weight packs are filled instances of the same shape.

NON-DESTRUCTIVE by construction: an existing file is never overwritten (it's skipped + reported), so
re-running `init`, or pointing it at a partially-built corpus, only fills the gaps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer


def templates_dir() -> Path:
    """The scaffold skeleton (`templates/corpus`), resolved from the package location so an editable
    install finds it regardless of CWD (the launch-context-resolution doctrine)."""
    return Path(__file__).resolve().parents[2] / "templates" / "corpus"


def scaffold_corpus(dest: Path) -> tuple[list[str], list[str]]:
    """Copy the skeleton into `dest`, non-destructively. Returns (created, skipped) relative paths.

    Dirs are created as needed; an existing file is left untouched (reported as skipped) so a user's
    real content is never clobbered — the corpus-writes-are-non-destructive rule.
    """
    src_root = templates_dir()
    if not src_root.is_dir():  # pragma: no cover — packaging guard
        raise FileNotFoundError(f"scaffold skeleton missing: {src_root}")
    created: list[str] = []
    skipped: list[str] = []
    for src in sorted(src_root.rglob("*")):
        rel = src.relative_to(src_root)
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            skipped.append(str(rel))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        created.append(str(rel))
    return created, skipped


def init(
    directory: Path = typer.Argument(
        ...,
        help="Where to scaffold the corpus (e.g. ~/projects/corpus). Required — so you never scaffold "
        "over an existing corpus by accident. Point the toolkit at it with TRACKER_PATH.",
    ),
) -> None:
    """Scaffold a new harness corpus (dirs + template config/weights files) for you to fill in."""
    dest = directory.expanduser()
    created, skipped = scaffold_corpus(dest)
    typer.echo(f"Scaffolded corpus → {dest}")
    typer.echo(f"  created: {len(created)} file(s)" + (f"   skipped (already present): {len(skipped)}"
                                                       if skipped else ""))
    if created:
        typer.echo("\nNext:")
        typer.echo("  1. Read the README in each dir for what to fill.")
        typer.echo("  2. Start with finance/config/portfolio.yaml, role-hunt/watchlist.yml, "
                   "travel/config/weights.yaml, and user_background.md.")
        typer.echo(f"  3. Point the toolkit at it:  export TRACKER_PATH={dest}")
        typer.echo("     (or let your agent build the corpus from conversation — the corpus-operator skill).")
