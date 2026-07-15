"""The beads board — pure computation over a fixture JSONL; date-injected."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from harness.beads.board import build_board

TODAY = date(2026, 7, 14)


def _issue(id: str, status: str = "open", priority: int = 2, **kw) -> dict:
    return {"_type": "issue", "id": id, "title": f"Issue {id}", "status": status,
            "priority": priority, "issue_type": "task", "updated_at": "2026-07-13T10:00:00Z", **kw}


def _write(tmp_path: Path, issues: list[dict]) -> Path:
    p = tmp_path / "issues.jsonl"
    p.write_text("\n".join(json.dumps(i) for i in issues))
    return p


def test_board_counts_and_bands(tmp_path: Path) -> None:
    p = _write(tmp_path, [
        _issue("t-1", priority=1),
        _issue("t-2", status="in_progress", assignee="pm"),
        _issue("t-3", status="closed", closed_at="2026-07-12T00:00:00Z"),
        _issue("t-4", status="closed", closed_at="2026-05-01T00:00:00Z"),  # ancient — not this week
        _issue("t-5", status="deferred"),
    ])
    b = build_board(p, today=TODAY)
    assert (b.total, b.open, b.in_progress, b.deferred, b.closed_7d) == (5, 1, 1, 1, 1)
    assert b.p1_open[0].id == "t-1"
    assert b.presence[0].assignee == "pm"
    assert b.shipped_7d[0].id == "t-3"
    assert b.exported_ago  # the staleness read always renders


def test_ready_respects_blocks_deps_and_defer_dates(tmp_path: Path) -> None:
    p = _write(tmp_path, [
        _issue("t-blocker", status="open"),
        _issue("t-blocked", dependencies=[{"depends_on_id": "t-blocker", "type": "blocks"}]),
        _issue("t-child", dependencies=[{"depends_on_id": "t-blocker", "type": "parent-child"}]),
        _issue("t-deferred", defer_until="2026-12-01"),
        _issue("t-defer-past", defer_until="2026-07-01"),
        _issue("t-dep-closed", dependencies=[{"depends_on_id": "t-done", "type": "blocks"}]),
        _issue("t-done", status="closed", closed_at="2026-07-01T00:00:00Z"),
    ])
    ready_ids = {r.id for r in build_board(p, today=TODAY).ready}
    assert "t-blocked" not in ready_ids          # open blocker gates it
    assert "t-child" in ready_ids                # parent-child is navigation, not a gate
    assert "t-deferred" not in ready_ids         # deferred to the future
    assert "t-defer-past" in ready_ids           # defer date passed
    assert "t-dep-closed" in ready_ids           # closed blocker releases it
    assert "t-blocker" in ready_ids


def test_rows_carry_ticket_links_when_rel_set(tmp_path: Path) -> None:
    p = _write(tmp_path, [_issue("t-1", priority=1)])
    with_links = build_board(p, today=TODAY, tickets_rel="ops/beads")
    assert with_links.p1_open[0].ticket == "ops/beads/t-1.md"
    without = build_board(p, today=TODAY)
    assert without.p1_open[0].ticket == ""


def test_tree_families_ancestors_and_quiet_singles(tmp_path: Path) -> None:
    p = _write(tmp_path, [
        _issue("t-epic", status="closed", closed_at="2026-01-01T00:00:00Z"),  # ancient ANCESTOR
        _issue("t-kid", dependencies=[{"depends_on_id": "t-epic", "type": "parent-child"}]),
        _issue("t-old-kid", status="closed", closed_at="2026-01-02T00:00:00Z",
               dependencies=[{"depends_on_id": "t-epic", "type": "parent-child"}]),
        _issue("t-single-active", status="in_progress"),
        _issue("t-single-quiet"),  # open, P2, no family → counted, not drawn
        _issue("t-blocker", status="in_progress",
               dependencies=[{"depends_on_id": "t-kid", "type": "blocks"}]),
    ])
    tree = build_board(p, today=TODAY).tree
    ids = {b.id for b in tree.beads}
    assert "t-epic" in ids          # ancient parent rides along for structure
    assert "t-kid" in ids
    assert "t-old-kid" not in ids   # long-closed child stays off
    assert "t-single-active" in ids
    assert "t-single-quiet" not in ids
    assert tree.omitted == 1
    kinds = {(e.source, e.target, e.kind) for e in tree.edges}
    assert ("t-epic", "t-kid", "child") in kinds
    assert ("t-kid", "t-blocker", "blocks") in kinds


def test_missing_export_is_calm_not_broken(tmp_path: Path) -> None:
    b = build_board(tmp_path / "nope.jsonl", today=TODAY)
    assert b.total == 0 and b.ready == []
    assert any("calm empty" in n for n in b.notes)


def test_corrupt_lines_are_skipped_rows_never_a_dead_board(tmp_path: Path) -> None:
    p = tmp_path / "issues.jsonl"
    p.write_text(json.dumps(_issue("t-ok")) + "\nNOT JSON\n")
    b = build_board(p, today=TODAY)
    assert b.total == 1 and b.open == 1
