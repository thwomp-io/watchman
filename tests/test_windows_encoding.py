"""The engine must survive legacy-codepage stdio (the Windows piped-spawn default).

Piped spawns on Windows hand the CLI a cp1252 stdout; the toolkit legitimately emits
characters outside that codepage (the ≈ in estimate labels, the → in allocation labels).
Importing ``harness.cli`` reconfigures both streams to UTF-8 so a single glyph can never
kill a whole command. These tests pin that behavior by forcing the legacy encoding via
PYTHONIOENCODING — the cross-platform stand-in for the Windows default.
"""

from __future__ import annotations

import os
import subprocess
import sys

_LEGACY_ENV = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}


def _run_under_cp1252(code: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(# noqa: S603 — fixed argv, test-controlled input
        [sys.executable, "-c", code],
        capture_output=True,
        env=_LEGACY_ENV,
        timeout=60,
        check=False,
    )


def test_cli_import_reconfigures_stdio_to_utf8() -> None:
    """Printing ≈ and → after importing the CLI must succeed under cp1252 stdio."""
    proc = _run_under_cp1252("import harness.cli; print('\\u2248 \\u2192')")
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert "≈ →".encode() in proc.stdout


def test_unfixed_interpreter_actually_fails_under_cp1252() -> None:
    """Control: without the CLI import, cp1252 stdio rejects the same glyphs.

    Guards the test itself — if this ever passes without the reconfigure (e.g. a future
    Python defaults stdio to UTF-8 everywhere), the pin above has lost its subject and
    this file can be retired.
    """
    proc = _run_under_cp1252("print('\\u2248 \\u2192')")
    assert proc.returncode != 0
    assert b"UnicodeEncodeError" in proc.stderr
