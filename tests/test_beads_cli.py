"""`hn beads` CLI — the --section pluck (the live-viz source path follow-on)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.beads.cli import app
from harness.finance.config.settings import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def tmp_tracker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRACKER_PATH", str(tmp_path))
    get_settings.cache_clear()  # settings are lru_cached — the env change must be seen
    yield tmp_path
    get_settings.cache_clear()


def test_section_tree_emits_the_tree_at_root(tmp_path: Path) -> None:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text(json.dumps({
        "_type": "issue", "id": "t-1", "title": "Solo", "status": "in_progress",
        "priority": 2, "issue_type": "task", "updated_at": "2026-01-01T00:00:00Z",
    }))
    res = runner.invoke(app, ["board", "--section", "tree"])
    assert res.exit_code == 0
    tree = json.loads(res.stdout)
    assert set(tree) == {"beads", "edges", "omitted"}
    assert tree["beads"][0]["id"] == "t-1"


def test_unknown_section_names_the_valid_ones(tmp_path: Path) -> None:
    res = runner.invoke(app, ["board", "--section", "nope"])
    assert res.exit_code != 0
    assert "tree" in res.output
