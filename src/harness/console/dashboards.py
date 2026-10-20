"""Console dashboards — the engine owns the built-in defaults and the load/seed/save/reset rules.

The built-in dashboards are packaged JSON (`defaults/dashboards/<name>.json`), one file per tab, so
the served console and any shell over it render the same tab set on a fresh machine. Rules:

- **Load** reads the operator's dashboards dir, then seeds every built-in whose lane is missing
  (a fresh machine, or a lane added in a later release). An existing file is never overwritten:
  the disk file is the override, the packaged default is the portable source of truth.
- **A pack that ships `dashboards/` owns the whole tab set** while it is active. Pack dashboards are
  read transiently and never seeded into the operator's dir (a file there would override forever).
- **Save** stamps `owner: "user"`, writes atomically (temp + rename), and banks the file it replaces
  into `.backups/<lane>-<epoch>.json` (newest 10 per lane). **Reset** restores the packaged default
  through the same backup path, so both are one file-copy from undone.

Dashboards are passed through as raw JSON: the webview owns the schema (`types.ts`), and a field the
engine does not know survives a round trip.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults" / "dashboards"
BACKUPS_KEPT = 10
_LANE_RE = re.compile(r"[a-z0-9-]+")

Dashboard = dict[str, Any]


def packaged_defaults() -> list[tuple[str, Dashboard]]:
    """The built-in dashboards as (file stem, dashboard), sorted by stem."""
    out: list[tuple[str, Dashboard]] = []
    for p in sorted(DEFAULTS_DIR.glob("*.json")):
        loaded = json.loads(p.read_text())
        if isinstance(loaded, dict):
            out.append((p.stem, loaded))
    return out


def read_dir(d: Path) -> list[Dashboard]:
    """Every dashboard JSON in a directory (non-recursive). A malformed file drops that one tab."""
    out: list[Dashboard] = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            loaded = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict) and loaded.get("lane"):
            out.append(loaded)
    return out


def load_all_in(d: Path) -> list[Dashboard]:
    """The operator's dashboards plus a seeded copy of every built-in whose lane is absent.

    Seeding is best-effort: an unwritable dir still returns the defaults, it just can't persist them.
    """
    out = read_dir(d)
    present = {x.get("lane") for x in out}
    missing = [(name, dash) for name, dash in packaged_defaults() if dash.get("lane") not in present]
    if missing:
        try:
            d.mkdir(parents=True, exist_ok=True)
            for name, dash in missing:
                (d / f"{name}.json").write_text(json.dumps(dash, indent=2))
        except OSError:
            pass
        out.extend(dash for _, dash in missing)
    out.sort(key=lambda x: str(x.get("lane", "")))
    return out


def load_all_for(active_pack: Path | None, real_dir: Path) -> list[Dashboard]:
    """Dashboards for the active scenario: a pack's own `dashboards/` when it ships any, else the
    operator's load (disk + seeded defaults)."""
    if active_pack is not None:
        pack_dashboards = read_dir(active_pack / "dashboards")
        if pack_dashboards:
            return sorted(pack_dashboards, key=lambda x: str(x.get("lane", "")))
    return load_all_in(real_dir)


def find_widget_for(
    active_pack: Path | None, real_dir: Path, lane: str, wid: str
) -> dict[str, Any] | None:
    """Resolve a widget by (lane, id) in the active scenario's dashboards, so a pack's widgets run
    with the pack's own sources and symbols."""
    for d in load_all_for(active_pack, real_dir):
        if d.get("lane") == lane:
            return next((w for w in d.get("widgets", []) if w.get("id") == wid), None)
    return None


def _write_atomic(path: Path, body: str) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(body)
    tmp.replace(path)


def _backup_existing(d: Path, lane: str, path: Path) -> None:
    """Bank the current lane file before it is replaced; keep the newest 10. Best-effort: a failed
    backup never blocks the save, which is atomic on its own."""
    if not path.exists():
        return
    try:
        backups = d / ".backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / f"{lane}-{int(time.time()):011d}.json").write_bytes(path.read_bytes())
        mine = sorted(backups.glob(f"{lane}-*.json"))
        for old in mine[: max(0, len(mine) - BACKUPS_KEPT)]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def _checked_lane(lane: object) -> str:
    if not isinstance(lane, str) or not _LANE_RE.fullmatch(lane):
        raise ValueError(f"invalid lane '{lane}': lowercase letters, digits, hyphens only")
    return lane


def save_to(d: Path, dashboard: Dashboard) -> None:
    """Persist an edited dashboard as user-owned. The lane is the filename, so its charset is
    validated before any path is built."""
    lane = _checked_lane(dashboard.get("lane"))
    owned = {**dashboard, "owner": "user"}
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{lane}.json"
    _backup_existing(d, lane, path)
    _write_atomic(path, json.dumps(owned, indent=2))


def reset_to(d: Path, lane: str) -> Dashboard:
    """Restore a lane's packaged default (the current file is banked first). A lane with no
    built-in default is an error; its history is in `.backups/`."""
    lane = _checked_lane(lane)
    default = next((dash for _, dash in packaged_defaults() if dash.get("lane") == lane), None)
    if default is None:
        raise ValueError(f"no built-in default for '{lane}' — its history is in .backups/")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{lane}.json"
    _backup_existing(d, lane, path)
    _write_atomic(path, json.dumps(default, indent=2))
    return default
