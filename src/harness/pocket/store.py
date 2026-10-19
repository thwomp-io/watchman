"""The pocket agent's on-disk state — one directory, one writer.

The runner is the only writer of the transcript, status and live files. The served console's door
writes exactly two things: prompt files into `queue/` and request flags (`stop`, `reset`). That split
is why the runner needs no network listener at all — it polls a directory — and why a crashed or
stopped runner leaves the console readable (it shows the last state and "runner offline").

Layout under `<state dir>/pocket/`:
  transcript.jsonl   append-only entries {seq, ts, role, text, …}
  status.json        the status pill's data (atomic replace)
  live.json          the reply being streamed right now (atomic replace; "" when idle)
  heartbeat          the runner's last-alive timestamp
  queue/*.json       prompts waiting for the runner (door → runner)
  stop / reset       request flags (door → runner)
  archive/           transcripts set aside by a reset
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.bus.store import harness_state_dir

HEARTBEAT_STALE_S = 20.0


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class PocketStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or harness_state_dir() / "pocket"
        self.queue = self.root / "queue"
        self.archive = self.root / "archive"
        self.transcript = self.root / "transcript.jsonl"
        self.status_path = self.root / "status.json"
        self.live_path = self.root / "live.json"
        self.heartbeat_path = self.root / "heartbeat"
        self.stop_flag = self.root / "stop"
        self.reset_flag = self.root / "reset"

    def ensure(self) -> None:
        for d in (self.root, self.queue, self.archive):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # ——— transcript (runner writes) ————————————————————————————————————————————————————————————

    def last_seq(self) -> int:
        if not self.transcript.exists():
            return 0
        last = 0
        with self.transcript.open(encoding="utf-8") as f:
            for line in f:
                try:
                    last = max(last, int(json.loads(line).get("seq", 0)))
                except (json.JSONDecodeError, ValueError, AttributeError):
                    continue
        return last

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.ensure()
        row = {"seq": self.last_seq() + 1, "ts": now_iso(), **entry}
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def entries(self, since: int = 0, limit: int = 400) -> list[dict[str, Any]]:
        if not self.transcript.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.transcript.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and int(row.get("seq", 0)) > since:
                    rows.append(row)
        return rows[-limit:]

    # ——— status + live (runner writes) ——————————————————————————————————————————————————————————

    def status(self) -> dict[str, Any]:
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def update_status(self, **fields: Any) -> dict[str, Any]:
        self.ensure()
        merged = {**self.status(), **fields, "updated_at": now_iso()}
        _atomic_write(self.status_path, json.dumps(merged, ensure_ascii=False))
        return merged

    def live(self) -> str:
        try:
            return str(json.loads(self.live_path.read_text(encoding="utf-8")).get("text", ""))
        except (OSError, json.JSONDecodeError, AttributeError):
            return ""

    def set_live(self, text: str) -> None:
        self.ensure()
        _atomic_write(self.live_path, json.dumps({"text": text}, ensure_ascii=False))

    def beat(self) -> None:
        self.ensure()
        _atomic_write(self.heartbeat_path, str(time.time()))

    def runner_online(self, now: float | None = None) -> bool:
        try:
            last = float(self.heartbeat_path.read_text().strip())
        except (OSError, ValueError):
            return False
        return ((now or time.time()) - last) < HEARTBEAT_STALE_S

    # ——— the door's two writes ——————————————————————————————————————————————————————————————————

    def enqueue(self, text: str) -> dict[str, Any]:
        self.ensure()
        item = {"id": uuid.uuid4().hex[:12], "ts": now_iso(), "text": text}
        name = f"{time.time_ns()}-{item['id']}.json"
        _atomic_write(self.queue / name, json.dumps(item, ensure_ascii=False))
        return item

    def queued(self) -> list[dict[str, Any]]:
        if not self.queue.is_dir():
            return []
        out = []
        for p in sorted(self.queue.glob("*.json")):
            try:
                item = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                out.append({**item, "_file": p.name})
        return out

    def take(self) -> dict[str, Any] | None:
        """Pop the oldest queued prompt (runner side)."""
        for item in self.queued():
            path = self.queue / item.pop("_file")
            try:
                path.unlink()
            except OSError:
                continue
            return item
        return None

    def request(self, flag: str) -> None:
        self.ensure()
        target = {"stop": self.stop_flag, "reset": self.reset_flag}[flag]
        target.write_text(now_iso())

    def pop_flag(self, flag: str) -> bool:
        target = {"stop": self.stop_flag, "reset": self.reset_flag}[flag]
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
            return True
        return False

    # ——— reset (runner side) ————————————————————————————————————————————————————————————————————

    def archive_conversation(self) -> Path | None:
        """Set the transcript aside and start a new conversation. Never deletes."""
        self.ensure()
        moved = None
        if self.transcript.exists() and self.transcript.stat().st_size:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            moved = self.archive / f"transcript-{stamp}.jsonl"
            os.replace(self.transcript, moved)
        keep = {k: v for k, v in self.status().items()
                if k in ("seal", "claude_version", "harness_version", "limits", "limit_status", "context_max",
                         "actor", "state", "mode")}
        _atomic_write(self.status_path, json.dumps({**keep, "updated_at": now_iso()}))
        self.set_live("")
        return moved

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """What the console reads: everything the Chat tab renders in one payload, shaped by the
        PocketSnapshot contract (unknown status keys are dropped, missing ones default)."""
        from harness.pocket.models import PocketSnapshot

        return PocketSnapshot.model_validate({
            "online": self.runner_online(),
            "status": self.status(),
            "entries": self.entries(since),
            "live": self.live(),
            "queued": [{"id": q["id"], "ts": q["ts"], "text": q["text"]} for q in self.queued()],
        }).model_dump()
