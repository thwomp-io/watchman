"""How the door turns a failed spawn's stderr into the error a widget shows."""

from __future__ import annotations

import json

from harness.console.errors import from_stderr


def test_a_harness_command_reports_its_own_fields() -> None:
    reported = {"kind": "source", "message": "nasdaq: HTTP 403 for ABC", "hint": "Try again."}
    line = json.dumps({"error": reported})
    err = from_stderr("Day moves", 1, f"progress line\n{line}\n")
    assert err.to_response() == {
        "error": "nasdaq: HTTP 403 for ABC",
        "kind": "source",
        "hint": "Try again.",
        "detail": "progress line",
    }


def test_any_other_program_gives_its_last_line_and_keeps_the_rest_as_detail() -> None:
    traceback = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: bad shape"
    err = from_stderr("Open traps", 1, traceback)
    assert str(err) == "ValueError: bad shape"
    assert err.kind == "error" and err.detail == traceback


def test_a_silent_failure_still_says_something() -> None:
    err = from_stderr("Day moves", 3, "")
    assert str(err) == "Day moves exited 3 without saying why" and err.detail is None


def test_a_last_line_that_is_json_but_not_an_error_is_just_a_line() -> None:
    assert str(from_stderr("X", 1, '["not", "an", "error"]')) == '["not", "an", "error"]'
