"""The bus over HTTP: `hn bus serve`.

The bus is one SQLite file, which is all a producer and a console on the same machine need. This
serves the same `BusService` to everything else (the web console, a phone, a desktop app pointed at
a remote bus) so there is still exactly one bus.

What the design holds to:

- A thin adapter. No bus logic lives here; dedup and idempotency are the service's.
- One connection per request. sqlite3 connections are bound to a thread and Starlette may run
  handlers on several, so each request opens a `BusService` and closes it.
- Closed by default. It binds 127.0.0.1 unless told otherwise, and every route but `/health`
  requires the bearer token, compared in constant time. The token is a second layer: keeping the
  port private is the deployment's job (a mesh, an ACL).
- The database path resolves through HARNESS_BUS_DB / HARNESS_STATE_DIR like everywhere else, so a
  sealed demo that serves, serves its own bus.
- Mountable. `create_app` takes extra routes, which is how the console's API shares this server,
  token and port without `bus/` importing it.
- No new dependencies: Starlette and uvicorn already arrive with `mcp[cli]`.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from harness import __version__
from harness.bus import push
from harness.bus.models import EventDraft
from harness.bus.service import BusService
from harness.bus.store import connect as bus_connect

Handler = Callable[[Request], Awaitable[Response]]

DEFAULT_PORT = 8787


class _BadRequest(Exception):
    """The request can't be acted on as sent. A handler raises it with the reason; the route wrapper
    answers 400 with that reason, so validation reads as straight-line checks."""


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, token)


def require_token(token: str) -> Callable[[Handler], Handler]:
    """A decorator for route handlers: the handler runs only for a request carrying `token`, and
    anything else is answered 401. Every route on this server but `/health` goes through it,
    including the ones a caller mounts."""

    def guard(handler: Handler) -> Handler:
        async def endpoint(request: Request) -> Response:
            if not _authorized(request, token):
                return _error("missing or invalid bearer token", 401)
            try:
                return await handler(request)
            except _BadRequest as e:
                return _error(str(e), 400)

        return endpoint

    return guard


async def _json(request: Request, *, optional: bool = False) -> Any:
    """The request's JSON body. With `optional`, an empty body is an empty object."""
    raw = await request.body()
    if not raw and optional:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise _BadRequest("body must be JSON") from None


async def _json_object(request: Request, shape: str, *, optional: bool = False) -> dict[str, Any]:
    """The request's body as a JSON object; `shape` describes the one expected, for the error."""
    body = await _json(request, optional=optional)
    if not isinstance(body, dict):
        raise _BadRequest(f"body must be {shape}")
    return body


def create_app(
    *, token: str, db_path: Path | None = None, extra_routes: list[BaseRoute] | None = None
) -> Starlette:
    """Build the bus HTTP app. `token` is required — the caller (the CLI verb) owns generation
    and persistence; an empty token is a config error, not an open-server mode.

    Every route but `/health` is wrapped in the bearer check here, in one table, so a new route
    can't be added without it. `extra_routes` is the mount seam (one server, one token, one bind):
    a caller appends its own routes, e.g. `harness.console.api.console_routes(token)`, wrapped in
    `require_token`. Deliberately a plain list, not an import: `bus/` stays extractable, with no
    domain or console imports."""
    if not token:
        raise ValueError("bus server requires a non-empty token (see `hn bus serve --help`)")

    guarded = require_token(token)
    api = _BusApi(db_path)
    guarded_routes = [
        ("/api/bus/events", "GET", api.list_events),
        ("/api/bus/events", "POST", api.publish),
        ("/api/bus/ack", "POST", api.ack),
        ("/api/bus/delivered", "POST", api.delivered),
        ("/api/bus/stats", "GET", api.stats),
        ("/api/push/vapid-key", "GET", api.push_vapid_key),
        ("/api/push/subscribe", "POST", api.push_subscribe),
        ("/api/push/unsubscribe", "POST", api.push_unsubscribe),
        ("/api/push/test", "POST", api.push_test),
    ]
    return Starlette(routes=[
        Route("/health", _health, methods=["GET"]),
        *(Route(path, guarded(handler), methods=[method]) for path, method, handler in guarded_routes),
        *(extra_routes or []),
    ])


async def _health(_: Request) -> JSONResponse:
    """Open by design: a reachability probe (mesh acceptance tests, monitors) needs no secret, and
    the payload says nothing but liveness and version."""
    return JSONResponse({"status": "pass", "service": "harness-bus", "version": __version__})


