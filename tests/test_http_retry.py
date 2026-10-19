"""`get_with_retry` retry policy — a 4xx is final (one request, no sleep), a 5xx retries, and
`describe_http_error` names the code + the 404 hint. respx intercepts httpx; time.sleep is stubbed.

Why: a 404'd board token was retried three times (7s of backoff) and then escaped as a raw
httpx error that aborted a 17-board scan. The policy is now pinned here."""

from __future__ import annotations

import httpx
import pytest
import respx

from harness._http import describe_http_error, get_with_retry


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harness._http.time.sleep", lambda _s: None)


@respx.mock
def test_404_is_final_one_request_no_retry() -> None:
    route = respx.get("https://example.test/board").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError) as ei:
        get_with_retry("https://example.test/board")
    assert ei.value.response.status_code == 404
    assert route.call_count == 1


@respx.mock
def test_5xx_retries_then_succeeds() -> None:
    route = respx.get("https://example.test/flaky").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    resp = get_with_retry("https://example.test/flaky")
    assert resp.json() == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_allow_status_returns_the_response_unraised() -> None:
    respx.get("https://example.test/maybe").mock(return_value=httpx.Response(404))
    resp = get_with_retry("https://example.test/maybe", allow_status={404})
    assert resp.status_code == 404


def test_describe_http_error_names_code_hint_and_transport() -> None:
    req = httpx.Request("GET", "https://example.test/x")
    e404 = httpx.HTTPStatusError("nope", request=req, response=httpx.Response(404, request=req))
    assert describe_http_error(e404, hint_404="token moved") == "HTTP 404 (token moved)"
    assert describe_http_error(e404) == "HTTP 404"
    e401 = httpx.HTTPStatusError("nope", request=req, response=httpx.Response(401, request=req))
    assert describe_http_error(e401, hint_404="token moved") == "HTTP 401"
    assert describe_http_error(httpx.ConnectError("boom", request=req)).startswith("transport error")
