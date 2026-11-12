"""The calibration ledger: the rules at entry, resolving once, the scores, and the verbs over them."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.calibration.cli import app
from harness.calibration.ledger import Calibration, Entry, EntryError, propose

runner = CliRunner()


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Calibration:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TRACKER_PATH", str(tmp_path / "corpus"))
    return Calibration.for_corpus(tmp_path / "corpus")


def _log(ledger: Calibration, p: float, by: str = "2026-11-02", test: str = "NAV < 41.20") -> Entry:
    entry, _ = ledger.log(
        owner="analyst", claim="a call", p=p, resolves_by=by, test=test, made="2026-11-01T18:20"
    )
    return entry


def test_an_entry_needs_its_parts_and_a_published_figure_wants_a_fallback(ledger: Calibration) -> None:
    with pytest.raises(EntryError, match="claim, test"):
        ledger.log(owner="analyst", claim=" ", p=0.5, resolves_by="2026-11-02", test="", made="")
    with pytest.raises(EntryError, match="between 0 and 1"):
        _log(ledger, 1.2)
    _, warnings = ledger.log(owner="analyst", claim="CPI runs hot", p=0.6, resolves_by="2026-11-10",
                             test="October CPI y/y prints above 3.0%", made="")
    assert warnings and "fallback" in warnings[0]
    _, warnings = ledger.log(owner="analyst", claim="CPI runs hot", p=0.6, resolves_by="2026-11-10",
                             test="October CPI y/y prints above 3.0%; void if no y/y is published", made="")
    assert warnings == []
    assert [e.ref for e in ledger.entries()] == ["C001", "C002"]


def test_resolving_scores_the_entry_once_and_a_void_stays_out_of_the_scores(ledger: Calibration) -> None:
    a, b, c = _log(ledger, 0.7), _log(ledger, 0.3), _log(ledger, 0.9, by="2026-11-09")
    assert [e.ref for e in ledger.due(date(2026, 11, 2))] == [a.ref, b.ref]  # c isn't due yet

    assert ledger.resolve(a.ref, outcome=1, on=date(2026, 11, 2)).brier == 0.09
    assert ledger.resolve(b.ref, outcome=0, on=date(2026, 11, 2), note="outside the band").brier == 0.09
    with pytest.raises(EntryError, match="already resolved 1"):
        ledger.resolve(a.ref, outcome=0, on=date(2026, 11, 3))
    voided = ledger.resolve(c.ref, outcome=None, on=date(2026, 11, 9), void="the figure was never published")
    assert voided.outcome is None and voided.brier is None and not voided.is_open
    with pytest.raises(EntryError, match="1 .* or 0"):
        ledger.resolve(_log(ledger, 0.5).ref, outcome=None, on=date(2026, 11, 9))

    [score] = ledger.scores()
    assert (score.owner, score.n, score.brier) == ("analyst", 2, 0.09)
    by_bucket = {(b.low, b.high): (b.n, b.mean_p, b.hit_rate) for b in score.buckets}
    assert by_bucket[(0.6, 0.8)] == (1, 0.7, 1.0) and by_bucket[(0.2, 0.4)] == (1, 0.3, 0.0)
    assert by_bucket[(0.8, 1.0)] == (0, None, None)  # the voided 0.9 call is not counted


def test_the_export_carries_the_ledger_and_shares_the_database_with_other_stores(
    ledger: Calibration, tmp_path: Path
) -> None:
    from harness.finance.ledger_log import Ledger, SyncRow

    _log(ledger, 0.7)
    finance = Ledger(ledger.db_path, tmp_path / "finance" / "ledger.jsonl")
    finance.add(SyncRow(date="2026-11-01", text="a balance changed"))
    assert ledger.rebuild() == 1  # rebuilding one store's tables leaves the other's alone
    assert len(finance.rows()) == 1 and len(ledger.entries()) == 1
    assert json.loads(ledger.export_path.read_text().splitlines()[0])["table"] == "calibration"


@pytest.mark.parametrize(("test", "nav", "pct", "want"), [
    ("Monday's struck NAV < 41.20 (the broker screen)", 41.08, -0.19, (1, "NAV 41.08 < 41.20 is true")),
    ("struck NAV >= 42.00", 41.08, -0.19, (0, "NAV 41.08 >= 42.00 is false")),
    ("day% between −0.60% and −0.30% inclusive", 41.08, -0.19, (0, "-0.19% is outside -0.60% to -0.30%")),
    ("day% between +2.5 and +4.5%", 41.90, 3.10, (1, "+3.10% is inside +2.50% to +4.50%")),
    ("the index settles under 5,000 every session", 41.08, -0.19, None),
])
def test_propose_reads_the_two_nav_shapes_and_nothing_else(
    test: str, nav: float, pct: float, want: tuple[int, str] | None
) -> None:
    assert propose(test, nav=nav, pct=pct) == want


def test_the_verbs_log_list_resolve_and_score(ledger: Calibration) -> None:
    def run(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == code, result.output
        return result.output

    assert "logged C001" in run("add", "the fund closes down", "--p", "0.7", "--by", "2026-11-02",
                                "--test", "NAV < 41.20", "--owner", "analyst", "--source", "a note")
    run("add", "no test", "--p", "0.7", "--by", "soon", "--test", "x", "--owner", "analyst", code=2)
    assert "C001" in run("due", "--on", "2026-11-02") and "nothing due" in run("due", "--on", "2026-11-01")
    assert "1 · brier 0.0900" in run("resolve", "C001", "--outcome", "1", "--note", "NAV 41.08")
    run("resolve", "C001", "--outcome", "0", code=2)  # resolves once
    [scored] = json.loads(run("score", "--json"))
    assert (scored["owner"], scored["n"], scored["brier"]) == ("analyst", 1, 0.09)
    assert json.loads(run("list", "--json"))[0]["note"] == "NAV 41.08"
