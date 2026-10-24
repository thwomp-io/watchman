"""Typer CLI adapter for the bus — thin over BusService.

`hn bus publish` is the universal producer on-ramp: any script/language can publish without
touching Python internals (the OSS escape hatch). All verbs honor HARNESS_BUS_DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from harness._cli import fail
from harness.bus.models import EventDraft, Severity
from harness.bus.service import BusService

app = typer.Typer(
    add_completion=False,
    help="The harness message bus — durable human-event layer for standing agents. "
    "Producers publish; the tray app (and future transports) deliver. Spec: docs/BUS.md.",
)
console = Console()


_fail = partial(fail, mark="✗")


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
    before: str = typer.Option("", "--before", help="ISO timestamp OR Nd (e.g. 30d)"),
    include_unread: bool = typer.Option(False, "--include-unread", help="Also purge unread"),
    policy: str = typer.Option(
        "", "--policy",
        help="Apply the standing retention policy instead of a bound: `default` = catalyst >30d · "
        "read+delivered non-alert >90d · read alerts >1y; also rotates the logs `hn bus logs` lists",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count what the policy would delete"),
) -> None:
    """Delete old events. `--before` is the manual bound (read events only unless
    --include-unread); `--policy default` is what a weekly scheduled run uses."""
    if policy:
        if policy != "default":
            _fail(f"unknown policy: {policy} (only `default` exists)")
            raise typer.Exit(code=1)
        counts = BusService().purge_policy(dry_run=dry_run)
        verb = "would purge" if dry_run else "purged"
        console.print(
            f"{verb} catalyst>30d={counts['catalyst']} · settled>90d={counts['settled']} · "
            f"alerts>1y={counts['alerts']}"
        )
        from harness import logrotate

        for rotated in logrotate.rotate(dry_run=dry_run):
            console.print(f"{'would rotate' if dry_run else 'rotated'} {rotated}")
        return
    if not before:
        _fail("pass --before <bound> or --policy default")
        raise typer.Exit(code=1)
    bound = before
    if before.endswith("d") and before[:-1].isdigit():
        cutoff = datetime.now(UTC) - timedelta(days=int(before[:-1]))
        bound = cutoff.isoformat(timespec="seconds")
    n = BusService().purge(bound, keep_unread=not include_unread)
    console.print(f"purged {n} (before {bound})")


def serve_log_config() -> dict[str, Any]:
    """uvicorn's logging config with a timestamp on every line, and the harness's own loggers (web
    push, the door) routed through the same handler at INFO — without this they fall to logging's
    last-resort handler: WARNING-only and undated."""
    import copy

    from uvicorn.config import LOGGING_CONFIG

    cfg: dict[str, Any] = copy.deepcopy(LOGGING_CONFIG)
    stamp = "%(asctime)s "
    cfg["formatters"]["default"]["fmt"] = stamp + "%(levelprefix)s %(message)s"
    cfg["formatters"]["access"]["fmt"] = stamp + cfg["formatters"]["access"]["fmt"]
    for f in cfg["formatters"].values():
        f["datefmt"] = "%Y-%m-%dT%H:%M:%S%z"
    cfg["loggers"]["harness"] = {"handlers": ["default"], "level": "INFO", "propagate": False}
    return cfg


@app.command("logs")
def logs(
    rotate: bool = typer.Option(False, "--rotate", help="Rotate every log past the cap now."),
    dry_run: bool = typer.Option(False, "--dry-run", help="With --rotate: list, don't touch."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The logs under size-capped rotation (overlay `bus.log_rotation`: paths, cap_mb, keep): size,
    rotated copies, over-cap. The weekly `purge --policy default` rotates them; --rotate does it now."""
    from dataclasses import asdict

    from harness import logrotate

    pol = logrotate.policy()
    if rotate:
        for p in logrotate.rotate(dry_run=dry_run, pol=pol):
            console.print(f"{'would rotate' if dry_run else 'rotated'} {p}")
    rows = logrotate.status(pol)
    if as_json:
        console.print_json(data={"cap_bytes": pol.cap_bytes, "keep": pol.keep, "globs": pol.globs,
                                 "logs": [asdict(r) for r in rows]})
        return
    console.print(f"cap {pol.cap_bytes / 1_048_576:g} MB · keep {pol.keep} · {len(rows)} logs")
    for r in rows:
        mark = "[yellow]over[/yellow]" if r.over_cap else "[green]ok  [/green]"
        console.print(f"  {mark} {r.bytes / 1_048_576:7.2f} MB  {r.copies} gz  {r.path}")


@app.command("push-keys")
def push_keys() -> None:
    """Show (generating on first run) the web-push VAPID PUBLIC key + subscription inventory.

    The private key stays in the config dir (0600) and is never printed — this verb exists so the
    operator can eyeball the public key and see which devices are subscribed without SQL.
    """
    from harness.bus import push

    svc = BusService()
    console.print(f"public key: {push.vapid_public_key()}")
    console.print(f"key file:   {push.vapid_key_path()} (private — never share/commit)")
    subs = svc.list_push_subscriptions()
    console.print(f"subscriptions: {len(subs)}")
    for s in subs:
        host = s.endpoint.split("/")[2] if "://" in s.endpoint else "?"
        console.print(f"  · {s.label or '(unlabeled)'} — {host} — since {s.created_at}")


