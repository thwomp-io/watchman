"""Import every module in the harness package and fail loudly on any import-time error.

CI smoke: catches missing platform dependencies and platform-only imports as a batch,
on whichever OS the job runs.  Usage: uv run python scripts/ci_import_walk.py
"""

from __future__ import annotations

import importlib
import pkgutil

import harness


def main() -> int:
    mods = [m.name for m in pkgutil.walk_packages(harness.__path__, "harness.")]
    failed: list[str] = []
    for name in mods:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — report every failure kind
            failed.append(f"{name}: {exc!r}")
    print(f"imported {len(mods) - len(failed)}/{len(mods)} modules")
    for line in failed:
        print(f"FAIL {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
