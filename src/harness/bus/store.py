"""SQLite store for the harness message bus.

Design contract (full spec: docs/BUS.md):

- One db at ``~/.local/state/harness/bus.db``; ``HARNESS_BUS_DB`` overrides (tests point at tmp,
  OSS users relocate freely). The db FILE is the cross-language contract — the Tauri app reads it
  directly via rusqlite; Python and Rust resolve the same path the same way.
- **WAL mode**: producers (Python, INSERT) and the app (Rust, UPDATE read_at/delivered_via) are
  concurrent writers on disjoint columns; WAL + busy_timeout=5000 makes contention a non-event.
- **Dedup is bus-side**: UNIQUE(idempotency_key); publish is INSERT .. ON CONFLICT DO NOTHING.
  Producers *compose* keys (``producer:kind:subject:YYYY-MM-DD``); the bus *enforces* them — so
  producer #2 (career watchman) inherits once-per-day semantics for free and producers stay
  stateless (supersedes any producer-side dedup cache).
- stdlib ``sqlite3`` only — no new dependency (API-over-library rule).
- ``push_subscriptions`` (web-push endpoints, harness.bus.push) is an ADDITIVE table riding the
  same idempotent DDL: both language surfaces create it so either can boot a fresh db, but only
  Python ever reads/writes it. Schema version stays 1 — CREATE IF NOT EXISTS self-migrates.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    producer        TEXT NOT NULL,
    lane            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    severity        TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warn', 'alert')),
    idempotency_key TEXT NOT NULL,
    read_at         TEXT,
    delivered_via   TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_idem ON events(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_events_unread ON events(read_at) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_events_lane_kind ON events(lane, kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint   TEXT PRIMARY KEY,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def harness_state_dir() -> Path:
    """The harness state dir (pulse.log, bus.db, pulse-flags.json). `HARNESS_STATE_DIR` seals it for a
    sandboxed instance (tests / CI): the Rust host passes it on every `hn` spawn so a
    spawned watchmen/pulse reads the SANDBOX state, not the real standing-agent log — else a real pulse
    flag leaks into the demo. The Rust->Python bridge for STATE, twin of `TRACKER_PATH` for the corpus.
    Default: the real `~/.local/state/harness`."""
    env = os.environ.get("HARNESS_STATE_DIR", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".local" / "state" / "harness"


def default_db_path() -> Path:
    """Resolve the bus db path: HARNESS_BUS_DB env override, else the harness state dir."""
    env = os.environ.get("HARNESS_BUS_DB", "").strip()
    if env:
        return Path(env).expanduser()
    return harness_state_dir() / "bus.db"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating dirs + schema idempotently) a WAL connection with row access by name."""
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
    )
    conn.commit()
    return conn
