"""The sealed demo seeds each sample pack's Inbox: replaced on a persona switch, backdated, read kept."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.bus.models import EventDraft
from harness.bus.service import BusService
from harness.console.demo import INBOX_FILE, seed_inbox
from harness.packs import discover_bundled_packs


def _pack(root: Path, name: str, rows: list[dict[str, object]]) -> Path:
    pack = root / name
    (pack / "bus").mkdir(parents=True)
    (pack / INBOX_FILE).write_text(json.dumps(rows))
    return pack


def test_seed_replaces_the_previous_persona(tmp_path: Path) -> None:
    db = tmp_path / "bus.db"
    a = _pack(tmp_path, "a", [{"lane": "finance", "kind": "day_move", "title": "A1", "severity": "alert"},
                              {"lane": "travel", "kind": "deadline", "title": "A2", "severity": "warn"}])
    b = _pack(tmp_path, "b", [{"lane": "career", "kind": "scan_delta", "title": "B1"}])
    assert seed_inbox(a, db) == 2
    assert seed_inbox(b, db) == 1
    titles = [e.title for e in BusService(db).list_events(limit=50)]
    assert titles == ["B1"]


def test_seed_backdates_and_marks_read(tmp_path: Path) -> None:
    db = tmp_path / "bus.db"
    pack = _pack(tmp_path, "p", [{"lane": "finance", "kind": "catalyst", "title": "old", "ago_min": 120,
                                  "read": True},
                                 {"lane": "finance", "kind": "catalyst", "title": "new"}])
    seed_inbox(pack, db)
    events = {e.title: e for e in BusService(db).list_events(limit=50)}
    age = datetime.now(UTC) - datetime.fromisoformat(events["old"].created_at)
    assert 119 * 60 <= age.total_seconds() <= 125 * 60
    assert events["old"].read_at is not None
    assert events["new"].read_at is None


def test_a_pack_without_a_seed_gets_an_empty_inbox(tmp_path: Path) -> None:
    db = tmp_path / "bus.db"
    seed_inbox(_pack(tmp_path, "a", [{"lane": "finance", "kind": "x", "title": "A"}]), db)
    bare = tmp_path / "bare"
    bare.mkdir()
    assert seed_inbox(bare, db) == 0
    assert BusService(db).list_events(limit=50) == []


def test_every_bundled_pack_ships_a_valid_inbox() -> None:
    packs = discover_bundled_packs()
    assert packs
    for p in packs:
        rows = json.loads((Path(p.path) / INBOX_FILE).read_text())
        assert any(r.get("severity") in ("alert", "warn") for r in rows), p.name  # the triage bands show
        for r in rows:
            EventDraft(producer="demo", lane=r["lane"], kind=r["kind"], title=r["title"],
                       severity=r.get("severity", "info"))


def test_a_demo_pack_switch_seeds_before_it_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.console import api, scenario

    monkeypatch.setenv("HARNESS_CONSOLE_DEMO", "1")
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    packs = discover_bundled_packs()
    first, second = Path(packs[0].path), Path(packs[1].path)
    db = scenario.demo_seal_state_dir() / "bus.db"
    db.parent.mkdir(parents=True)
    seed_inbox(first, db)
    api._h_set_active_pack({"pack": str(second)})
    want = [r["title"] for r in json.loads((second / INBOX_FILE).read_text())]
    assert sorted(e.title for e in BusService(db).list_events(limit=50)) == sorted(want)
