"""The web-console RPC door — `POST /api/invoke/{cmd}` mirroring the Tauri shell's commands.rs.

Why an RPC route per command instead of resource routes: the frontend's single door (`api.ts` →
`transport.ts`) speaks in commands.rs command names, so the most literal server mirror is one
dispatch table keyed by the same names — a new command is one `api.ts` wrapper + one row here,
never a bespoke route shape, which keeps two-backend parity drift structurally hard. Each handler
returns EXACTLY the type its Rust twin returns (strings stay strings — `run_widget`/`read_doc`
results are JSON-encoded strings, matching `invoke()`'s `Result<String>`), so the webview cannot
tell the backends apart.

Gating (the door is read-only by design):
- READ commands are mirrored (bus reads, config/dashboards/surfaces listings, the vault four,
  widget/surface runs — spawns of allowlisted read-only `hn` verbs are reads in effect).
- `ack_events` is allowed: it is the SAME write capability the shipped `/api/bus/ack` route
  already carries (bus writes ride the already-shipped routes) — door-aliased, not a new
  write class.
- `set_active_pack` / `run_producer` are WRITE-GATED (403) — packs are the native console's
  scenario-switcher, producers are the standing agents' job.
- `list_viz` / `read_viz` are mirrored (the viz.rs discover/sniff port);
  `list_packs`/`get_active_pack` answer honestly empty (the web console serves the real corpus).

Security model (inherits the bus server's): bearer token on every call (the caller mounts these
routes into `create_app()`, one server / one token / one bind); the tailnet is the perimeter.
Spawns are DOUBLY constrained: the client passes only (lane, id) — commands come from the
operator-owned config, exactly like commands.rs — and a belt-and-braces allowlist rejects any
resolved command that isn't the `hn` engine on a known lane.

⚠ Demo-seal caveat: these read paths serve the REAL corpus and
config; the demo-pack seal (TRACKER_PATH/WEIGHTS_PACK/state redirects) is NOT yet applied here.
That is acceptable while `--console` is an operator-run opt-in on the operator's own node, and it
is exactly the "does the seal cover this read path?" check to revisit if that posture ever
changes.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from harness import __version__
from harness.bus.service import BusService
from harness.bus.store import default_db_path
from harness.settings import BaseToolkitSettings

# ————— roots + shared config reads ————————————————————————————————————————————————————————————

# Lanes the spawn allowlist accepts — the read-only engine surface the dashboards/surfaces use.
ALLOWED_LANES = {"finance", "career", "travel", "bus", "beads"}
# Mirrors commands.rs VAULT_SKIP/VAULT_MAX_DEPTH/IMG_EXT — keep in sync (three-surface contract).
VAULT_SKIP = {"node_modules", "__pycache__", "target", "dist", "screenshots", "tmp"}
VAULT_MAX_DEPTH = 8
IMG_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "avif": "image/avif", "svg": "image/svg+xml",
}
MAX_IMAGE_BYTES = 16 * 1024 * 1024


class CommandError(Exception):
    """A handler failure reported to the client as the Tauri error-string shape (HTTP 400)."""


def _vault_root() -> Path:
    # The one corpus-root primitive (env TRACKER_PATH → ~/projects/corpus) — instantiated per
    # request so env changes (tests, future sealing) are honored without process restarts.
    return BaseToolkitSettings().tracker_path.expanduser()


def _config_dir() -> Path:
    # Honors HARNESS_CONFIG_DIR ahead of the default — the Python half of the config-dir seal
    # (the Rust host handles the same dir); free to support now, load-bearing when sealing lands.
    import os

    override = os.environ.get("HARNESS_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path("~/.config/harness").expanduser()


def _app_config() -> dict[str, Any]:
    """The native console's registry — bus-app.json (producers/surfaces/live_viz/active_pack),
    read-only. THE FILENAME IS bus-app.json, not config.json — config.rs `config_path()` is the
    source of truth (a guessed name once rendered every surface empty until caught). Absent file →
    empty config: the API still works on a machine that never ran the Tauri app."""
    path = _config_dir() / "bus-app.json"
    try:
        loaded = json.loads(path.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ————— containment guard (the commands.rs resolve_vault_path port — tests ride along) ——————————


def resolve_vault_path(path: str) -> Path:
    """Resolve a vault-relative path to a real path GUARANTEED inside the vault. The port of
    commands.rs's guard: canonicalize (resolving symlinks + `..`), then require the result to stay
    under the canonicalized vault root. Every read path flows through here — a new read is a
    one-liner over this guard, never a fresh hand-rolled check."""
    vault = _vault_root()
    try:
        canon = (vault / path).resolve(strict=True)
        vault_canon = vault.resolve(strict=True)
    except OSError as exc:
        raise CommandError(f"{path}: {exc}") from exc
    if not canon.is_relative_to(vault_canon):
        raise CommandError("path escapes the vault")
    return canon


def _read_vault_file(path: str) -> str:
    try:
        return resolve_vault_path(path).read_text()
    except OSError as exc:
        raise CommandError(f"{path}: {exc}") from exc


# ————— spawn allowlist + helpers (the run_json_command port) ———————————————————————————————————


def _validate_tool(cmd: str, args: list[str]) -> None:
    """Belt-and-braces over config trust: the only spawnable tool is the `hn` engine (bare or via
    `uv run`), on an allowlisted lane. Config is operator-owned (same trust model as commands.rs),
    but a server should still refuse to become a generic command runner."""
    if cmd == "hn":
        tool_args = args
    elif cmd == "uv" and args[:1] == ["run"] and "hn" in args:
        tool_args = args[args.index("hn") + 1 :]
    else:
        raise CommandError(f"command not allowlisted: {cmd} {' '.join(args[:2])}")
    lane = tool_args[0] if tool_args else ""
    if lane not in ALLOWED_LANES:
        raise CommandError(f"lane not allowlisted: {lane or '(none)'}")


_TODAY_RE = re.compile(r"\{today(?:\+(\d+))?\}")


def _substitute_dates(args: list[str]) -> list[str]:
    # {today}/{today+N} arg tokens (surfaces need live dates a static config can't express) —
    # mirrors commands.rs substitute_dates.
    def sub(m: re.Match[str]) -> str:
        return (date.today() + timedelta(days=int(m.group(1) or 0))).isoformat()

    return [_TODAY_RE.sub(sub, a) for a in args]


# ————— the spawn cache — cold dashboard load was minutes, not seconds ——————————
# Three cooperating layers over the same key (the RESOLVED spawn tuple):
#   1. in-flight dedupe — concurrent identical calls share ONE subprocess (a dashboard fans out
#      the same networth verb from several widgets; the server twin of the webview promise-dedupe)
#   2. short-TTL result cache — repeat loads (a second tab, the phone, a poll tick) serve warm
#      results without a spawn; TTL is deliberately shorter than the fastest widget cadence
#      (market10m), so staleness is bounded well inside what the native console already shows
#   3. both are process-local + size-bounded — a serve restart clears them by construction
_CACHE_TTL_S = 120.0
_CACHE_MAX = 256
_spawn_gate = threading.Lock()
_spawn_cache: dict[tuple[str, ...], tuple[float, str]] = {}
_inflight: dict[tuple[str, ...], Future[str]] = {}


def _cached_spawn(key: tuple[str, ...], execute: Callable[[], str]) -> str:
    now = time.monotonic()
    with _spawn_gate:
        hit = _spawn_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        fut = _inflight.get(key)
        if fut is None:
            fut = Future()
            _inflight[key] = fut
            owner = True
        else:
            owner = False
    if not owner:
        return fut.result(timeout=180)  # ride the owner's spawn
    try:
        result = execute()
        with _spawn_gate:
            if len(_spawn_cache) >= _CACHE_MAX:
                _spawn_cache.clear()  # crude but sufficient: the set re-warms in one dashboard load
            _spawn_cache[key] = (time.monotonic() + _CACHE_TTL_S, result)
        fut.set_result(result)
        return result
    except BaseException as exc:  # errors propagate to every rider, never cached
        fut.set_exception(exc)
        raise
    finally:
        with _spawn_gate:
            _inflight.pop(key, None)


def _run_json_command(cmd: str, args: list[str], cwd: str, label: str) -> str:
    """Spawn a registered read-only command and return its validated JSON stdout — the shared
    engine behind surfaces and command-widgets, now cached + deduped. A registered
    command MUST emit JSON; config errors surface loudly, never as blank panels."""
    _validate_tool(cmd, args)
    args = _substitute_dates(args)
    key = (cmd, *args, cwd)
    return _cached_spawn(key, lambda: _execute_json_command(cmd, args, cwd, label))


def _execute_json_command(cmd: str, args: list[str], cwd: str, label: str) -> str:
    import os

    # Spawn-cost cut: `uv run hn …` pays uv's project resolution on EVERY spawn; a resolvable `hn`
    # console-script runs the same code directly. shutil.which (not a hardcoded ~/.local/bin path)
    # so it holds in every context: an editable install on a dev machine AND the container image,
    # where hn lives in the venv on PATH and uv doesn't exist at runtime AT ALL — there the rewrite
    # isn't an optimization, it's what makes uv-configured widgets work. Absent → as configured.
    import shutil

    hn = shutil.which("hn")
    if cmd == "uv" and args[:1] == ["run"] and "hn" in args and hn:
        cmd, args = hn, args[args.index("hn") + 1 :]

    env = dict(os.environ)
    # The augmented_path() port (commands.rs) — instance #5 of the minimal-launch-context class:
    # under launchd this server gets /usr/bin:/bin:… with no /opt/homebrew/bin or ~/.local/bin, so
    # a bare `uv` spawn dies FileNotFoundError (caught when the server ran under launchd). Fix in the
    # spawn helper, NEVER the plist (autostart managers rewrite plists; the env belongs to code).
    home = Path.home()
    tool_dirs = [str(home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    current = env.get("PATH", "")
    prepend = [d for d in tool_dirs if Path(d).is_dir() and d not in current.split(":")]
    if prepend:
        env["PATH"] = ":".join([*prepend, current]) if current else ":".join(prepend)
    # Scenario parity with the native console: an active pack shapes on-demand panel reads.
    pack = str(_app_config().get("active_pack") or "").strip()
    if pack:
        env["WEIGHTS_PACK"] = str(Path(pack).expanduser())
    try:
        out = subprocess.run(# noqa: S603 — allowlisted above; config is operator-owned
            [cmd, *args],
            cwd=Path(cwd).expanduser() if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"{cmd} failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"{label} timed out") from exc
    if out.returncode != 0:
        raise CommandError(f"{label} exited {out.returncode}: {out.stderr[:800]}")
    trimmed = out.stdout.strip()
    try:
        json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise CommandError(f"{label} did not emit valid JSON: {exc}") from exc
    return trimmed


# ————— per-command handlers (each returns its Rust twin's exact result type) ———————————————————


def _event_wire(e: Any) -> dict[str, Any]:
    # The webview contract carries the payload as the `payload_json` STRING (the Rust bus::Event
    # shape; remote.rs does the same mapping when the native app reads a served bus).
    d: dict[str, Any] = e.model_dump()
    d["payload_json"] = json.dumps(d.pop("payload"))
    return d


def _h_list_events(args: dict[str, Any]) -> list[dict[str, Any]]:
    svc = BusService()
    try:
        events = svc.list_events(
            unread_only=bool(args.get("unreadOnly", False)),
            lane=args.get("lane") or None,
            kind=args.get("kind") or None,
            limit=int(args.get("limit") or 100),
        )
        return [_event_wire(e) for e in events]
    finally:
        svc.close()


def _h_ack_events(args: dict[str, Any]) -> int:
    ids = args.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise CommandError('"ids" must be a list of integers')
    svc = BusService()
    try:
        return svc.ack(ids)
    finally:
        svc.close()


def _h_unread_count(_: dict[str, Any]) -> int:
    svc = BusService()
    try:
        return svc.unread_count()
    finally:
        svc.close()


def _h_distinct_meta(_: dict[str, Any]) -> dict[str, list[str]]:
    # Mirror of bus::distinct_meta (Rust queries SQL directly; BusService has no twin yet).
    with sqlite3.connect(default_db_path()) as conn:
        lanes = [r[0] for r in conn.execute("SELECT DISTINCT lane FROM events ORDER BY lane")]
        kinds = [r[0] for r in conn.execute("SELECT DISTINCT kind FROM events ORDER BY kind")]
    return {"lanes": lanes, "kinds": kinds}


def _h_app_version(_: dict[str, Any]) -> str:
    # The SERVER's version (harness), not a Tauri bundle's — the amber footer renders it; honest
    # about which binary answered.
    return __version__


def _h_get_config(_: dict[str, Any]) -> dict[str, Any]:
    cfg = _app_config()
    db = str(default_db_path())
    return {"db_path": db, "bus_source": f"served: {db}", "producers": cfg.get("producers", [])}


def _h_list_surfaces(_: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces = _app_config().get("surfaces", [])
    return surfaces if isinstance(surfaces, list) else []


def _h_run_surface(args: dict[str, Any]) -> str:
    sid = str(args.get("id") or "")
    surface = next((s for s in _h_list_surfaces({}) if s.get("id") == sid), None)
    if surface is None:
        raise CommandError(f"unknown surface: {sid}")
    return _run_json_command(
        str(surface.get("cmd", "")),
        [str(a) for a in surface.get("args", [])],
        str(surface.get("cwd", "")),
        str(surface.get("label", sid)),
    )


def _dashboards() -> list[dict[str, Any]]:
    # Reads what's ON DISK (the native console owns seeding compiled defaults — a machine that
    # never ran the Tauri app lists empty). Lane-alphabetical mirrors dash.rs load_all ordering.
    out: list[dict[str, Any]] = []
    dash_dir = _config_dir() / "dashboards"
    for p in sorted(dash_dir.glob("*.json")):
        try:
            loaded = json.loads(p.read_text())
            if isinstance(loaded, dict):
                out.append(loaded)
        except (OSError, json.JSONDecodeError):
            continue  # one corrupt file is a skipped tab, never a dead dashboard list
    return out


def _h_list_dashboards(_: dict[str, Any]) -> list[dict[str, Any]]:
    return _dashboards()


def _h_run_widget(args: dict[str, Any]) -> str:
    lane, wid = str(args.get("lane") or ""), str(args.get("id") or "")
    symbol = args.get("symbol")
    widget = next(
        (
            w
            for d in _dashboards()
            if d.get("lane") == lane
            for w in d.get("widgets", [])
            if w.get("id") == wid
        ),
        None,
    )
    if widget is None:
        raise CommandError(f"unknown widget: {lane}/{wid}")
    # Parameterized widgets: the symbol must be one the CONFIG declares — the client selects from
    # a closed list, it never injects arguments (the commands.rs rule, ported).
    sym: str | None = None
    if symbol is not None:
        if symbol not in (widget.get("symbols") or []):
            raise CommandError(f"symbol {symbol} not in widget config")
        sym = str(symbol)
    source = widget.get("source") or {}
    kind = source.get("type")
    if kind == "command":
        raw_args = [str(a) for a in source.get("args", [])]
        if sym is not None:
            raw_args = [a.replace("{symbol}", sym) for a in raw_args]
        return _run_json_command(
            str(source.get("cmd", "")), raw_args, str(source.get("cwd", "")),
            str(widget.get("title", wid)),
        )
    if kind == "file":
        return _read_vault_file(str(source.get("path", "")))
    raise CommandError("bus sources resolve webview-side")


def _h_list_vault_docs(_: dict[str, Any]) -> list[dict[str, Any]]:
    vault = _vault_root()
    out: list[dict[str, Any]] = []

    def walk(d: Path, depth: int) -> None:
        if depth > VAULT_MAX_DEPTH:
            return
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for p in entries:
            # never follow symlinks (a symlink can point outside the vault; a listed-but-
            # unreadable entry is worse than absent — the commands.rs rule)
            if p.is_symlink():
                continue
            name = p.name
            if p.is_dir():
                if not name.startswith(".") and name not in VAULT_SKIP:
                    walk(p, depth + 1)
                continue
            ext = p.suffix.lower().lstrip(".")
            is_md, is_img = ext == "md", ext in IMG_MIME
            if not is_md and not is_img:
                continue
            rel = str(p.relative_to(vault))
            title = _h1_title(p) if is_md else name
            out.append(
                {
                    "path": rel,
                    "area": rel.split("/", 1)[0],
                    "dir": str(p.parent.relative_to(vault)) if p.parent != vault else "",
                    "name": p.stem,
                    "title": title,
                    "kind": "doc" if is_md else "image",
                }
            )

    walk(vault, 0)
    out.sort(key=lambda d: d["path"])
    return out


def _h1_title(p: Path) -> str:
    try:
        for line in p.read_text().splitlines():
            if line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
    except OSError:
        pass
    return p.stem


def _h_read_doc(args: dict[str, Any]) -> str:
    return _read_vault_file(str(args.get("path") or ""))


def _h_list_vault_dir(args: dict[str, Any]) -> list[dict[str, Any]]:
    rel = str(args.get("path") or "")
    try:
        d = resolve_vault_path(rel)
    except CommandError:
        return []  # missing dir is empty, not an error (fresh machine before the first take)
    if not d.is_dir():
        return []
    base = rel.rstrip("/")
    out = [
        {"path": f"{base}/{p.name}", "name": p.stem, "title": _h1_title(p)}
        for p in d.iterdir()
        if not p.is_symlink() and p.suffix.lower() == ".md"
    ]
    out.sort(key=lambda x: x["name"], reverse=True)  # newest-first on timestamped names
    return out


def _h_read_image(args: dict[str, Any]) -> str:
    path = str(args.get("path") or "")
    canon = resolve_vault_path(path)
    mime = IMG_MIME.get(canon.suffix.lower().lstrip("."))
    if mime is None:
        raise CommandError(f"unsupported image type: {canon.suffix or '?'}")
    data = canon.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise CommandError(f"image too large ({len(data)} bytes)")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ————— viz mirrors (the viz.rs discover/sniff port) ————————————————————————————————————————————

# Mirrors viz.rs SKIP_DIRS/MAX_DEPTH — keep in sync (three-surface contract).
VIZ_SKIP = {".git", ".obsidian", ".beads", "node_modules", "tmp", "screenshots"}
VIZ_MAX_DEPTH = 7
VIZ_SUPPORTED = {
    "sankey", "treemap", "pies", "line", "matrix", "compare", "schedule", "food-bank",
    "scatter", "rank-bar", "vest-timeline", "ladder", "bead-tree",
}


def _sniff(v: Any) -> str:
    """The viz.rs shape-sniffer, ported rule-for-rule (most-specific signatures first — the
    documented sniff-order discipline; a reorder here re-introduces the radial→line mis-sniff)."""
    if not isinstance(v, dict):
        return "unknown"
    if "windows" in v and "vests" in v:
        return "vest-timeline"
    symbols = v.get("symbols")
    if isinstance(symbols, list) and symbols and isinstance(symbols[0], dict) and "rungs" in symbols[0]:
        return "ladder"
    if isinstance(v.get("beads"), list) and isinstance(v.get("edges"), list):
        return "bead-tree"
    nodes = v.get("nodes")
    if nodes is not None and "links" in v:
        return "sankey"
    if isinstance(nodes, list) and nodes and all(isinstance(n, dict) and "value" in n for n in nodes):
        return "treemap"
    if "pies" in v:
        return "pies"
    if "restaurants" in v:
        return "food-bank"
    if "dayStart" in v and ("items" in v or "availability" in v):
        return "schedule"
    if "axes" in v and "candidates" in v:
        return "compare"
    if "axes" in v and "rows" in v:
        return "matrix"
    rows = v.get("rows")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "parts" in rows[0]:
        return "rank-bar"
    if "rings" in v and "points" in v:
        return "radial"
    pts = v.get("points")
    if (
        isinstance(pts, list)
        and pts
        and isinstance(pts[0], dict)
        and isinstance(pts[0].get("x"), int | float)
        and "group" in pts[0]
    ):
        return "scatter"
    if "points" in v or "series" in v:
        return "line"
    return "unknown"


def _h_list_viz(_: dict[str, Any]) -> list[dict[str, Any]]:
    # live entries (config-registered commands) first, then vault discovery — the commands.rs order
    out: list[dict[str, Any]] = [
        {
            "path": f"live:{lv.get('id', '')}",
            "doc": f"{lv.get('lane', '')} · LIVE",
            "name": str(lv.get("label", "")),
            "viz_type": str(lv.get("viz_type", "")),
            "title": str(lv.get("label", "")),
            "supported": True,
        }
        for lv in _app_config().get("live_viz", [])
        if isinstance(lv, dict)
    ]
    vault = _vault_root()
    found: list[dict[str, Any]] = []

    def walk(d: Path, depth: int) -> None:
        if depth > VIZ_MAX_DEPTH:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        # a data JSON is a viz candidate only where the co-located convention holds: its dir
        # carries a visuals/ sibling (the two-consumer contract's on-disk signature)
        has_visuals = any(c.is_dir() and c.name == "visuals" for c in children)
        for c in children:
            if c.is_dir():
                if c.name not in VIZ_SKIP:
                    walk(c, depth + 1)
                continue
            if not has_visuals or c.suffix != ".json":
                continue
            try:
                value = json.loads(c.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            viz_type = _sniff(value)
            found.append(
                {
                    "path": str(c.relative_to(vault)),
                    "doc": str(d.relative_to(vault)) if d != vault else "",
                    "name": c.stem,
                    "viz_type": viz_type,
                    "title": str(value.get("title", "")) if isinstance(value, dict) else "",
                    "supported": viz_type in VIZ_SUPPORTED,
                }
            )

    walk(vault, 0)
    found.sort(key=lambda e: (not e["supported"], e["doc"], e["name"]))
    return out + found


def _h_read_viz(args: dict[str, Any]) -> str:
    path = str(args.get("path") or "")
    if path.startswith("live:"):
        lid = path.removeprefix("live:")
        lv = next(
            (x for x in _app_config().get("live_viz", []) if isinstance(x, dict) and x.get("id") == lid),
            None,
        )
        if lv is None:
            raise CommandError(f"unknown live viz: {lid}")
        return _run_json_command(
            str(lv.get("cmd", "")), [str(a) for a in lv.get("args", [])],
            str(lv.get("cwd", "")), str(lv.get("label", lid)),
        )
    return _read_vault_file(path)


def _h_list_packs(_: dict[str, Any]) -> list[dict[str, Any]]:
    return []  # the web console serves the real corpus; scenario packs are the native app's


def _h_get_active_pack(_: dict[str, Any]) -> None:
    return None


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "list_events": _h_list_events,
    "ack_events": _h_ack_events,
    "unread_count": _h_unread_count,
    "distinct_meta": _h_distinct_meta,
    "app_version": _h_app_version,
    "get_config": _h_get_config,
    "list_surfaces": _h_list_surfaces,
    "run_surface": _h_run_surface,
    "list_dashboards": _h_list_dashboards,
    "run_widget": _h_run_widget,
    "list_vault_docs": _h_list_vault_docs,
    "read_doc": _h_read_doc,
    "list_vault_dir": _h_list_vault_dir,
    "read_image": _h_read_image,
    "list_packs": _h_list_packs,
    "get_active_pack": _h_get_active_pack,
    "list_viz": _h_list_viz,
    "read_viz": _h_read_viz,
}
# 403 — desktop-console features + the Studio save/reset: native-only for now; an authed
# write carve-out for served-console dashboard edits is tracked.
WRITE_GATED = {"set_active_pack", "run_producer", "save_dashboard", "reset_dashboard"}
NOT_YET: set[str] = set()  # empty today; kept for the next unmirrored command


def ui_mounts(specs: list[str]) -> list[Any]:
    """Static UI mount table — the multi-console affordance (one server should
    serve many consoles). Each spec is either a bare DIR (mounted at `/` — the default console) or
    `name=DIR` (mounted at `/ui/<name>/` — variants: a phone-tuned build, an A/B candidate, a
    `next` build soak-testing before promotion). Mount order rides the caller's route order, and
    the `/` mount must come LAST overall so `/api/*` always wins — `console_routes() + ui_mounts()`
    in that order gives Starlette first-match-wins correctness for free.

    ⚠ Variant builds must be Vite-built with `--base=/ui/<name>/` or their asset URLs resolve
    against `/` and load the DEFAULT console's bundle — the silent way to think you're A/B testing
    two consoles while serving one.
    """
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    named: list[Any] = []
    root: list[Any] = []
    for spec in specs:
        name, _, raw = spec.partition("=")
        if raw:  # name=DIR → a named variant
            directory = Path(raw).expanduser()
            named.append(Mount(f"/ui/{name}", app=StaticFiles(directory=directory, html=True)))
        else:  # bare DIR → the default console at /
            directory = Path(name).expanduser()
            root.append(Mount("/", app=StaticFiles(directory=directory, html=True)))
    if len(root) > 1:
        raise ValueError("only one bare DIR --ui spec may mount the root console")
    return named + root  # root last — it swallows everything after the API routes


def console_routes(token: str) -> list[Route]:
    """The RPC door, ready to mount into the bus server's `create_app(extra_routes=…)` — one
    server, one token, one bind."""
    from harness.bus.server import _authorized, _unauthorized

    async def invoke(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        cmd = request.path_params["cmd"]
        if cmd in WRITE_GATED:
            return JSONResponse(
                {"error": f"{cmd} is write-gated on the web console (read-only by design)"},
                status_code=403,
            )
        if cmd in NOT_YET:
            return JSONResponse({"error": f"{cmd} is not mirrored yet"}, status_code=501)
        handler = HANDLERS.get(cmd)
        if handler is None:
            return JSONResponse({"error": f"unknown command: {cmd}"}, status_code=404)
        body = await request.body()
        try:
            args: Any = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(args, dict):
            return JSONResponse({"error": "body must be a JSON object of args"}, status_code=400)
        try:
            # run_in_threadpool — the spawn_blocking port: a sync handler (subprocess, filesystem
            # walk) must never sit on the event loop, or concurrent widget calls SERIALIZE (the
            # native app's nine-queued-sync-commands lockup, reproduced server-side as the 2-min
            # cold dashboard load). The pool gives true widget concurrency; the
            # dedupe above keeps identical spawns from multiplying under it.
            from starlette.concurrency import run_in_threadpool

            return JSONResponse(await run_in_threadpool(handler, args))
        except CommandError as exc:
            # The Tauri failure shape: invoke rejects with the command's error string — the
            # transport turns this into the same rejection the native path produces.
            return JSONResponse({"error": str(exc)}, status_code=400)

    return [Route("/api/invoke/{cmd}", invoke, methods=["POST"])]
