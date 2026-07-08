"""BusService — the core capability (CLI/MCP/producers are thin adapters over this).

Read+publish+ack, plus ``mark_delivered`` for the HTTP adapter. Delivery markers remain
transport-OWNED semantically (each transport appends only its own marker; spec in docs/BUS.md),
and a local transport still writes its marker directly (the Tauri app via rusqlite). But a
REMOTE transport (a watchman in ``bus_url`` mode, a future ntfy relay) has no file access —
the served bus must proxy that same write, so the service carries it. No producer or CLI verb
calls it; it exists for transports reaching the bus over HTTP.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from harness.bus.models import BusStats, Event, EventDraft, PublishResult, PushSubscription
from harness.bus.store import connect, default_db_path

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_event(row: sqlite3.Row) -> Event:
    payload: dict[str, Any] = json.loads(row["payload_json"]) if row["payload_json"] else {}
    delivered: list[str] = json.loads(row["delivered_via"]) if row["delivered_via"] else []
    return Event(
        id=row["id"], created_at=row["created_at"], producer=row["producer"], lane=row["lane"],
        kind=row["kind"], subject=row["subject"], title=row["title"], body=row["body"],
        payload=payload, severity=row["severity"], read_at=row["read_at"], delivered_via=delivered,
    )


class BusService:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_db_path()
        self._conn = connect(self._db_path)

    # -- publish ---------------------------------------------------------------------------------

    def publish(self, draft: EventDraft) -> PublishResult:
        """Durable publish; bus-side dedup via UNIQUE(idempotency_key) → published | duplicate."""
        key = draft.resolved_key(date.today().isoformat())
        cur = self._conn.execute(
            "INSERT INTO events (created_at, producer, lane, kind, subject, title, body, "
            "payload_json, severity, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(idempotency_key) DO NOTHING",
            (
                _utc_now(), draft.producer, draft.lane, draft.kind, draft.subject, draft.title,
                draft.body, json.dumps(draft.payload), draft.severity, key,
            ),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return PublishResult(status="duplicate", idempotency_key=key)
        # Web-push fan-out rides the publish hook (transports never re-poll for inserts) and ONLY
        # fires on a genuinely new row — a duplicate republish must not re-buzz a phone. Delivery
        # is best-effort by contract: any failure is a log line, never a failed publish.
        try:
            from harness.bus import push  # deferred: the module gates cheaply, but stay lean here

            push.notify_event(self._conn, draft)
        except Exception:  # noqa: BLE001 — the bus row is the durable truth; push is best-effort
            logger.warning("web push fan-out failed; publish unaffected", exc_info=True)
        return PublishResult(status="published", event_id=cur.lastrowid, idempotency_key=key)

    def publish_many(self, drafts: list[EventDraft]) -> list[PublishResult]:
        return [self.publish(d) for d in drafts]

    # -- read ------------------------------------------------------------------------------------

    def list_events(
        self,
        *,
        unread_only: bool = False,
        lane: str | None = None,
        kind: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        """Newest-first event listing with optional filters (since = ISO timestamp lower bound)."""
        clauses: list[str] = []
        params: list[Any] = []
        if unread_only:
            clauses.append("read_at IS NULL")
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", (*params, limit)
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def unread_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM events WHERE read_at IS NULL").fetchone()
        return int(row["n"])

    def urgent_unread_count(self) -> int:
        """Unread ALERT/WARN only — the to-do count. The severity doctrine: info (the catalyst
        wire) and filings are skim-streams that must never drive the urgent badge — a wire
        backlog screaming from the tray trains the operator to ignore it."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE read_at IS NULL"
            " AND severity IN ('alert','warn')"
        ).fetchone()
        return int(row["n"])

    # -- ack -------------------------------------------------------------------------------------

    def ack(self, ids: list[int]) -> int:
        """Mark events read (idempotent — already-read rows are untouched). Returns rows changed."""
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE events SET read_at = ? WHERE id IN ({marks}) AND read_at IS NULL",
            (_utc_now(), *ids),
        )
        self._conn.commit()
        return cur.rowcount

    def ack_all(self, lane: str | None = None) -> int:
        sql = "UPDATE events SET read_at = ? WHERE read_at IS NULL"
        params: list[Any] = [_utc_now()]
        if lane:
            sql += " AND lane = ?"
            params.append(lane)
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.rowcount

    # -- delivery markers --------------------------------------------------------------------------

    def mark_delivered(self, event_id: int, marker: str) -> list[str] | None:
        """Append a transport's delivery marker to one event (idempotent; never touches other
        transports' markers — the docs/BUS.md delivered_via rule). Returns the updated marker
        list, or None for an unknown event id. Identical semantics to the Rust app's local
        ``mark_delivered``; exists so remote transports can make the same write over HTTP."""
        row = self._conn.execute(
            "SELECT delivered_via FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        markers: list[str] = json.loads(row["delivered_via"]) if row["delivered_via"] else []
        if marker not in markers:
            markers.append(marker)
            self._conn.execute(
                "UPDATE events SET delivered_via = ? WHERE id = ?",
                (json.dumps(markers), event_id),
            )
            self._conn.commit()
        return markers

    # -- web-push subscriptions --------------------------------------------------------------------
    # Thin delegations over harness.bus.push's row helpers so every adapter (HTTP routes, CLI)
    # speaks BusService — the dual-surface convention — while the SQL lives with the transport.

    def add_push_subscription(
        self, *, endpoint: str, p256dh: str, auth: str, label: str = ""
    ) -> None:
        from harness.bus import push

        push.save_subscription(self._conn, endpoint=endpoint, p256dh=p256dh, auth=auth, label=label)

    def remove_push_subscription(self, endpoint: str) -> bool:
        from harness.bus import push

        return push.delete_subscription(self._conn, endpoint)

    def list_push_subscriptions(self) -> list[PushSubscription]:
        from harness.bus import push

        return push.list_subscriptions(self._conn)

    # -- hygiene ---------------------------------------------------------------------------------

    def purge(self, before: str, *, keep_unread: bool = True) -> int:
        """Delete events created before the ISO bound; unread survive unless keep_unread=False."""
        sql = "DELETE FROM events WHERE created_at < ?"
        if keep_unread:
            sql += " AND read_at IS NOT NULL"
        cur = self._conn.execute(sql, (before,))
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> BusStats:
        total = int(self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])
        by_lane = {
            str(r["lane"]): int(r["n"])
            for r in self._conn.execute("SELECT lane, COUNT(*) AS n FROM events GROUP BY lane")
        }
        by_kind = {
            str(r["kind"]): int(r["n"])
            for r in self._conn.execute("SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")
        }
        version_row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return BusStats(
            total=total,
            unread=self.unread_count(),
            urgent_unread=self.urgent_unread_count(),
            by_lane=by_lane,
            by_kind=by_kind,
            db_path=str(self._db_path),
            db_bytes=self._db_path.stat().st_size if self._db_path.exists() else 0,
            schema_version=str(version_row["value"]) if version_row else "?",
        )

    def close(self) -> None:
        self._conn.close()
