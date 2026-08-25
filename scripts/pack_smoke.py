"""Maintainer tool — run every command widget of every bundled sample pack's console, end to end.

The persona-completeness tests (tests/test_sample_packs.py) prove a pack SHIPS the files its
dashboards read. This tool proves the dashboards actually RENDER: for each pack, walk
`dashboards/*.json`, take every `command` widget, run its `hn …` verb against the pack (the same
spawn the console makes, `{today}`/`{today+N}` placeholders substituted the way the app does), and
report anything that exits non-zero or returns non-JSON. A dead lane fails here, not in a demo.

Why a script and not a test: the widgets hit live keyless providers (weather, quotes, filings) —
network-dependent and tens of seconds per pack, which is release-audit work, not CI work. Run it
before every public release (the demo-pack E2E step of the release audit):

    uv run python scripts/pack_smoke.py                 # all packs
    uv run python scripts/pack_smoke.py demo-investor   # one pack
    uv run python scripts/pack_smoke.py --list          # just print the widget matrix

Exit status 1 if any widget failed. File/doc_series widgets are checked for path existence only
(the console reads them straight off disk through the pack-aware vault guard).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "samples" / "packs"
_TODAY_RE = re.compile(r"\{today([+-]\d+)?\}")

# Lanes a File source maps into inside a pack (mirrors the console's pack-aware vault resolution:
# a `finance/…` path reads `<pack>/finance/…`, `role-hunt/…` reads `<pack>/career/…`).
_LANE_PREFIX = {"finance/": "finance/", "travel/": "travel/", "role-hunt/": "career/"}


def _subst_today(arg: str) -> str:
    def repl(m: re.Match[str]) -> str:
        off = int(m.group(1) or 0)
        return (dt.date.today() + dt.timedelta(days=off)).isoformat()

    return _TODAY_RE.sub(repl, arg)


def _pack_path(pack: Path, rel: str) -> Path | None:
    for prefix, lane_dir in _LANE_PREFIX.items():
        if rel.startswith(prefix):
            return pack / lane_dir / rel[len(prefix):]
    return None


def _run_widget(pack: Path, exe: str, args: list[str], timeout: int) -> tuple[bool, str]:
    # the widget's own spawn line, verbatim (`cmd` + `args` — e.g. `uv run hn finance gates --json`),
    # run from the repo root the way the console's engine cwd resolves
    cmd = [exe, *[_subst_today(a) for a in args]]
    env = dict(os.environ)
    # the console's seal: corpus + weights from the pack, state in a scratch dir (never the real
    # `~/.local/state/harness` — the earnings cache etc. must not touch the operator's state)
    env["WEIGHTS_PACK"] = str(pack)
    env["TRACKER_PATH"] = str(pack)
    env.setdefault("HARNESS_STATE_DIR", "/tmp/pack-smoke-state")
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["(no output)"]
        return False, f"exit {proc.returncode}: {tail[0][:160]}"
    if "--json" in args:
        try:
            json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return False, f"non-JSON stdout: {e}"
    return True, "ok"


def smoke_pack(pack: Path, *, list_only: bool, timeout: int) -> list[str]:
    failures: list[str] = []
    for dash in sorted((pack / "dashboards").glob("*.json")):
        d = json.loads(dash.read_text())
        for w in d.get("widgets", []):
            src = w.get("source") or {}
            kind = src.get("type")
            label = f"{pack.name}/{dash.stem}:{w.get('id')}"
            if kind == "bus":
                continue  # resolved webview-side against the (sealed) bus — nothing to spawn
            if kind == "command":
                exe, args = src.get("cmd", "uv"), list(src.get("args") or [])
                line = " ".join([exe, *args])
                if list_only:
                    print(f"  {label}  {line}")
                    continue
                # a per-symbol widget (the position chart) runs its command once per listed symbol
                # with `{symbol}` substituted — the console fans it out the same way
                symbols = list(w.get("symbols") or []) if any("{symbol}" in a for a in args) else [None]
                for sym in symbols:
                    sym_args = [a.replace("{symbol}", sym) if sym else a for a in args]
                    ok, msg = _run_widget(pack, exe, sym_args, timeout)
                    shown = f"{line}" + (f"  [{sym}]" if sym else "")
                    print(f"  {'✓' if ok else '✗'} {label}  {shown}  — {msg}")
                    if not ok:
                        failures.append(f"{label}{f' [{sym}]' if sym else ''}: {msg}")
            else:
                rel = src.get("path") or ""
                target = _pack_path(pack, rel)
                exists = target is not None and target.exists()
                if list_only:
                    print(f"  {label}  file {rel}")
                    continue
                verdict = "ok" if exists else "MISSING in pack"
                print(f"  {'✓' if exists else '✗'} {label}  file {rel}  — {verdict}")
                if not exists:
                    failures.append(f"{label}: file {rel} missing")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("packs", nargs="*", help="pack names (default: all bundled packs)")
    ap.add_argument("--list", action="store_true", help="print the widget matrix, run nothing")
    ap.add_argument("--timeout", type=int, default=120, help="per-widget timeout in seconds")
    ns = ap.parse_args()
    packs = [PACKS / n for n in ns.packs] if ns.packs else sorted(
        p for p in PACKS.iterdir() if (p / "pack.yaml").exists()
    )
    all_failures: list[str] = []
    for pack in packs:
        if not (pack / "pack.yaml").exists():
            print(f"no such pack: {pack}", file=sys.stderr)
            return 2
        print(f"== {pack.name}")
        all_failures += smoke_pack(pack, list_only=ns.list, timeout=ns.timeout)
    if ns.list:
        return 0
    print()
    if all_failures:
        print(f"FAIL — {len(all_failures)} widget(s) dead:")
        for f in all_failures:
            print(f"  - {f}")
        return 1
    print("PASS — every widget in every pack rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
