"""`hn media` — read a video the agent cannot watch: its words (`transcript`), its frames (`frames`)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from harness.errors import ProviderError

app = typer.Typer(no_args_is_help=True, help="Read a video: transcript + keyframes (YouTube, via yt-dlp).")
console = Console()


def _tmp(out: str) -> Path:
    if out:
        return Path(out).expanduser()
    from harness.settings import BaseToolkitSettings

    return BaseToolkitSettings().tracker_path / "tmp"


@app.command()
def transcript(
    url: str = typer.Argument(..., help="A YouTube URL (watch, youtu.be, shorts, embed, live) or id"),
    out: str = typer.Option("", "--out", help="Output dir (default: the corpus tmp/ dir)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Write the file; print only the header"),
) -> None:
    """Metadata + the timestamped transcript (creator captions first, auto-captions labeled as such),
    printed and written to <id>-transcript.md in the corpus tmp/ dir. Read-only; no account or key."""
    from harness.media.youtube import read_transcript, render_markdown

    try:
        t = read_transcript(url)
    except ProviderError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    md = render_markdown(t)
    dest = _tmp(out)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{t.video_id}-transcript.md"
    path.write_text(md)
    if quiet:
        console.print(md.split("## Transcript")[0].rstrip(), markup=False, soft_wrap=True)
    else:
        console.print(md, markup=False, highlight=False, soft_wrap=True)
    console.print(f"[dim]wrote {path} · {len(t.cues)} caption lines[/dim]")
    if t.kind == "none":
        raise typer.Exit(code=2)


@app.command()
def frames(
    url: str = typer.Argument(..., help="A YouTube URL or id"),
    max_n: int = typer.Option(16, "--max", help="Most stills to keep (scene changes, spread out)"),
    out: str = typer.Option("", "--out", help="Output dir (default: the corpus tmp/ dir)"),
) -> None:
    """Keyframes for a video that shows charts: downloads the ≤720p video-only stream, picks
    scene-change moments, writes stills named by timestamp to <id>/ in the corpus tmp/ dir plus a contact
    sheet (<id>-sheet.png), then deletes the video. Read the sheet first."""
    from harness.media.youtube import read_frames

    try:
        fr = read_frames(url, _tmp(out), max_n=max(1, max_n))
    except ProviderError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"[bold]{fr.title}[/bold]  [dim]{fr.scene_changes} scene change(s) → "
                  f"{len(fr.stills)} still(s)[/dim]")
    for n in fr.notes:
        console.print(f"[dim]note: {n}[/dim]")
    if fr.sheet:
        console.print(f"contact sheet: {fr.sheet}")
    console.print(f"stills: {fr.out_dir}/ ({', '.join(p.stem for p in fr.stills)})")