def _process_alive(pid: int) -> bool:
    """Whether a process exists. Windows needs a real query: there `os.kill(pid, 0)` sends CTRL_C_EVENT."""
    import os
    import sys

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)  # signal 0: existence check only
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _exit_with_parent() -> None:
    """A serve started by the desktop app (HARNESS_PARENT_PID) exits when the app is gone, however
    the app ended (a crash or a force-quit skips its own cleanup), so no orphan holds the port."""
    import os
    import threading
    import time

    raw = os.environ.get("HARNESS_PARENT_PID", "").strip()
    if not raw.isdigit():
        return
    parent = int(raw)

    def watch() -> None:
        while _process_alive(parent):
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


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
    web_console: bool = typer.Option(
        False,
        "--console/--no-console",
        help="Also mount the web-console RPC door (/api/invoke/{cmd} — read-only mirror of the "
        "native console's commands; the browser form-factor's backend). Off by default: the "
        "plain bus serve stays exactly what satellites already depend on.",
    ),
    ui: list[str] = typer.Option(  # noqa: B008 — typer option factory, the exempted pattern
        [],
        "--ui",
        help="Serve a built console UI (implies --console). Bare DIR mounts the default console "
        "at /; repeatable name=DIR mounts variants at /ui/<name>/ (A/B candidates, a phone-tuned "
        "build — variants must be Vite-built with --base=/ui/<name>/). One server, many consoles.",
    ),
    writes: str = typer.Option(
        "",
        "--writes",
        help="Comma-separated console writes this serve allows (default none: read-only): "
        "set_active_pack, save_dashboard, reset_dashboard, run_producer. Anyone holding the token "
        "gets them; corpus files are never writable through the console.",
    ),
    tls_cert: Path | None = typer.Option(
        None,
        "--tls-cert",
        envvar="HARNESS_TLS_CERT",
        help="TLS certificate chain (PEM). With --tls-key, uvicorn terminates TLS directly — no "
        "reverse-proxy daemon. Web push REQUIRES a secure context off-localhost, so this is the "
        "phone-notifications enabler. Deploy notes: docs/WEB-CONSOLE.md → TLS.",
    ),
    tls_key: Path | None = typer.Option(
        None,
        "--tls-key",
        envvar="HARNESS_TLS_KEY",
        help="TLS private key (PEM) matching --tls-cert.",
    ),
    access_log: bool = typer.Option(
        False,
        "--access-log",
        help="Log every HTTP request. Off by default: a polling console makes it >99% of the log and "
        "buries the warnings (push failures, bind errors) the log is for.",
    ),
) -> None:
    """Serve the bus over HTTP (the multi-device 'centralize, don't sync' layer).

    Purely additive: nothing else in the harness starts or needs this server. Run it on the
    always-on node; remote consoles/producers point at it. Spec: docs/BUS.md (Serving section).
    """
    import uvicorn

    from harness.bus.server import create_app, resolve_token
    from harness.bus.store import default_db_path

    # TLS is all-or-nothing: half a pair is a config error, not a silent plain-HTTP fallback
    # (the operator believing TLS is on when it isn't is the worst failure shape here).
    if bool(tls_cert) != bool(tls_key):
        raise typer.BadParameter("--tls-cert and --tls-key must be given together")

    resolved_file = token_file.expanduser()
    token = resolve_token(resolved_file)
    extra = None
    if web_console or ui:
        from harness.console.api import WRITE_GATED, console_routes, ui_mounts

        allowed = frozenset(w.strip() for w in writes.split(",") if w.strip())
        allowed_ok = WRITE_GATED - {"set_bus_config", "test_bus_connection"}
        if allowed - allowed_ok:
            raise typer.BadParameter(
                f"--writes: unknown {sorted(allowed - allowed_ok)}; choose from {sorted(allowed_ok)}"
            )
        # UI mounts ride AFTER the door: Starlette is first-match-wins, and the root mount
        # swallows everything — /api/* must already be claimed by then.
        extra = [*console_routes(token, writes=allowed), *ui_mounts(ui)]
    scheme = "https" if tls_cert else "http"
    console.print(f"bus db: {default_db_path()}")
    console.print(f"token:  {resolved_file} (send as 'Authorization: Bearer …')")
    surface = "/api/bus/* + /api/invoke/*" if extra else "/api/bus/*"
    if extra and writes:
        console.print(f"writes: {writes}")
    console.print(f"listen: {scheme}://{host}:{port}  (health: /health · api: {surface})")
    if tls_cert:
        console.print(f"tls:    cert {tls_cert} · key {tls_key}")
    for spec in ui:
        name, _, raw = spec.partition("=")
        console.print(f"ui:     {'/ui/' + name + '/' if raw else '/'} ← {raw or name}")
    _exit_with_parent()
    uvicorn.run(
        create_app(token=token, extra_routes=extra),
        host=host,
        port=port,
        log_level="info",
        access_log=access_log,
        log_config=serve_log_config(),
        ssl_certfile=str(tls_cert.expanduser()) if tls_cert else None,
        ssl_keyfile=str(tls_key.expanduser()) if tls_key else None,
    )
