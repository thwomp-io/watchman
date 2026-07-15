"""The generated ticket docs — pure render + sync discipline over fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from harness.beads.tickets import SENTINEL, load_issues, render_ticket, sync_tickets


def _issue(id: str, **kw) -> dict:
    return {"_type": "issue", "id": id, "title": f"Issue {id}", "status": "open",
            "priority": 2, "issue_type": "task", "created_at": "2026-07-01T09:00:00Z",
            "updated_at": "2026-07-02T10:00:00Z", **kw}


def _write(tmp_path: Path, issues: list[dict]) -> Path:
    p = tmp_path / "issues.jsonl"
    p.write_text("\n".join(json.dumps(i) for i in issues))
    return p


def test_render_carries_the_jira_anatomy() -> None:
    issues = {
        "t-epic": _issue("t-epic", issue_type="epic"),
        "t-1": _issue(
            "t-1",
            status="closed",
            assignee="pm",
            labels=["widget", "console"],
            description="Build the widget.",
            acceptance_criteria="Widget renders.",
            close_reason="Shipped in v1.2.0.",
            closed_at="2026-07-03T12:00:00Z",
            dependencies=[{"depends_on_id": "t-epic", "type": "parent-child"}],
            comments=[
                {"author": "pm", "created_at": "2026-07-02T11:00:00Z", "text": "First pass done."},
                {"author": "qa", "created_at": "2026-07-01T10:00:00Z", "text": "Claimed."},
            ],
        ),
    }
    doc = render_ticket(issues["t-1"], issues)
    assert doc.startswith("---\ntags: [bead, widget, console]")
    assert "# t-1 — Issue t-1" in doc
    assert "**Status:** closed · **Priority:** P2 · **Type:** task · **Assignee:** pm" in doc
    assert SENTINEL in doc
    assert "## Description" in doc and "Build the widget." in doc
    assert "## Acceptance criteria" in doc
    assert "**Parent** (1)" in doc and "[[t-epic]]" in doc
    assert "## Resolution" in doc and "Shipped in v1.2.0." in doc
    # comments read oldest-first (the story reads down)
    assert doc.index("Claimed.") < doc.index("First pass done.")
    # the epic's doc carries the inbound direction
    epic_doc = render_ticket(issues["t-epic"], issues)
    assert "**Children** (1)" in epic_doc and "[[t-1]]" in epic_doc


def test_render_is_deterministic() -> None:
    issues = {"t-1": _issue("t-1", description="Same in, same out.")}
    assert render_ticket(issues["t-1"], issues) == render_ticket(issues["t-1"], issues)


def test_sync_writes_stamps_and_skips_unmoved_export(tmp_path: Path) -> None:
    p = _write(tmp_path, [_issue("t-1"), _issue("t-2")])
    out = tmp_path / "docs"
    first = sync_tickets(p, out)
    assert (first.written, first.skipped) == (2, False)
    assert (out / "t-1.md").exists() and (out / "t-2.md").exists()
    second = sync_tickets(p, out)
    assert second.skipped  # stamp fast-path: unmoved export is a no-op
    forced = sync_tickets(p, out, force=True)
    assert not forced.skipped and forced.unchanged == 2 and forced.written == 0


def test_sync_prunes_vanished_beads_but_never_foreign_docs(tmp_path: Path) -> None:
    p = _write(tmp_path, [_issue("t-1"), _issue("t-2")])
    out = tmp_path / "docs"
    sync_tickets(p, out)
    hand_written = out / "hand-note.md"
    hand_written.write_text("# My own note\nNo sentinel here.")
    _write(tmp_path, [_issue("t-1")])  # t-2 vanishes from the export
    res = sync_tickets(p, out, force=True)
    assert res.pruned == 1
    assert not (out / "t-2.md").exists()
    assert hand_written.exists()  # the sentinel guard: never delete what we didn't generate


def test_sync_refuses_path_hostile_ids(tmp_path: Path) -> None:
    p = _write(tmp_path, [_issue("../escape"), _issue("t-ok")])
    out = tmp_path / "docs"
    res = sync_tickets(p, out, force=True)
    assert res.written == 1 and (out / "t-ok.md").exists()
    assert not (tmp_path / "escape.md").exists()


def test_meta_anchor_shifts_fixture_dates_to_stay_fresh(tmp_path: Path) -> None:
    from datetime import date, timedelta
    anchor = date.today() - timedelta(days=30)  # authored a month ago
    p = tmp_path / "issues.jsonl"
    p.write_text("\n".join([
        json.dumps({"_type": "meta", "anchor_date": anchor.isoformat()}),
        json.dumps(_issue(
            "t-1",
            status="closed",
            created_at=f"{(anchor - timedelta(days=3)).isoformat()}T09:00:00Z",
            closed_at=f"{(anchor - timedelta(days=1)).isoformat()}T12:00:00Z",
            comments=[{"author": "pm", "created_at": f"{anchor.isoformat()}T10:00:00Z", "text": "hi"}],
        )),
    ]))
    d = load_issues(p)["t-1"]
    # "closed a day before the anchor" reads as "closed yesterday" forever
    assert d["closed_at"].startswith((date.today() - timedelta(days=1)).isoformat())
    assert d["created_at"].startswith((date.today() - timedelta(days=3)).isoformat())
    assert d["closed_at"].endswith("T12:00:00Z")  # time-of-day preserved
    assert d["comments"][0]["created_at"].startswith(date.today().isoformat())


def test_no_meta_line_means_no_shifting(tmp_path: Path) -> None:
    p = _write(tmp_path, [_issue("t-1", closed_at="2026-01-05T00:00:00Z", status="closed")])
    assert load_issues(p)["t-1"]["closed_at"] == "2026-01-05T00:00:00Z"


def test_load_issues_skips_corrupt_lines(tmp_path: Path) -> None:
    p = tmp_path / "issues.jsonl"
    p.write_text(json.dumps(_issue("t-1")) + "\nNOT JSON\n")
    assert set(load_issues(p)) == {"t-1"}
