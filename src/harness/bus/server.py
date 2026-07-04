"""HTTP API over the bus — the multi-device epic's "serve the bus, don't sync it" half.

Why this exists: the bus.db is a *local-file* contract (Python producers + the Rust app read one
SQLite file). That's perfect on the machine the watchmen run on — and invisible to every other
device. `hn bus serve` exposes the SAME BusService over HTTP so a remote console (another
desktop's watchman in `bus_url` mode, the future web console, a phone) can read/ack/publish
against the one authoritative bus.

Design constraints (all load-bearing):

- **Purely additive — the out-of-box experience is untouched.** Nothing starts this server unless
  the operator runs `hn bus serve`; the app's default path stays the local file; no sample pack
  configures a remote bus. Multi-device is an opt-in layer documented separately.
- **Zero new dependencies**: Starlette + uvicorn already ride `mcp[cli]`; they're declared
  explicitly in pyproject to make the intent honest, but the installed tree doesn't grow.
- **Thin adapter over BusService** (the dual-surface convention — CLI/MCP/HTTP are all skins over
  the same core). No bus logic lives here; idempotency/dedup semantics are the service's.
- **Per-request connections.** sqlite3 connections are thread-bound and Starlette may run
  handlers across threads; a fresh BusService per request (WAL + busy_timeout) matches the Rust
  side's per-call-connection convention and removes the whole shared-state class of bugs.
- **Secure by default**: binds 127.0.0.1 unless told otherwise; every /api route requires a
  bearer token (constant-time compare); /health alone is open (a mesh reachability probe needs
  no secret). Transport privacy is the deployment's job (mesh/ACL) — the token is the
  belt-and-braces layer, not the perimeter.
- **Seal-honoring by construction**: the db path resolves through the standard
  HARNESS_BUS_DB / HARNESS_STATE_DIR knobs, so a sealed demo instance that serves would serve
  its sealed bus — never the real one.
- **Mountable**: `create_app()` returns a plain Starlette app the future web-console server can
  mount under its own root (one server, one token, one bind — the settled end-state).
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from harness import __version__
from harness.bus.models import EventDraft
from harness.bus.service import BusService

DEFAULT_PORT = 8787


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "missing or invalid bearer token"}, status_code=401)


def _bad_request(msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=400)


def _authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, token)


def create_app(
    *, token: str, db_path: Path | None = None, extra_routes: list[BaseRoute] | None = None
) -> Starlette:
    """Build the bus HTTP app. `token` is required — the caller (the CLI verb) owns generation
    and persistence; an empty token is a config error, not an open-server mode.

    `extra_routes` is the mount seam (the console's standing rule: one server, one token,
    one bind): a caller appends its own routes — e.g. `harness.console.api.console_routes(token)`
    — without this module knowing about them. Deliberately a plain list, not an import: `bus/`
    stays cleanly extractable (no domain/console imports)."""
    if not token:
        raise ValueError("bus server requires a non-empty token (see `hn bus serve --help`)")

    def service() -> BusService:
        return BusService(db_path)

    async def health(_: Request) -> JSONResponse:
        # Open by design: a reachability probe (mesh acceptance tests, monitors) needs no secret,
        # and the payload leaks nothing but liveness + version.
        return JSONResponse({"status": "pass", "service": "harness-bus", "version": __version__})

    async def list_events(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        q = request.query_params
        try:
            limit = min(int(q.get("limit", "50")), 500)
        except ValueError:
            return _bad_request("limit must be an integer")
        svc = service()
        try:
            events = svc.list_events(
                unread_only=q.get("unread", "") in ("1", "true"),
                lane=q.get("lane") or None,
                kind=q.get("kind") or None,
                since=q.get("since") or None,
                limit=limit,
            )
            return JSONResponse({"events": [e.model_dump() for e in events]})
        finally:
            svc.close()

    async def publish(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body: Any = json.loads(await request.body())
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        # Accept a single draft object or {"events": [drafts…]} — the same shapes producers use.
        raw_drafts = body.get("events") if isinstance(body, dict) and "events" in body else [body]
        if not isinstance(raw_drafts, list):
            return _bad_request('"events" must be a list of event drafts')
        try:
            drafts = [EventDraft.model_validate(d) for d in raw_drafts]
        except Exception as exc:  # pydantic ValidationError — report, don't 500
            return _bad_request(f"invalid event draft: {exc}")
        svc = service()
        try:
            results = svc.publish_many(drafts)
            return JSONResponse({"results": [r.model_dump() for r in results]})
        finally:
            svc.close()

    async def ack(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        if not isinstance(body, dict):
            return _bad_request('body must be {"ids": […]} or {"all": true, "lane"?: …}')
        svc = service()
        try:
            if body.get("all"):
                acked = svc.ack_all(lane=body.get("lane") or None)
            else:
                ids = body.get("ids")
                if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                    return _bad_request('"ids" must be a list of integers')
                acked = svc.ack(ids)
            return JSONResponse({"acked": acked})
        finally:
            svc.close()

    async def delivered(request: Request) -> JSONResponse:
        # Remote transports' half of the delivered_via contract: a watchman in bus_url mode has
        # no rusqlite access, so it records its own delivery marker here (append-only, own
        # marker only — BusService enforces the never-touch-other-transports rule).
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        if not isinstance(body, dict):
            return _bad_request('body must be {"id": <event id>, "marker": <transport marker>}')
        event_id, marker = body.get("id"), body.get("marker")
        if not isinstance(event_id, int) or not isinstance(marker, str) or not marker.strip():
            return _bad_request('"id" must be an integer and "marker" a non-empty string')
        svc = service()
        try:
            markers = svc.mark_delivered(event_id, marker.strip())
            if markers is None:
                return JSONResponse({"error": f"no event with id {event_id}"}, status_code=404)
            return JSONResponse({"id": event_id, "delivered_via": markers})
        finally:
            svc.close()

    async def stats(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        svc = service()
        try:
            return JSONResponse(svc.stats().model_dump())
        finally:
            svc.close()

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/api/bus/events", list_events, methods=["GET"]),
            Route("/api/bus/events", publish, methods=["POST"]),
            Route("/api/bus/ack", ack, methods=["POST"]),
            Route("/api/bus/delivered", delivered, methods=["POST"]),
            Route("/api/bus/stats", stats, methods=["GET"]),
            *(extra_routes or []),
        ]
    )


def resolve_token(token_file: Path) -> str:
    """Load the bearer token, generating + persisting one (0600) on first run. Auto-generation
    keeps the operator flow to a single command while never running tokenless."""
    if token_file.exists():
        token = token_file.read_text().strip()
        if token:
            return token
    import secrets

    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token + "\n")
    token_file.chmod(0o600)
    return token
