"""Tests for the bus HTTP server (`hn bus serve`) — the thin Starlette adapter over BusService.

The suite drives the REAL app object through Starlette's TestClient against a tmp-path bus db
(never the live one — the tests-never-couple-to-live-state rule). Auth, both publish shapes,
idempotency-through-the-API, filters, ack, and stats each get a check; the service internals are
already covered by the bus service tests, so this file stays at the adapter seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from harness.bus.server import create_app, resolve_token

TOKEN = "test-token-1234"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(token=TOKEN, db_path=tmp_path / "bus.db")
    return TestClient(app)


def _draft(**over: object) -> dict[str, object]:
    d: dict[str, object] = {
        "producer": "test.suite",
        "lane": "finance",
        "kind": "day_move",
        "subject": "TSM",
        "title": "TSM moved",
        "severity": "warn",
    }
    d.update(over)
    return d


def test_health_is_open_and_versioned(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "pass"
    assert body["service"] == "harness-bus"
    assert body["version"]


def test_api_requires_bearer_token(client: TestClient) -> None:
    assert client.get("/api/bus/events").status_code == 401
    bad = {"Authorization": "Bearer wrong-token"}
    assert client.get("/api/bus/events", headers=bad).status_code == 401
    assert client.post("/api/bus/ack", headers=bad, json={"all": True}).status_code == 401
    assert client.get("/api/bus/stats", headers=bad).status_code == 401


def test_publish_single_then_duplicate(client: TestClient) -> None:
    first = client.post("/api/bus/events", headers=AUTH, json=_draft())
    assert first.status_code == 200
    result = first.json()["results"][0]
    assert result["status"] == "published"
    assert result["event_id"] is not None

    again = client.post("/api/bus/events", headers=AUTH, json=_draft())
    assert again.json()["results"][0]["status"] == "duplicate"


def test_publish_batch_and_list_filters(client: TestClient) -> None:
    batch = {
        "events": [
            _draft(subject="NVDA", title="NVDA moved"),
            _draft(lane="career", kind="scan_delta", subject="", title="3 new roles"),
        ]
    }
    res = client.post("/api/bus/events", headers=AUTH, json=batch)
    assert [r["status"] for r in res.json()["results"]] == ["published", "published"]

    finance = client.get("/api/bus/events?lane=finance", headers=AUTH).json()["events"]
    assert {e["subject"] for e in finance} == {"NVDA"}
    unread = client.get("/api/bus/events?unread=1", headers=AUTH).json()["events"]
    assert len(unread) == 2
    assert unread[0]["payload"] == {}  # payload round-trips as an object, not a JSON string


def test_ack_ids_and_ack_all(client: TestClient) -> None:
    client.post("/api/bus/events", headers=AUTH, json=_draft(subject="A", title="a"))
    client.post("/api/bus/events", headers=AUTH, json=_draft(subject="B", title="b"))
    events = client.get("/api/bus/events?unread=1", headers=AUTH).json()["events"]
    first_id = events[0]["id"]

    acked = client.post("/api/bus/ack", headers=AUTH, json={"ids": [first_id]}).json()["acked"]
    assert acked == 1
    remaining = client.get("/api/bus/events?unread=1", headers=AUTH).json()["events"]
    assert len(remaining) == 1

    assert client.post("/api/bus/ack", headers=AUTH, json={"all": True}).json()["acked"] == 1
    assert client.get("/api/bus/events?unread=1", headers=AUTH).json()["events"] == []


def test_bad_requests_are_400_not_500(client: TestClient) -> None:
    assert client.post("/api/bus/events", headers=AUTH, content=b"not json").status_code == 400
    assert client.post("/api/bus/events", headers=AUTH, json=_draft(severity="loud")).status_code == 400
    assert client.post("/api/bus/ack", headers=AUTH, json={"ids": ["x"]}).status_code == 400
    assert client.get("/api/bus/events?limit=abc", headers=AUTH).status_code == 400


def test_stats_shape(client: TestClient) -> None:
    client.post("/api/bus/events", headers=AUTH, json=_draft())
    stats = client.get("/api/bus/stats", headers=AUTH).json()
    assert stats["total"] == 1
    assert stats["unread"] == 1
    assert stats["by_lane"] == {"finance": 1}
    assert stats["schema_version"] == "1"


def test_delivered_appends_own_marker_only_and_is_idempotent(client: TestClient) -> None:
    client.post("/api/bus/events", headers=AUTH, json=_draft())
    event = client.get("/api/bus/events", headers=AUTH).json()["events"][0]
    assert event["delivered_via"] == []

    # First transport marks; a second transport's marker coexists; repeats don't duplicate.
    res = client.post(
        "/api/bus/delivered", headers=AUTH, json={"id": event["id"], "marker": "desktop:winbox"}
    )
    assert res.status_code == 200
    assert res.json()["delivered_via"] == ["desktop:winbox"]
    client.post("/api/bus/delivered", headers=AUTH, json={"id": event["id"], "marker": "desktop"})
    res = client.post(
        "/api/bus/delivered", headers=AUTH, json={"id": event["id"], "marker": "desktop:winbox"}
    )
    assert res.json()["delivered_via"] == ["desktop:winbox", "desktop"]

    refreshed = client.get("/api/bus/events", headers=AUTH).json()["events"][0]
    assert refreshed["delivered_via"] == ["desktop:winbox", "desktop"]


def test_delivered_validates_body_and_404s_unknown_id(client: TestClient) -> None:
    assert client.post("/api/bus/delivered", json={"id": 1, "marker": "x"}).status_code == 401
    assert client.post("/api/bus/delivered", headers=AUTH, content=b"nope").status_code == 400
    assert client.post("/api/bus/delivered", headers=AUTH, json={"id": "1"}).status_code == 400
    assert (
        client.post("/api/bus/delivered", headers=AUTH, json={"id": 1, "marker": "  "}).status_code
        == 400
    )
    assert (
        client.post("/api/bus/delivered", headers=AUTH, json={"id": 999, "marker": "desktop"}).status_code
        == 404
    )


def test_empty_token_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_app(token="", db_path=tmp_path / "bus.db")


def test_resolve_token_generates_persists_0600(tmp_path: Path) -> None:
    token_file = tmp_path / "cfg" / "bus-token"
    token = resolve_token(token_file)
    assert token and token_file.read_text().strip() == token
    assert (token_file.stat().st_mode & 0o777) == 0o600
    assert resolve_token(token_file) == token  # stable across calls
