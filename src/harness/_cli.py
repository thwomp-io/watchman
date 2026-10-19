"""Shared CLI plumbing for the lane CLIs."""

from __future__ import annotations

from rich.console import Console

_err = Console(stderr=True)


def fail(msg: str, *, mark: str = "error:") -> None:
    """Print a one-line error to STDERR — a spawning console (the watchman door, the desktop app)
    relays stderr as the widget's error reason, so an error on stdout reads as no reason at all."""
    _err.print(f"[red]{mark}[/red] {msg}")
