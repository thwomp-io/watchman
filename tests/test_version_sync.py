"""Version-sync gate — `harness.__version__` MUST equal `pyproject [project].version`.

A stated rule without a check is a hope: the two have drifted silently before.
`__version__` rides `_http.USER_AGENT`, so any drift makes every
outbound request (SEC/RSS/etc.) mis-identify its version. This turns a stated release rule
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
        "__version__ is the outbound User-Agent; the two have drifted silently before."
    )
