"""`hn pocket` — the pocket agent's runner and its operator verbs.

`run` is what the launchd unit starts; the rest are the operator's controls from a terminal (the
Chat tab drives the same spool through the console door).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from harness.pocket import config as pocket_config
from harness.pocket.runner import Runner
from harness.pocket.store import PocketStore

app = typer.Typer(no_args_is_help=True, help="The pocket agent — a read-only phone chat with an agent lane.")


@app.command()
def run(once: bool = typer.Option(False, help="Answer at most one queued prompt, then exit."),
        no_live_check: bool = typer.Option(
            False, "--no-live-check", help="Skip the live seal probe (static checks still run).")) -> None:
    """Seal, self-test, then serve queued prompts until stopped (the launchd unit's entry point)."""
    cfg = pocket_config.load()
    if no_live_check:
        cfg.selftest_on_start = False
    raise typer.Exit(Runner(cfg).serve(once=once))


@app.command()
def selftest(live: bool = typer.Option(True, help="Also run the live probe (one cheap model call)."),
             as_json: bool = typer.Option(False, "--json")) -> None:
    """Check the seal without serving: the invocation, the settings, auth, and (by default) a live
    probe that tries to read a canary and write a file. Exit 1 on any failure."""
    runner = Runner(pocket_config.load())
    rep = runner.seal_up(live=live)
    if as_json:
        print(json.dumps({"ok": rep.ok, "checks": rep.checks}, indent=2))
    else:
        for c in rep.checks:
            mark = "ok  " if c["ok"] else "FAIL"
            print(f"{mark} {c['check']}" + (f" — {c['detail']}" if c["detail"] else ""))
        print("SEALED" if rep.ok else "NOT SEALED")
    raise typer.Exit(0 if rep.ok else 1)


@app.command()
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    """The runner's state: online, the status pill's data, queued prompts."""
    snap = PocketStore().snapshot(since=10**12)
    snap.pop("entries", None)
    if as_json:
        print(json.dumps(snap, indent=2))
        return
    s = snap["status"]
    lim = s.get("limits") or {}
    five = (lim.get("five_hour") or {}).get("utilization")
    print(f"runner   {'online' if snap['online'] else 'OFFLINE'} · {s.get('state', '—')}")
    print(f"seal     {'ok' if (s.get('seal') or {}).get('ok') else 'NOT OK'}")
    print(f"model    {s.get('model', '—')} · context {s.get('context_used', 0)}/{s.get('context_max', '—')}")
    print(f"5h       {'—' if five is None else f'{five:.0%}'} · cost (est.) ${s.get('cost_usd', 0)}")
    print(f"queued   {len(snap['queued'])}")


@app.command()
def send(text: str) -> None:
    """Queue a prompt (the same spool the Chat tab writes)."""
    item = PocketStore().enqueue(text)
    print(item["id"])


@app.command()
def tail(since: int = typer.Option(0, help="Print entries after this sequence number.")) -> None:
    """Print the conversation transcript."""
    for e in PocketStore().entries(since=since, limit=10**6):
        role = e.get("role")
        label = e.get("tool") or ""
        print(f"[{e['seq']}] {role}{(' ' + label) if label else ''}: {e.get('text', '')}")


@app.command()
def stop() -> None:
    """Ask the runner to stop the turn in progress."""
    PocketStore().request("stop")


@app.command()
def reset() -> None:
    """Start a new conversation (the transcript is archived, never deleted)."""
    PocketStore().request("reset")


@app.command("show-command")
def show_command() -> None:
    """Print the exact sealed invocation and settings (for review)."""
    runner = Runner(pocket_config.load())
    rep = runner.prepare()
    print(" ".join(runner.command()))
    print(runner.settings_path.read_text())
    if not rep.ok:
        print("static checks failing:\n  " + "\n  ".join(rep.failures()), file=sys.stderr)


@app.command()
def replay(script: Path = typer.Option(
               None, "--script", help="Replay script (default: <corpus>/pocket/replay.json)"),
           autoplay: bool = typer.Option(
               False, "--autoplay", help="Post the script's own messages on a timer."),
           gap: float = typer.Option(4.0, "--gap", help="Seconds between autoplayed turns."),
           speed: float = typer.Option(1.0, "--speed", help="Playback speed multiplier."),
           once: bool = typer.Option(False, "--once", help="Exit after the last scripted turn.")) -> None:
    """Serve a scripted, model-free conversation to the CHAT tab (demos, sample packs, recordings).
    Uses the real parser and store; the status says `replay`. Never calls a model."""
    from harness.pocket.replay import Replayer, default_script, load_script
    from harness.settings import BaseToolkitSettings

    path = script or default_script(BaseToolkitSettings().tracker_path.expanduser())
    try:
        data = load_script(path)
    except (OSError, ValueError) as e:
        print(f"replay: {e}", file=sys.stderr)
        raise typer.Exit(2) from e
    raise typer.Exit(Replayer(data, speed=speed).serve(autoplay=autoplay, gap=gap, once=once))