class _BusApi:
    """The /api handlers. Each request opens its own BusService and closes it: sqlite3 connections
    are thread-bound, and Starlette may run handlers on different threads. Work that does network
    I/O (a publish fans out web pushes) runs in the threadpool, and opens its connection there."""

    def __init__(self, db_path: Path | None) -> None:
        self.db_path = db_path

    def _service(self) -> closing[BusService]:
        return closing(BusService(self.db_path))

    async def list_events(self, request: Request) -> JSONResponse:
        q = request.query_params
        try:
            limit = min(int(q.get("limit", "50")), 500)
        except ValueError:
            raise _BadRequest("limit must be an integer") from None
        with self._service() as svc:
            events = svc.list_events(
                unread_only=q.get("unread", "") in ("1", "true"),
                lane=q.get("lane") or None,
                kind=q.get("kind") or None,
                since=q.get("since") or None,
                limit=limit,
            )
        return JSONResponse({"events": [e.model_dump() for e in events]})

    async def publish(self, request: Request) -> JSONResponse:
        """One event draft, or {"events": [drafts…]}: the same shapes producers use."""
        body = await _json(request)
        raw_drafts = body.get("events") if isinstance(body, dict) and "events" in body else [body]
        if not isinstance(raw_drafts, list):
            raise _BadRequest('"events" must be a list of event drafts')
        try:
            drafts = [EventDraft.model_validate(d) for d in raw_drafts]
        except ValidationError as e:
            raise _BadRequest(f"invalid event draft: {e}") from None

        def run() -> list[dict[str, Any]]:
            with self._service() as svc:
                return [r.model_dump() for r in svc.publish_many(drafts)]

        return JSONResponse({"results": await run_in_threadpool(run)})

    async def ack(self, request: Request) -> JSONResponse:
        body = await _json_object(request, '{"ids": […]} or {"all": true, "lane"?: …}')
        if body.get("all"):
            with self._service() as svc:
                return JSONResponse({"acked": svc.ack_all(lane=body.get("lane") or None)})
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise _BadRequest('"ids" must be a list of integers')
        with self._service() as svc:
            return JSONResponse({"acked": svc.ack(ids)})

    async def delivered(self, request: Request) -> JSONResponse:
        """A remote transport records its own delivery marker. A watchman reading the bus over HTTP
        has no direct database access, so this is its half of the delivered_via contract; the
        service appends its marker only and never touches another transport's."""
        body = await _json_object(request, '{"id": <event id>, "marker": <transport marker>}')
        event_id, marker = body.get("id"), body.get("marker")
        if not isinstance(event_id, int) or not isinstance(marker, str) or not marker.strip():
            raise _BadRequest('"id" must be an integer and "marker" a non-empty string')
        with self._service() as svc:
            markers = svc.mark_delivered(event_id, marker.strip())
        if markers is None:
            return _error(f"no event with id {event_id}", 404)
        return JSONResponse({"id": event_id, "delivered_via": markers})

    async def stats(self, request: Request) -> JSONResponse:
        with self._service() as svc:
            return JSONResponse(svc.stats().model_dump())

    # Web push. The VAPID public key isn't secret, but it sits behind the token like every /api
    # route: one open route would be the odd one out.

    async def push_vapid_key(self, request: Request) -> JSONResponse:
        # the first call may generate the keypair (file I/O and EC keygen), so it runs off the loop
        return JSONResponse({"key": await run_in_threadpool(push.vapid_public_key)})

    async def push_subscribe(self, request: Request) -> JSONResponse:
        body = await _json_object(request, '{"subscription": <PushSubscription.toJSON()>, "label"?: …}')
        sub = body.get("subscription")
        if not isinstance(sub, dict):
            raise _BadRequest('"subscription" must be the browser PushSubscription.toJSON() object')
        endpoint = sub.get("endpoint")
        raw_keys = sub.get("keys")
        keys: dict[str, Any] = raw_keys if isinstance(raw_keys, dict) else {}
        p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
        if not (isinstance(endpoint, str) and endpoint.startswith("https://")):
            raise _BadRequest('"endpoint" must be an https URL')
        if not (isinstance(p256dh, str) and p256dh and isinstance(auth_key, str) and auth_key):
            raise _BadRequest('"keys" must carry non-empty "p256dh" and "auth"')
        label = str(body.get("label") or "")[:80]
        with self._service() as svc:
            svc.add_push_subscription(endpoint=endpoint, p256dh=p256dh, auth=auth_key, label=label)
            return JSONResponse({"stored": True, "subscriptions": len(svc.list_push_subscriptions())})

    async def push_unsubscribe(self, request: Request) -> JSONResponse:
        shape = '{"endpoint": <subscription endpoint>}'
        endpoint = (await _json_object(request, shape)).get("endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise _BadRequest(f"body must be {shape}")
        with self._service() as svc:
            return JSONResponse({"removed": svc.remove_push_subscription(endpoint)})

    async def push_test(self, request: Request) -> JSONResponse:
        """Prove the whole pipeline (key → subscription → push service → device banner) without
        waiting for a real alert. An optional {"endpoint"} narrows it to one device."""
        body = await _json(request, optional=True)
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if endpoint is not None and not isinstance(endpoint, str):
            raise _BadRequest('"endpoint" must be a string when present')
        payload = json.dumps({
            "title": "WATCHMAN TEST",
            "summary": "push pipeline verified — this device is wired to the bus",
            "lane": "core",
            "kind": "push_test",
            "subject": "",
            "severity": "info",
        })

        def send() -> dict[str, Any]:
            conn = bus_connect(self.db_path)
            try:
                return push.send_to_all(conn, payload, urgency="normal", endpoint=endpoint).model_dump()
            finally:
                conn.close()

        return JSONResponse(await run_in_threadpool(send))


def resolve_token(token_file: Path) -> str:
    """The bearer token in `token_file`, generated on first run so the operator's flow is one
    command and the server never runs without one. The file is created owner-only (0600) rather than
    narrowed afterwards, so the token is never readable by anyone else, even briefly."""
    if token_file.exists():
        token = token_file.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.unlink(missing_ok=True)  # an empty leftover would keep its old, possibly wider, mode
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token
