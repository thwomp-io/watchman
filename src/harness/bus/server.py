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
        # Threadpool, not inline: publish now fans out web pushes (network I/O with real
        # timeouts) — leaving it on the event loop would stall every other request for the
        # duration of a slow push service (a lesson the console door already learned).
        # The service is constructed INSIDE the worker: sqlite3 connections are thread-bound.
        from starlette.concurrency import run_in_threadpool

        def run() -> list[dict[str, Any]]:
            svc = service()
            try:
                return [r.model_dump() for r in svc.publish_many(drafts)]
            finally:
                svc.close()

        return JSONResponse({"results": await run_in_threadpool(run)})

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

    # —— web push (harness.bus.push) ————————————————————————————————————————————————————————————
    # Same token as every /api route: the vapid PUBLIC key isn't secret, but an open route would
    # be the only unauthenticated /api surface — consistency beats the micro-convenience.

    async def push_vapid_key(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        from starlette.concurrency import run_in_threadpool

        from harness.bus import push

        # threadpool: first call may GENERATE the keypair (file I/O + EC keygen)
        key = await run_in_threadpool(push.vapid_public_key)
        return JSONResponse({"key": key})

    async def push_subscribe(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        if not isinstance(body, dict):
            return _bad_request('body must be {"subscription": <PushSubscription.toJSON()>, "label"?: …}')
        sub = body.get("subscription")
        if not isinstance(sub, dict):
            return _bad_request('"subscription" must be the browser PushSubscription.toJSON() object')
        endpoint = sub.get("endpoint")
        raw_keys = sub.get("keys")
        keys: dict[str, Any] = raw_keys if isinstance(raw_keys, dict) else {}
        p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
        if not (isinstance(endpoint, str) and endpoint.startswith("https://")):
            return _bad_request('"endpoint" must be an https URL')
        if not (isinstance(p256dh, str) and p256dh and isinstance(auth_key, str) and auth_key):
            return _bad_request('"keys" must carry non-empty "p256dh" and "auth"')
        label = str(body.get("label") or "")[:80]
        svc = service()
        try:
            svc.add_push_subscription(endpoint=endpoint, p256dh=p256dh, auth=auth_key, label=label)
            return JSONResponse({"stored": True, "subscriptions": len(svc.list_push_subscriptions())})
        finally:
            svc.close()

    async def push_unsubscribe(request: Request) -> JSONResponse:
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if not isinstance(endpoint, str) or not endpoint:
            return _bad_request('body must be {"endpoint": <subscription endpoint>}')
        svc = service()
        try:
            return JSONResponse({"removed": svc.remove_push_subscription(endpoint)})
        finally:
            svc.close()

    async def push_test(request: Request) -> JSONResponse:
        # The verification affordance: prove the whole pipeline (key → subscription → push service
        # → device banner) without waiting for a real alert. Optional {"endpoint"} narrows to one
        # device; default fans out to all.
        if not _authorized(request, token):
            return _unauthorized()
        try:
            body_raw = await request.body()
            body = json.loads(body_raw) if body_raw else {}
        except json.JSONDecodeError:
            return _bad_request("body must be JSON")
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if endpoint is not None and not isinstance(endpoint, str):
            return _bad_request('"endpoint" must be a string when present')
        from starlette.concurrency import run_in_threadpool

        from harness.bus import push
        from harness.bus.store import connect as bus_connect

        payload = json.dumps(
            {
                "title": "WATCHMAN TEST",
                "summary": "push pipeline verified — this device is wired to the bus",
                "lane": "core",
                "kind": "push_test",
                "subject": "",
                "severity": "info",
            }
        )
        def send() -> dict[str, Any]:
            # connect INSIDE the worker: sqlite3 connections are thread-bound, and threadpool
            # execution is a different thread than this handler's.
            conn = bus_connect(db_path)
            try:
                return push.send_to_all(conn, payload, urgency="normal", endpoint=endpoint).model_dump()
            finally:
                conn.close()

        return JSONResponse(await run_in_threadpool(send))

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/api/bus/events", list_events, methods=["GET"]),
            Route("/api/bus/events", publish, methods=["POST"]),
            Route("/api/bus/ack", ack, methods=["POST"]),
            Route("/api/bus/delivered", delivered, methods=["POST"]),
            Route("/api/bus/stats", stats, methods=["GET"]),
            Route("/api/push/vapid-key", push_vapid_key, methods=["GET"]),
            Route("/api/push/subscribe", push_subscribe, methods=["POST"]),
            Route("/api/push/unsubscribe", push_unsubscribe, methods=["POST"]),
            Route("/api/push/test", push_test, methods=["POST"]),
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
