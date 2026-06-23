"""Career corpus path resolution — the pack-aware role-hunt root.

The career lane reads its corpus from a `role-hunt/` tree under the tracker root. When a weight pack
is loaded, that tree comes from the pack instead — so the dashboard can swap scenarios. This is the
one place the lane resolves that root, mirroring finance's `Settings.portfolio_path`.
"""

from __future__ import annotations

from pathlib import Path

from harness.settings import BaseToolkitSettings


def role_hunt_root(settings: BaseToolkitSettings) -> Path:
    """The role-hunt corpus root the career lane reads from.

    Returns the active weight pack's `career/` lane dir when a pack provides the career lane — the
    pack drops the `role-hunt/` infix, so `<pack>/career/` IS the role-hunt root (it holds
    `watchlist.yml`, `applications.yaml`, `discoveries/`, etc. directly). Otherwise `<tracker>/
    role-hunt`. Additive: no pack loaded -> the legacy path, unchanged.
    """
    return settings.pack_file("career") or (settings.tracker_path / "role-hunt")
