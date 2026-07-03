"""Typer CLI adapter for the bus — thin over BusService.

`hn bus publish` is the universal producer on-ramp: any script/language can publish without
touching Python internals (the OSS escape hatch). All verbs honor HARNESS_BUS_DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from harness.bus.models import EventDraft, Severity
from harness.bus.service import BusService

app = typer.Typer(
    add_completion=False,
    help="The harness message bus — durable human-event layer for standing agents. "
    "Producers publish; the tray app (and future transports) deliver. Spec: docs/BUS.md.",
)
console = Console()


def _payload_arg(raw: str) -> dict[str, object]:
    """--payload accepts inline JSON or @path/to/file.json."""
    text = Path(raw[1:]).read_text() if raw.startswith("@") else raw
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise typer.BadParameter("payload must be a JSON object")
    return loaded


@app.command()
def publish(
    title: str = typer.Option(..., "--title", help="Notification headline"),
    lane: str = typer.Option(..., "--lane", help="finance | career | travel | core | ..."),
    kind: str = typer.Option(..., "--kind", help="Event kind, e.g. day_move / scan_delta / test"),
    subject: str = typer.Option("", "--subject", help="Filterable subject, e.g. a ticker"),
    body: str = typer.Option("", "--body"),
    severity: str = typer.Option("info", "--severity", help="info | warn | alert"),
    payload: str = typer.Option("{}", "--payload", help="JSON object, or @file.json"),
    producer: str = typer.Option("manual", "--producer", help="e.g. finance.pulse / manual"),
    key: str = typer.Option("", "--key", help="Idempotency key; default producer:kind:subject:date"),
) -> None:
    """Publish one event (bus-side dedup: a repeated key reports duplicate, no new row)."""
    if severity not in ("info", "warn", "alert"):
        raise typer.BadParameter("severity must be info | warn | alert")
    sev: Severity = severity  # type: ignore[assignment]  # validated just above
    draft = EventDraft(
        producer=producer, lane=lane, kind=kind, subject=subject, title=title, body=body,
        payload=_payload_arg(payload), severity=sev, idempotency_key=key,
    )
    res = BusService().publish(draft)
    console.print(f"{res.status} · key={res.idempotency_key}"
                  + (f" · id={res.event_id}" if res.event_id else ""))


@app.command("list")
def list_cmd(
    unread: bool = typer.Option(False, "--unread", help="Unread only"),
    lane: str = typer.Option("", "--lane"),
    kind: str = typer.Option("", "--kind"),
    since: str = typer.Option("", "--since", help="ISO lower bound on created_at"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List events, newest first."""
    events = BusService().list_events(
        unread_only=unread, lane=lane or None, kind=kind or None, since=since or None, limit=limit
    )
    if as_json:
        console.print_json(json.dumps([e.model_dump() for e in events]))
        return
    table = Table(title=f"bus events ({len(events)})")
    for col in ("ID", "Created", "Lane", "Kind", "Subject", "Sev", "Title", "Read", "Via"):
        table.add_column(col)
    for e in events:
        table.add_row(
            str(e.id), e.created_at[:16].replace("T", " "), e.lane, e.kind, e.subject or "—",
            e.severity, e.title, "✓" if e.read_at else "●", ",".join(e.delivered_via) or "—",
        )
    console.print(table)


@app.command()
def ack(
    ids: list[int] = typer.Argument(None, help="Event IDs to mark read"),
    ack_all: bool = typer.Option(False, "--all", help="Ack every unread event"),
    lane: str = typer.Option("", "--lane", help="Scope --all to one lane"),
) -> None:
    """Mark events read (idempotent)."""
    svc = BusService()
    if ack_all:
        n = svc.ack_all(lane=lane or None)
    elif ids:
        n = svc.ack(list(ids))
    else:
        raise typer.BadParameter("give IDs or --all")
    console.print(f"acked {n}")


@app.command()
def stats(as_json: bool = typer.Option(False, "--json")) -> None:
    """Bus health: counts by lane/kind, unread, db path/size, schema version."""
    s = BusService().stats()
    if as_json:
        console.print_json(s.model_dump_json())
        return
    console.print(f"bus · {s.db_path} ({s.db_bytes:,}B, schema v{s.schema_version})")
    console.print(f"events {s.total} · unread {s.unread}")
    if s.by_lane:
        console.print("by lane: " + " · ".join(f"{k}={v}" for k, v in sorted(s.by_lane.items())))
    if s.by_kind:
        console.print("by kind: " + " · ".join(f"{k}={v}" for k, v in sorted(s.by_kind.items())))


@app.command()
def watchmen(as_json: bool = typer.Option(False, "--json")) -> None:
    """Standing-agent health: last/next run, today's cadence, missed runs."""
    from harness.bus.watchmen import compute_watchmen_status

    w = compute_watchmen_status(datetime.now())  # local naive time — matches the log + plist
    if as_json:
        console.print_json(w.model_dump_json())
        return
    dot = {"green": "●", "red": "○"}
    for a in w.agents:
        ticks = "".join({"ran": "▮", "missed": "▯", "pending": "▫"}[t.state] for t in a.cadence)
        console.print(
            f"{dot.get(a.state, '?')} {a.label}: last {a.last_run_rel or '—'} · "
            f"next {a.next_run or '—'} · {ticks} {a.runs_today}/{a.expected_by_now} · "
            f"{a.missed} missed"
        )
        if a.last_flags:
            console.print(f"  last flags: {a.last_flags}")


@app.command()
def purge(
    before: str = typer.Option(..., "--before", help="ISO timestamp OR Nd (e.g. 30d)"),
    include_unread: bool = typer.Option(False, "--include-unread", help="Also purge unread"),
) -> None:
    """Delete old events (read-only events by default; unread survive unless --include-unread)."""
    bound = before
    if before.endswith("d") and before[:-1].isdigit():
        cutoff = datetime.now(UTC) - timedelta(days=int(before[:-1]))
        bound = cutoff.isoformat(timespec="seconds")
    n = BusService().purge(bound, keep_unread=not include_unread)
    console.print(f"purged {n} (before {bound})")


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind address. Default localhost; bind a mesh/tailnet address (or 0.0.0.0) "
        "deliberately — every /api route requires the bearer token regardless.",
    ),
    port: int = typer.Option(8787, "--port"),
    token_file: Path = typer.Option(
        Path("~/.config/harness/bus-token"),
        "--token-file",
        help="Bearer-token file; auto-generated (0600) on first run. Clients send "
        "'Authorization: Bearer <token>'.",
    ),
) -> None:
    """Serve the bus over HTTP (the multi-device 'centralize, don't sync' layer).

    Purely additive: nothing else in the harness starts or needs this server. Run it on the
    always-on node; remote consoles/producers point at it. Spec: docs/BUS.md (Serving section).
    """
    import uvicorn

    from harness.bus.server import create_app, resolve_token
    from harness.bus.store import default_db_path

    resolved_file = token_file.expanduser()
    token = resolve_token(resolved_file)
    console.print(f"bus db: {default_db_path()}")
    console.print(f"token:  {resolved_file} (send as 'Authorization: Bearer …')")
    console.print(f"listen: http://{host}:{port}  (health: /health · api: /api/bus/*)")
    uvicorn.run(create_app(token=token), host=host, port=port, log_level="info")
