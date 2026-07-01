"""Version-sync gate — `harness.__version__` MUST equal `pyproject [project].version`.

A stated rule without a check is a hope: the two drifted silently 0.20→0.34 (v0.34.0), then AGAIN
0.69→0.71 (caught + fixed in 0.72.1). `__version__` rides `_http.USER_AGENT`, so any drift makes every
outbound request (SEC/RSS/etc.) mis-identify its version. This turns the operator skill mechanical grep
into an enforced gate — the drift can't recur silently.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from harness import __version__


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert __version__ == declared, (
        f"__version__ ({__version__}) != pyproject ({declared}) — bump BOTH on release. "
        "__version__ is the outbound User-Agent; it drifted silently at 0.34 and again at 0.71."
    )
