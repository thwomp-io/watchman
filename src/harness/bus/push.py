"""Web Push transport for the bus — alert/warn events reach the operator's phone via the
installed PWA (iOS 16.4+ / Android), account-free and self-hosted end to end.

Why this shape (all deliberate):

- **A transport, not a producer.** Web push rides the SAME hook every transport uses: after a
  successful non-duplicate publish, `BusService` calls :func:`notify_event`. Producers stay
  ignorant of delivery (the docs/BUS.md rule); removing this module would change nothing about
  what the bus stores.
- **Severity-gated by design**: only ``alert`` and ``warn`` push. The ``info`` stream (the
  catalyst wire) is deliberately a non-urgent skim surface — pushing it to a phone would drown
  the very signal the gate protects. Filing kinds are a primary-source rail, not an interrupt,
  so they never push regardless of severity. Mirrors the console's triage bands (App.tsx).
- **Push failures never fail a publish.** Delivery is best-effort; the bus row is the durable
  truth. Errors are logged and swallowed; a permanently-gone endpoint (HTTP 404/410) prunes its
  subscription so dead devices don't accumulate.
- **Crypto is a library, never hand-rolled**: VAPID ES256 JWTs + aes128gcm payload encryption
  come from ``pywebpush``/``py-vapid`` (the one place the compose-from-primitives rule yields —
  hand-rolling crypto is how you ship a subtly-broken transport).
- **Keys live in the config dir, never the repo.** The VAPID private key is generated on first
  use into the same directory the bus token already resolves to; only the PUBLIC key ever leaves
  this module (via the API route / the ``push-keys`` verb).
- **Minimal payload.** The push services (Apple/Google) only ever see ciphertext — the Web Push
  protocol encrypts end-to-end — but the payload still carries only what the notification needs
  (kind, subject, severity, title, one-line summary), never the full event payload.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.bus.models import EventDraft, PushReport, PushSubscription

logger = logging.getLogger(__name__)

# Severities that interrupt a human. Keep in sync with the console's triage bands: alert=ACT,
# warn=WATCH; info=the wire skim-stream (never a push).
PUSHABLE_SEVERITIES = frozenset({"alert", "warn"})
# Filing kinds are the FILINGS rail (primary sources to read at leisure) — mirror of the
# console's FILING_KINDS set in bus-app/src/App.tsx; keep the two in sync.
NON_PUSH_KINDS = frozenset({"filing", "filing_drop", "print_landed"})

# How long a push service should retain an undelivered push (seconds). Alerts are morning-scale
# signals, not millisecond ones — 6h covers a phone that's offline overnight without replaying
# stale noise days later.
PUSH_TTL_S = 6 * 60 * 60
PUSH_TIMEOUT_S = 10.0

VAPID_KEY_FILENAME = "push-vapid-key.pem"


def should_push(severity: str, kind: str) -> bool:
    """The one severity/kind gate — every send path (publish hook, future transports) asks here."""
    return severity in PUSHABLE_SEVERITIES and kind not in NON_PUSH_KINDS


# ————— key management ———————————————————————————————————————————————————————————————————————————


def config_dir() -> Path:
    """The harness config dir (bus-token, bus-app.json, and now the VAPID key). Honors
    ``HARNESS_CONFIG_DIR`` ahead of the default — the same resolution the console API and the
    Rust host use (bus/ stays extractable, so the helper is defined here rather than imported
    from console/)."""
    override = os.environ.get("HARNESS_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path("~/.config/harness").expanduser()


def vapid_key_path() -> Path:
    return config_dir() / VAPID_KEY_FILENAME


def ensure_vapid_keys() -> Path:
    """Generate the VAPID keypair on first use (0600, like the bus token); return the PEM path.
    The private key never leaves this file — never printed, never logged, never in an API body."""
    path = vapid_key_path()
    if not path.exists():
        from py_vapid import Vapid02

        path.parent.mkdir(parents=True, exist_ok=True)
        Vapid02.from_file(str(path))  # absent file → generate + save (py-vapid's documented path)
        path.chmod(0o600)
        logger.info("generated VAPID keypair at %s", path)
    return path


def vapid_public_key() -> str:
    """The applicationServerKey the browser needs: base64url (unpadded) of the uncompressed
    P-256 public point. Generates the keypair on first call."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02

    vapid = Vapid02.from_file(str(ensure_vapid_keys()))
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def push_contact_path() -> Path:
    return config_dir() / "push-contact"


def _vapid_claims(endpoint: str) -> dict[str, str]:
    # The `sub` claim is the push service's abuse contact. Resolution: env override → a config-dir
    # file (`push-contact`, one mailto: line — set once, every producer process picks it up) → an
    # RFC 2606 .invalid placeholder so no personal email is ever baked into code.
    # ⚠ Apple's push service (web.push.apple.com) REJECTS placeholder/unreachable `sub` domains
    # with 403 Forbidden (FCM/Mozilla tolerate them) — iOS PWA push effectively REQUIRES a real
    # contact on file.
    contact = os.environ.get("HARNESS_PUSH_CONTACT", "").strip()
    if not contact:
        try:
            contact = push_contact_path().read_text().strip()
        except OSError:
            contact = ""
    if contact and not contact.startswith("mailto:"):
        contact = f"mailto:{contact}"
    origin = urlparse(endpoint)
    return {"aud": f"{origin.scheme}://{origin.netloc}", "sub": contact or "mailto:operator@harness.invalid"}


