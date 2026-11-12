"""The CLI's failure contract: every failure is one short report on stderr, in one shape."""

from __future__ import annotations

import json

import httpx
import pytest

from harness import _cli
from harness.errors import HarnessError, ProviderError


def _run(raises: BaseException, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    def app() -> None:
        raise raises

    with pytest.raises(SystemExit) as exit_info:
        _cli.run(app)
    captured = capsys.readouterr()
    return int(exit_info.value.code or 0), captured.out, captured.err


def test_a_known_failure_is_a_line_and_a_hint_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(HarnessError("no portfolio file", hint="run hn init"), capsys)
    assert code == 1
    assert out == ""  # stdout is the result channel; a spawning console reads it as data
    assert err.splitlines() == ["error: no portfolio file", "hint: run hn init"]


def test_json_format_is_one_parseable_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_cli.ERROR_FORMAT_ENV, "json")
    _, _, err = _run(ProviderError("nasdaq: HTTP 403 for ABC"), capsys)
    assert json.loads(err) == {
        "error": {"kind": "source", "message": "nasdaq: HTTP 403 for ABC", "hint": None}
    }


def test_a_leaked_network_error_reads_as_a_source_failure_without_its_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = httpx.Request("GET", "https://api.example.com/v1/thing?apikey=SECRET")
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError("HTTP 403", request=request, response=response)
    code, _, err = _run(error, capsys)
    assert code == 1
    assert "api.example.com: HTTP 403" in err
    assert "SECRET" not in err and "Traceback" not in err


def test_a_bug_is_named_in_one_line_and_exits_70(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_cli.DEBUG_ENV, raising=False)
    code, _, err = _run(KeyError("day_change"), capsys)
    assert code == _cli.EXIT_INTERNAL
    assert err.startswith("error: KeyError: 'day_change'")
    assert "Traceback" not in err and _cli.DEBUG_ENV in err


def test_debug_lets_the_traceback_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_cli.DEBUG_ENV, "1")

    def app() -> None:
        raise KeyError("day_change")

    with pytest.raises(KeyError):
        _cli.run(app)


def test_a_message_with_brackets_is_printed_as_written(capsys: pytest.CaptureFixture[str]) -> None:
    _, _, err = _run(HarnessError("bad value [red]x[/red] in list[int]"), capsys)
    assert "[red]x[/red] in list[int]" in err


def test_a_lane_verb_only_has_to_raise() -> None:
    """The lane group reports a raised error itself, so a verb needs no try/except of its own."""
    import typer
    from typer.testing import CliRunner

    from harness.packs import PackGroup

    lane = typer.Typer(cls=PackGroup)

    @lane.command()
    def quote() -> None:
        raise ProviderError("no market-data key", hint="set ALPACA_API_KEY")

    @lane.command()
    def other() -> None: ...

    result = CliRunner().invoke(lane, ["quote"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == ["error: no market-data key", "hint: set ALPACA_API_KEY"]


def test_a_usage_error_exits_2_through_the_lane_group() -> None:
    from typer.testing import CliRunner

    from harness.finance.cli import app

    result = CliRunner().invoke(app, ["crashdays", "ABC", "--threshold", "5"])
    assert result.exit_code == 2
    assert result.stderr.splitlines() == ["error: --threshold is a loss, e.g. -7"]
    assert result.stdout == ""


def test_a_plain_group_reports_like_a_lane() -> None:
    """Groups outside the lanes (bus, backup, media, …) carry the same reporting, so a command
    anywhere only has to raise."""
    from typer.testing import CliRunner

    from harness.bus.cli import app

    result = CliRunner().invoke(app, ["purge"])
    assert result.exit_code == 2
    assert result.stderr.splitlines() == ["error: pass --before <bound> or --policy default"]
    assert "Traceback" not in result.output
