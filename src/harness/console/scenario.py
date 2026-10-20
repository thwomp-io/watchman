"""The console's active scenario — which pack is loaded and what that seals.

The active pack lives in the console registry (`bus-app.json` → `active_pack`). Two kinds:

- **A bundled sample pack** (under the engine's `samples/packs/`) is a fictional persona and gets
  the full seal: the vault root, every spawn's corpus, state, cache and bus all point at the pack or
  a demo-scoped state dir, so nothing real on the machine can render. A lane the pack lacks reads
  empty, never the real corpus.
- **A personal pack** (any other dir) blends: lanes it provides override, lanes it lacks fall back
  to the real corpus.

Every function resolves per call, so a pack switch applies to the next request without a restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from harness.packs import bundled_packs_dir

# Tracker-relative lane prefix → the pack's lane dir name (`role-hunt/` lives in a pack's `career/`).
_PACK_LANES = (("finance/", "finance"), ("travel/", "travel"), ("role-hunt/", "career"))


def config_dir() -> Path:
    """The console config dir: HARNESS_CONFIG_DIR, else ~/.config/harness."""
    override = os.environ.get("HARNESS_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path("~/.config/harness").expanduser()


def registry_path() -> Path:
    return config_dir() / "bus-app.json"


DEFAULT_REGISTRY = Path(__file__).resolve().parent / "defaults" / "registry.json"


def _default_registry() -> dict[str, Any]:
    loaded = json.loads(DEFAULT_REGISTRY.read_text())
    return {k: v for k, v in loaded.items() if v is not None}


def registry() -> dict[str, Any]:
    """The console registry (producers, surfaces, live_viz, active_pack). An absent file reads as the
    packaged defaults (a fresh machine gets the default surfaces and producers); a file missing the
    `surfaces` or `live_viz` list gets the default list, as the desktop app's loader does."""
    try:
        loaded = json.loads(registry_path().read_text())
    except OSError:
        return _default_registry()
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    defaults = _default_registry()
    for key in ("surfaces", "live_viz"):
        loaded.setdefault(key, defaults.get(key, []))
    return loaded


def active_pack() -> Path | None:
    pack = str(registry().get("active_pack") or "").strip()
    return Path(pack).expanduser() if pack else None


def is_bundled(pack: Path) -> bool:
    try:
        return pack.resolve().is_relative_to(bundled_packs_dir().resolve())
    except OSError:
        return False


def active_bundled_pack() -> Path | None:
    """The active pack, only when it is a bundled sample (the full-seal gate)."""
    pack = active_pack()
    return pack if pack is not None and is_bundled(pack) else None


def demo_seal_state_dir() -> Path:
    """Where state, cache and the bus live while a bundled pack is active."""
    from harness.bus.store import harness_state_dir

    return harness_state_dir() / "demo-seal"


def bus_db() -> Path:
    """The bus the console reads: the demo-scoped bus under a bundled pack, else the default."""
    if active_bundled_pack() is not None:
        return demo_seal_state_dir() / "bus.db"
    from harness.bus.store import default_db_path

    return default_db_path()


def vault_root(tracker_path: Path) -> Path:
    """The root vault reads resolve against: the bundled pack itself, else the real corpus."""
    return active_bundled_pack() or tracker_path


def pack_base(path: str) -> tuple[Path, str] | None:
    """Map a tracker-relative path into the active pack when the pack provides that path's lane.
    Once it does, the read stays in the pack even if the file is absent."""
    pack = active_pack()
    if pack is None:
        return None
    for prefix, lane in _PACK_LANES:
        if path.startswith(prefix):
            lane_dir = pack / lane
            return (lane_dir, path[len(prefix):]) if lane_dir.is_dir() else None
    return None


def spawn_env(env: dict[str, str]) -> dict[str, str]:
    """Apply the active scenario to a spawn's environment. A bundled pack seals corpus, weights,
    state, bus and cache; a personal pack sets WEIGHTS_PACK only (lane blending happens in `hn`)."""
    out = dict(env)
    pack = active_pack()
    if pack is None:
        return out
    out["WEIGHTS_PACK"] = str(pack)
    if is_bundled(pack):
        seal = demo_seal_state_dir()
        out.update({
            "TRACKER_PATH": str(pack),
            "HARNESS_STATE_DIR": str(seal),
            "HARNESS_BUS_DB": str(seal / "bus.db"),
            "HARNESS_CACHE_DIR": str(seal / "cache"),
        })
    return out


def set_active_pack(pack: str | None) -> None:
    """Set (or clear, with None/blank) the active pack — a field-allowlisted write: only
    `active_pack` changes, every other registry key is preserved. The target must look like a pack
    (a `pack.yaml`, or a lane or `dashboards/` dir): over HTTP an arbitrary path would be a way to
    point the console at any directory."""
    cfg = registry()
    cleaned = (pack or "").strip()
    if cleaned:
        target = Path(cleaned).expanduser()
        markers = ("pack.yaml", "finance", "travel", "career", "dashboards")
        if not target.is_dir() or not any((target / m).exists() for m in markers):
            raise ValueError(f"not a pack: {cleaned}")
        cfg["active_pack"] = str(target)
    else:
        cfg.pop("active_pack", None)
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(path)
