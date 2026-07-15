"""Pure board computation over the issues.jsonl export.

`ready` is an HONEST approximation of `bd ready`: open + not deferred-to-the-future + no OPEN
issue on the blocking side of its dependencies. Dependency types: only `blocks` gates readiness
(`parent-child`/`discovered-from`/`related` are navigation, not gates — mirroring bd's semantics).
The export is authoritative-enough for a read-only glance surface; state CHANGES go through bd.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from harness.beads.tickets import load_issues


class BeadRow(BaseModel):
    id: str
    title: str
    status: str
    priority: str  # "P1".."P4" (pre-rendered — the table is the consumer)
    type: str = "task"
    assignee: str = ""
    labels: str = ""  # joined for the table cell
    updated: str = ""  # date only
    description: str = ""  # full text — reachable via the table's RAW toggle
    ticket: str = ""  # vault-relative doc path — the console renders it "open ↗"


class TreeNode(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    type: str = "task"
    assignee: str = ""
    labels: str = ""
    updated: str = ""
    ticket: str = ""


class TreeEdge(BaseModel):
    source: str  # parent (or blocker)
    target: str  # child (or blocked)
    kind: str  # "child" | "blocks"


class BeadTreeData(BaseModel):
    """The family-tree contract: active/recent beads + their ancestry, laid out
    client-side as an org chart. Keys are deliberately `beads`/`edges` (NOT nodes/links) so the
    console's shape-sniffer can't confuse it with a sankey."""

    beads: list[TreeNode] = Field(default_factory=list)
    edges: list[TreeEdge] = Field(default_factory=list)
    omitted: int = 0  # standalone quiet beads left off the tree (honesty count, never silent)


class BeadsBoard(BaseModel):
    as_of: str
    source: str  # which file was read (the liveness/provenance read)
    # The export is PASSIVE — it lags live bd ops until the next `bd export` fires. Surfacing its
    # age is the liveness≠freshness doctrine applied to this surface: the board must never
    # perform more currency than its file has.
    exported_ago: str = ""  # human ("2h ago") from the file mtime
    total: int = 0
    open: int = 0
    in_progress: int = 0
    deferred: int = 0
    closed_7d: int = 0
    p1_count: int = 0
    p1_open: list[BeadRow] = Field(default_factory=list)
    presence: list[BeadRow] = Field(default_factory=list)  # in_progress w/ assignee = who's on what
    ready: list[BeadRow] = Field(default_factory=list)
    shipped_7d: list[BeadRow] = Field(default_factory=list)
    tree: BeadTreeData = Field(default_factory=BeadTreeData)
    notes: list[str] = Field(default_factory=list)


def _row(d: dict[str, Any], tickets_rel: str = "") -> BeadRow:
    ts = str(d.get("updated_at") or d.get("created_at") or "")
    id = str(d.get("id", ""))
    return BeadRow(
        id=id,
        title=str(d.get("title", "")),
        status=str(d.get("status", "")),
        priority=f"P{d.get('priority', 3)}",
        type=str(d.get("issue_type", "task")),
        assignee=str(d.get("assignee") or ""),
        labels=", ".join(d.get("labels") or []),
        updated=ts[:10],
        description=str(d.get("description") or ""),
        ticket=f"{tickets_rel}/{id}.md" if tickets_rel and id else "",
    )


def _node(d: dict[str, Any], tickets_rel: str) -> TreeNode:
    r = _row(d, tickets_rel)
    return TreeNode(
        id=r.id, title=r.title, status=r.status, priority=r.priority, type=r.type,
        assignee=r.assignee, labels=r.labels, updated=r.updated, ticket=r.ticket,
    )


def build_tree(
    issues: dict[str, dict[str, Any]], *, today: date, tickets_rel: str = "",
) -> BeadTreeData:
    """The family-tree scope rule: every ACTIVE-OR-RECENT bead (open/in_progress/deferred, or
    closed within 7d) that belongs to a FAMILY (touches a parent-child edge), plus the ancestors
    needed for structure regardless of age. Standalone beads join only when they demand attention
    (in progress, P1 open, or freshly shipped) — the quiet singles are counted, never silently
    dropped. Blocks-deps overlay as dashed edges when both ends are on the tree."""
    week_ago = (today - timedelta(days=7)).isoformat()

    def active_or_recent(d: dict[str, Any]) -> bool:
        s = d.get("status")
        if s in ("open", "in_progress", "deferred"):
            return True
        return s == "closed" and str(d.get("closed_at", ""))[:10] >= week_ago

    parent_of: dict[str, str] = {}
    for d in issues.values():
        for dep in d.get("dependencies") or []:
            if dep.get("type") == "parent-child" and str(dep.get("depends_on_id")) in issues:
                parent_of[str(d["id"])] = str(dep.get("depends_on_id"))

    in_family = set(parent_of) | set(parent_of.values())
    keep: set[str] = set()
    for id, d in issues.items():
        if not active_or_recent(d):
            continue
        if id in in_family:
            keep.add(id)
        elif d.get("status") == "in_progress" or (
            d.get("status") == "open" and d.get("priority") == 1
        ) or d.get("status") == "closed":
            keep.add(id)  # standalone but attention-worthy
    # ancestors ride along for structure, whatever their age
    frontier = list(keep)
    while frontier:
        id = frontier.pop()
        parent = parent_of.get(id)
        if parent and parent not in keep:
            keep.add(parent)
            frontier.append(parent)

    omitted = sum(
        1 for id, d in issues.items() if active_or_recent(d) and id not in keep
    )
    edges = [
        TreeEdge(source=parent, target=child, kind="child")
        for child, parent in parent_of.items()
        if child in keep and parent in keep
    ]
    for d in issues.values():
        for dep in d.get("dependencies") or []:
            if dep.get("type") != "blocks":
                continue
            blocker, blocked = str(dep.get("depends_on_id")), str(d["id"])
            if blocker in keep and blocked in keep:
                edges.append(TreeEdge(source=blocker, target=blocked, kind="blocks"))

    def freshness(id: str) -> str:
        return str(issues[id].get("updated_at") or "")

    beads = [_node(issues[id], tickets_rel) for id in sorted(keep, key=freshness, reverse=True)]
    return BeadTreeData(beads=beads, edges=edges, omitted=omitted)


def build_board(
    jsonl_path: Path, *, today: date | None = None, ready_limit: int = 15, tickets_rel: str = "",
) -> BeadsBoard:
    today = today or date.today()
    as_of = datetime.now().isoformat(timespec="seconds")
    if not jsonl_path.exists():
        return BeadsBoard(
            as_of=as_of, source=str(jsonl_path),
            notes=["no beads export found — a fresh corpus (or a demo pack) has no backlog; calm empty"],
        )

    mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
    age_min = max(0, int((datetime.now() - mtime).total_seconds() // 60))
    exported_ago = (
        f"{age_min}m ago" if age_min < 120 else
        f"{age_min // 60}h ago" if age_min < 2880 else f"{age_min // 1440}d ago"
    )

    issues = load_issues(jsonl_path)

    week_ago = today - timedelta(days=7)
    open_rows = [d for d in issues.values() if d.get("status") == "open"]
    in_prog = [d for d in issues.values() if d.get("status") == "in_progress"]
    deferred = [d for d in issues.values() if d.get("status") == "deferred"]
    closed_7d = [
        d for d in issues.values()
        if d.get("status") == "closed" and str(d.get("closed_at", ""))[:10] >= week_ago.isoformat()
    ]

    def is_ready(d: dict[str, Any]) -> bool:
        du = str(d.get("defer_until") or "")[:10]
        if du and du > today.isoformat():
            return False
        for dep in d.get("dependencies") or []:
            if dep.get("type") != "blocks":
                continue
            blocker = issues.get(str(dep.get("depends_on_id")))
            if blocker is not None and blocker.get("status") != "closed":
                return False
        return True

    def fresh_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = sorted(rows, key=lambda d: str(d.get("updated_at", "")), reverse=True)
        return sorted(rows, key=lambda d: int(d.get("priority", 3)))

    ready = fresh_first([d for d in open_rows if is_ready(d)])

    return BeadsBoard(
        as_of=as_of,
        source=str(jsonl_path),
        exported_ago=exported_ago,
        total=len(issues),
        open=len(open_rows),
        in_progress=len(in_prog),
        deferred=len(deferred),
        closed_7d=len(closed_7d),
        p1_count=sum(1 for d in open_rows if d.get("priority") == 1),
        p1_open=[_row(d, tickets_rel) for d in fresh_first([d for d in open_rows if d.get("priority") == 1])],
        presence=[_row(d, tickets_rel) for d in fresh_first(in_prog)],
        ready=[_row(d, tickets_rel) for d in ready[:ready_limit]],
        shipped_7d=[
            _row(d, tickets_rel)
            for d in sorted(closed_7d, key=lambda d: str(d.get("closed_at", "")), reverse=True)
        ],
        tree=build_tree(issues, today=today, tickets_rel=tickets_rel),
        notes=[],
    )
