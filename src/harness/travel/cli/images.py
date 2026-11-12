"""Destination images: fetch and store, tile a contact sheet, and enforce the vault's size discipline."""

from __future__ import annotations

import json

import typer
from rich.markup import escape
from rich.table import Table

from harness.errors import HarnessError
from harness.travel.cli.common import console
from harness.travel.providers.base import ProviderError
from harness.travel.service import TravelService


def images(
    dest: str = typer.Option(
        ...,
        "--dest",
        help="Destination slug (e.g. san-diego → destinations/{slug}/assets) OR a vault path "
        "under travel/ (e.g. visits/2026-05-example-visit → travel/{dest}/assets), like `viz --dest`",
    ),
    subjects: str = typer.Option(
        ..., "--subjects", help="Comma-separated subjects (hotels / attractions / views)"
    ),
    provider: str = typer.Option("wikimedia", "--provider", help="wikimedia (keyless / free)"),
    width: int = typer.Option(480, "--width", help="Embed width for the suggested markdown"),
    keep_original: bool = typer.Option(
        False, "--keep-original",
        help="Store the bytes as served (no downscale/re-encode) — the rare print-quality case; "
        "default applies the vault cap (≤1600px long edge, JPEG q85, ~400KB)",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch + locally store images for a destination report; print embed-ready markdown."""
    subj_list = [s.strip() for s in subjects.split(",") if s.strip()]
    errors: dict[str, str] = {}
    try:
        results = TravelService().fetch_destination_images(
            dest, subj_list, provider_name=provider, keep_original=keep_original, errors=errors
        )
    except (ProviderError, RuntimeError) as e:
        raise HarnessError(str(e)) from e

    if as_json:
        console.print_json(json.dumps({"images": [r.model_dump() for r in results], "errors": errors}))
        if errors and not results:
            raise typer.Exit(code=1)
        return

    table = Table(title=f"Images → {dest}")
    for col in ("Subject", "Source", "Size", "Dims", "Path"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.subject, r.source, f"{r.size_bytes // 1024}KB",
            f"{r.width}x{r.height}", r.rel_path,
        )
    console.print(table)

    console.print("\n[bold]Embed-ready markdown[/bold] (paste into the report, add a caption):")
    for r in results:
        console.print(r.markdown(width), markup=False)  # markup=False: keep the [[...]] literal
        console.print(f"[dim]*{r.subject}* — {r.attribution}[/dim]")

    found = {r.subject for r in results}
    missing = [s for s in subj_list if s not in found and s not in errors]
    if missing:
        console.print(f"\n[yellow]No image found for:[/yellow] {', '.join(missing)}")
    for subject, reason in errors.items():
        console.print(f"[red]Failed:[/red] {escape(subject)} — {escape(reason)}")
    if errors and not results:
        raise typer.Exit(code=1)


def contact_sheet(
    dest: str = typer.Option(
        None, "--dest", help="Destination slug or vault path — tiles its assets/ images"
    ),
    paths: str = typer.Option(None, "--paths", help="Comma-separated image paths (instead of --dest)"),
    cols: int = typer.Option(4, "--cols", help="Grid columns"),
    name: str = typer.Option(None, "--name", help="Output filename stem (default: the dest slug)"),
) -> None:
    """Tile fetched images into ONE labeled grid PNG for fast eyeballing before embedding.

    Written to ~/.cache/harness/contact-sheets/ (NOT the vault — a throwaway eyeball aid).
    """
    try:
        out = TravelService().make_contact_sheet(
            dest=dest, paths=[p.strip() for p in paths.split(",")] if paths else None,
            cols=cols, name=name,
        )
    except (ValueError, OSError) as e:
        raise HarnessError(str(e)) from e
    console.print(f"Contact sheet: {out}")


def shrink_images(
    path: str = typer.Argument(
        ..., help="Vault-relative dir (e.g. travel/destinations/x/food/2026-09-assets)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing"),
    skip_png: bool = typer.Option(
        False, "--skip-png",
        help="Leave PNGs untouched (map canvases that SVG overlays are registered against)",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Apply the vault image-size discipline (≤1600px long edge · JPEG q85 · ~400KB) to images
    already on disk, IN PLACE with extensions kept so every embed still resolves. Prints
    before/after per file + the total saved. Pair with a git history rewrite for old blobs."""
    reports = TravelService().shrink_images(path, dry_run=dry_run, skip_png=skip_png)
    if as_json:
        console.print_json(json.dumps([
            {"path": str(r.path), "before": r.before, "after": r.after, "width": r.width,
             "height": r.height, "over_cap": r.over_cap, "changed": r.changed}
            for r in reports
        ]))
        return
    table = Table(title=f"shrink-images → {path}{' (dry run)' if dry_run else ''}")
    for col in ("File", "Before", "After", "Dims", "Note"):
        table.add_column(col)
    changed = [r for r in reports if r.changed]
    for r in changed:
        table.add_row(
            r.path.name, f"{r.before // 1024}KB", f"{r.after // 1024}KB", f"{r.width}x{r.height}",
            "over cap (best effort)" if r.over_cap else "",
        )
    console.print(table)
    console.print(
        f"{len(changed)} of {len(reports)} images shrunk · "
        f"saved {sum(r.saved for r in changed) / 1e6:.1f}MB"
        + (" · nothing written" if dry_run else "")
    )
