"""Shared CLI plumbing for the lane CLIs: how a failure is reported, and the entry point that
guarantees every failure is reported that way.

One shape, two renderings. A person gets `error: <message>` and, when there is one, `hint: <what to
do>`. A program that sets `HARNESS_ERROR_FORMAT=json` (the console door does) gets the same fields
as one JSON line on stderr: `{"error": {"kind", "message", "hint"}}`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from typer.core import TyperGroup

from harness.errors import HarnessError

ERROR_FORMAT_ENV = "HARNESS_ERROR_FORMAT"
DEBUG_ENV = "HARNESS_DEBUG"

# sysexits.h EX_SOFTWARE: a bug in this program, as distinct from exit 1 (a failure it understood).
EXIT_FAILURE = 1
EXIT_INTERNAL = 70

_err = Console(stderr=True)


def report(message: str, *, kind: str = "error", hint: str | None = None) -> None:
    """Write one failure to stderr, never stdout: a spawning console reads stdout as the result."""
    if os.environ.get(ERROR_FORMAT_ENV) == "json":
        payload = {"error": {"kind": kind, "message": message, "hint": hint}}
        sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    # soft_wrap: piped stderr has no real width, and a hard wrap would break the message mid-word.
    _err.print(f"[red]error:[/red] {escape(message)}", soft_wrap=True)
    if hint:
        _err.print(f"[dim]hint:[/dim] {escape(hint)}", soft_wrap=True)


class ReportingGroup(TyperGroup):
    """A command group that turns a `HarnessError` from any of its commands into the standard report.

    Set as the `cls` of every `typer.Typer(...)`. `run` already does this for `hn`; doing it at the
    group as well gives the same outcome when a group is invoked directly, as the tests do, so a
    command only ever has to raise.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except HarnessError as e:
            report(str(e), kind=e.kind, hint=e.hint)
            raise typer.Exit(code=e.exit_code) from e


def _report_network_error(e: Exception) -> bool:
    """Report `e` as a source failure if it is a network error, naming the host and never the URL
    (a URL can carry a key). httpx is imported here, not at module top: every command pays for this
    module's imports, and only a failing one needs httpx."""
    import httpx

    from harness._http import describe_http_error

    if not isinstance(e, httpx.HTTPError):
        return False
    try:
        host = e.request.url.host
    except RuntimeError:  # httpx raises this when the error carries no request
        host = "an outside source"
    report(
        f"{host}: {describe_http_error(e)}",
        kind="source",
        hint="Usually temporary. Try again in a minute.",
    )
    return True


def run(app: Callable[[], object]) -> NoReturn:
    """Run the CLI so that no failure reaches the user as a traceback.

    A `HarnessError` is the expected path. A network error that a provider let through is still an
    outside source failing, so it is reported as one. Anything else is a bug: one line naming it,
    and the traceback only under `HARNESS_DEBUG=1`.
    """
    try:
        app()
    except HarnessError as e:
        report(str(e), kind=e.kind, hint=e.hint)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        if os.environ.get(DEBUG_ENV):
            raise
        if _report_network_error(e):
            sys.exit(EXIT_FAILURE)
        report(
            f"{type(e).__name__}: {e}",
            kind="internal",
            hint=f"This is a bug in harness. Rerun with {DEBUG_ENV}=1 for the traceback.",
        )
        sys.exit(EXIT_INTERNAL)
    sys.exit(0)
