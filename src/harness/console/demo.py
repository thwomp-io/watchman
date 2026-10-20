"""`hn console demo` — the whole console on the bundled sample personas, one command, nothing real.

Stages a scratch root (config, state, cache, bus and an empty corpus floor), points every harness
env knob at it, activates a sample pack, and serves the console with exactly one write enabled:
switching packs. A bundled pack is fully sealed (see `scenario`), and the corpus floor is an empty
dir, so even "no pack" can never fall back to a real corpus that happens to exist on the machine.
Each pack's scripted CHAT conversation plays under the same seal; it never calls a model.
Ctrl-C stops the server and removes the scratch root.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import typer

from harness.packs import bundled_packs_dir, discover_bundled_packs
from harness.paths import REPO_ROOT

app = typer.Typer(no_args_is_help=True, help="The web console: a sealed demo on the sample packs.")

DEMO_WRITES = frozenset({"set_active_pack"})
_UI_CANDIDATES = (REPO_ROOT / "bus-app" / "dist", Path("/app/ui"))


def resolve_ui(explicit: Path | None) -> Path:
    """The built console UI: --ui, HARNESS_UI_DIR, this checkout's bus-app/dist, the image's /app/ui."""
    env = os.environ.get("HARNESS_UI_DIR", "").strip()
    for cand in (explicit, Path(env) if env else None, *_UI_CANDIDATES):
        if cand is not None and (cand.expanduser() / "index.html").is_file():
            return cand.expanduser()
    raise typer.BadParameter(
        "no built console UI found — run `npm run build` in bus-app/, pass --ui DIR, "
        "or use the container image (it ships the UI)."
    )


def default_pack() -> Path:
    packs = discover_bundled_packs()
    if not packs:
        raise typer.BadParameter(f"no sample packs under {bundled_packs_dir()}")
    chosen = next((p for p in packs if p.default), packs[0])
    return Path(chosen.path)


def stage(pack: Path) -> tuple[Path, dict[str, str], str]:
    """Create the scratch root and the sealed environment. Returns (root, env, token)."""
    root = Path(tempfile.mkdtemp(prefix="watchman-demo-"))
    config = root / "config"
    (config / "dashboards").mkdir(parents=True)
    (config / ".env").write_text("# demo: no provider keys\n")
    (config / "bus-app.json").write_text(json.dumps({"active_pack": str(pack)}, indent=2))
    (root / "corpus").mkdir()
    token = secrets.token_urlsafe(24)
    token_file = root / "token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    env = {
        "TRACKER_PATH": str(root / "corpus"),
        "HARNESS_CONFIG_DIR": str(config),
        "HARNESS_STATE_DIR": str(root / "state"),
        "HARNESS_CACHE_DIR": str(root / "cache"),
        "HARNESS_BUS_DB": str(root / "state" / "bus.db"),
        "HARNESS_CONSOLE_DEMO": "1",
    }
    return root, env, token


class _Replay:
    """The active pack's scripted CHAT, restarted whenever the pack changes."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.proc: subprocess.Popen[bytes] | None = None
        self.pack: Path | None = None
        self.stop = threading.Event()

    def _start(self, pack: Path) -> None:
        self._kill()
        self.pack = pack
        script = pack / "pocket" / "replay.json"
        if script.is_file():
            cmd = [sys.executable, "-m", "harness.cli", "pocket", "replay", "--script", str(script)]
            self.proc = subprocess.Popen(  # noqa: S603 — our own engine, fixed args
                cmd, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def watch(self) -> None:
        from harness.console import scenario

        while not self.stop.wait(2.0):
            pack = scenario.active_pack()
            if pack is not None and pack != self.pack:
                self._start(pack)

    def run(self, first: Path) -> None:
        self._start(first)
        threading.Thread(target=self.watch, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self._kill()


@app.command()
def demo(
    pack: str = typer.Option("", "--pack", help="Sample pack to open on (default: the pack marked default)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (0.0.0.0 inside a container)."),
    port: int = typer.Option(8787, "--port"),
    ui: Path | None = typer.Option(None, "--ui", help="Built console UI dir (auto-detected)."),
    replay: bool = typer.Option(True, "--replay/--no-replay", help="Play each pack's scripted CHAT."),
    open_browser: bool = typer.Option(False, "--open", help="Open the console in the default browser."),
) -> None:
    """Serve the console on the bundled sample personas: fictional data, nothing real reachable."""
    import uvicorn

    first = (bundled_packs_dir() / pack) if pack else default_pack()
    if not (first / "pack.yaml").is_file():
        names = ", ".join(p.name for p in discover_bundled_packs())
        raise typer.BadParameter(f"unknown pack '{pack}' — choose from: {names}")
    ui_dir = resolve_ui(ui)
    root, env, token = stage(first)
    os.environ.update(env)

    from harness.bus.server import create_app
    from harness.console.api import console_routes, ui_mounts

    shown = "localhost" if host in ("0.0.0.0", "::") else host  # noqa: S104 — display only
    url = f"http://{shown}:{port}/?token={token}"
    typer.echo(f"Watchman demo — sample pack: {first.name} (switch personas from the header)")
    typer.echo(f"open: {url}")
    typer.echo("fictional data only; Ctrl-C stops the demo and deletes its scratch files")
    player = _Replay({**os.environ, **env}) if replay else None
    if player:
        player.run(first)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # uvicorn re-raises a captured SIGTERM after its graceful shutdown; a handler that exits
    # normally (instead of the default kill) lets the cleanup below run (`docker stop`, pkill).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        extra = [*console_routes(token, writes=DEMO_WRITES), *ui_mounts([str(ui_dir)])]
        uvicorn.run(create_app(token=token, extra_routes=extra), host=host, port=port, log_level="warning")
    finally:
        if player:
            player.close()
        shutil.rmtree(root, ignore_errors=True)