# ————— subscription store (rows in the bus db — the three-surface schema contract) ———————————————


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_subscription(
    conn: sqlite3.Connection, *, endpoint: str, p256dh: str, auth: str, label: str = ""
) -> None:
    """Upsert by endpoint — a browser re-subscribing (new keys, same endpoint) must replace, not
    duplicate; re-posting an identical subscription is a no-op refresh."""
    conn.execute(
        "INSERT INTO push_subscriptions (endpoint, p256dh, auth, label, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
        "p256dh=excluded.p256dh, auth=excluded.auth, label=excluded.label",
        (endpoint, p256dh, auth, label, _utc_now()),
    )
    conn.commit()


def delete_subscription(conn: sqlite3.Connection, endpoint: str) -> bool:
    cur = conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    return cur.rowcount > 0


def list_subscriptions(conn: sqlite3.Connection) -> list[PushSubscription]:
    rows = conn.execute(
        "SELECT endpoint, p256dh, auth, label, created_at FROM push_subscriptions ORDER BY created_at"
    ).fetchall()
    return [
        PushSubscription(
            endpoint=r[0], p256dh=r[1], auth=r[2], label=r[3], created_at=r[4]
        )
        for r in rows
    ]


# ————— sending ——————————————————————————————————————————————————————————————————————————————————


class PushSendError(Exception):
    """A single-subscription send failure, carrying the push service's HTTP status (None when the
    failure never reached HTTP — DNS, timeout). Lets the caller prune-vs-log without importing
    pywebpush types."""

    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def _webpush_send(subscription_info: dict[str, Any], data: str, urgency: str) -> None:
    """The one seam onto pywebpush (tests monkeypatch THIS, never the network). Imports lazily so
    `import harness.bus.push` stays cheap on the hot publish path."""
    from pywebpush import WebPushException, webpush

    from harness._http import USER_AGENT  # descriptive, versioned, non-PII outbound identity

    endpoint = str(subscription_info.get("endpoint", ""))
    try:
        webpush(
            subscription_info=subscription_info,
            data=data,
            vapid_private_key=str(ensure_vapid_keys()),
            vapid_claims=_vapid_claims(endpoint),
            ttl=PUSH_TTL_S,
            timeout=PUSH_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Urgency": urgency},
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        raise PushSendError(status, str(exc)) from exc


def _endpoint_host(endpoint: str) -> str:
    # Log the push-service HOST only: the endpoint path is a capability URL (whoever holds it can
    # send pushes to that device) — it stays out of logs like any other secret.
    return urlparse(endpoint).netloc or "?"


def _payload_for(draft: EventDraft) -> str:
    # Minimal by policy (module docstring): the notification's needs, nothing more.
    summary = draft.body.splitlines()[0][:140] if draft.body else ""
    return json.dumps(
        {
            "title": draft.title,
            "summary": summary,
            "lane": draft.lane,
            "kind": draft.kind,
            "subject": draft.subject,
            "severity": draft.severity,
        }
    )


def send_to_all(
    conn: sqlite3.Connection, payload: str, *, urgency: str = "normal", endpoint: str | None = None
) -> PushReport:
    """Send one payload to every stored subscription (or one, when `endpoint` narrows it).
    Per-subscription failures are contained: 404/410 (subscription gone at the push service)
    prunes the row; anything else logs and moves on. Never raises."""
    subs = list_subscriptions(conn)
    if endpoint is not None:
        subs = [s for s in subs if s.endpoint == endpoint]
    report = PushReport()
    for sub in subs:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            _webpush_send(info, payload, urgency)
            report.sent += 1
        except PushSendError as exc:
            if exc.status in (404, 410):
                # The push service says this subscription no longer exists (app uninstalled,
                # permission revoked) — pruning is the protocol-correct response.
                delete_subscription(conn, sub.endpoint)
                report.pruned += 1
                logger.info("pruned dead push subscription at %s", _endpoint_host(sub.endpoint))
            else:
                report.failed += 1
                logger.warning(
                    "web push to %s failed (%s): %s", _endpoint_host(sub.endpoint), exc.status, exc
                )
        except Exception:  # noqa: BLE001 — delivery is best-effort by contract; the row is durable
            report.failed += 1
            logger.warning("web push to %s failed", _endpoint_host(sub.endpoint), exc_info=True)
    return report


def notify_event(conn: sqlite3.Connection, draft: EventDraft) -> PushReport:
    """The publish hook: gate, then fan out. Cheap when quiet — the gate and the empty-table check
    run before any crypto/network import is paid."""
    if not should_push(draft.severity, draft.kind):
        return PushReport(skipped="severity/kind gated")
    if not list_subscriptions(conn):
        return PushReport(skipped="no subscriptions")
    if not vapid_key_path().exists():
        # Subscriptions without keys shouldn't happen (subscribe flows through the key route),
        # but a deleted key file must degrade to a log line, never a failed publish.
        logger.warning("push subscriptions exist but no VAPID key at %s — skipping push", vapid_key_path())
        return PushReport(skipped="no VAPID key")
    urgency = "high" if draft.severity == "alert" else "normal"
    return send_to_all(conn, _payload_for(draft), urgency=urgency)
