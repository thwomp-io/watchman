"""Jira-shaped ticket docs rendered from the beads export.

Each bead becomes `ops/beads/<id>.md` in the tracker vault — a GENERATED, read-only view
(bd is truth; the docs are a derived cache, gitignored). The console's `.md`-cell primitive
then turns every board row into a hyperlink, and dependency wikilinks make tickets
cross-navigate the way Jira's linked-issues panel does.

Sync discipline:
- deterministic output (no volatile timestamps inside files) → content-diff writes stay quiet
- a stamp file short-circuits the whole sync when the export hasn't moved
- pruning only ever deletes files carrying the GENERATED sentinel — a hand-written doc that
  wandered into the directory is never ours to remove
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

SENTINEL = "> GENERATED from the beads db"
RENDERER_VERSION = 1  # bump to force a full re-render on format changes
STAMP_NAME = ".tickets-stamp.json"

# dependency semantics (mirrors board.py): `depends_on_id` is the parent / the blocker.
_DEP_LABELS_OUT = {
    "parent-child": "Parent",
    "blocks": "Blocked by",
    "discovered-from": "Discovered from",
    "related": "Related",
}
_DEP_LABELS_IN = {
    "parent-child": "Children",
    "blocks": "Blocks",
    "discovered-from": "Discoveries filed from this",
    "related": "Related",
}


class TicketSync(BaseModel):
    written: int = 0
    unchanged: int = 0
    pruned: int = 0
    skipped: bool = False  # stamp said the export hasn't moved


_DATE_FIELDS = ("created_at", "updated_at", "closed_at", "started_at", "defer_until")


def _shift_date(value: Any, delta_days: int) -> Any:
    s = str(value or "")
    if len(s) < 10:
        return value
    try:
        moved = date.fromisoformat(s[:10]) + timedelta(days=delta_days)
    except ValueError:
        return value
    return moved.isoformat() + s[10:]


def load_issues(jsonl_path: Path) -> dict[str, dict[str, Any]]:
    """Parse the export; one bad line is a skipped row, never a dead read.

    DEMO-FIXTURE anchor: a fixture may carry a `{"_type": "meta", "anchor_date": "YYYY-MM-DD"}`
    line — every date in the file then shifts by (today - anchor), so a sample pack's backlog
    stays eternally fresh ("closed yesterday" is closed yesterday forever) instead of aging out
    of the shipped-this-week / active-tree windows. Real `bd export` files never carry the line,
    so live corpora are untouched — the static-fixture time-capsule class, closed at the loader.
    """
    issues: dict[str, dict[str, Any]] = {}
    anchor: date | None = None
    if not jsonl_path.exists():
        return issues
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("_type") == "meta" and d.get("anchor_date"):
            try:
                anchor = date.fromisoformat(str(d["anchor_date"]))
            except ValueError:
                anchor = None
            continue
        if d.get("_type") == "issue" and d.get("id"):
            issues[d["id"]] = d
    if anchor is not None:
        delta = (date.today() - anchor).days
        if delta:
            for d in issues.values():
                for f in _DATE_FIELDS:
                    if d.get(f):
                        d[f] = _shift_date(d[f], delta)
                for c in d.get("comments") or []:
                    if c.get("created_at"):
                        c["created_at"] = _shift_date(c["created_at"], delta)
    return issues


def _safe_id(id: str) -> bool:
    return bool(id) and all(c.isalnum() or c in "._-" for c in id) and ".." not in id


def _cell(v: str) -> str:
    return v.replace("|", "\\|").replace("\n", " ").strip()


def _d(ts: Any) -> str:
    return str(ts or "")[:10]


def _link_line(other: dict[str, Any] | None, dep_id: str) -> str:
    if other is None:
        return f"- [[{dep_id}]] — (not in export)"
    return f"- [[{dep_id}]] — {other.get('title', '')} · {other.get('status', '?')}"


def render_ticket(d: dict[str, Any], issues: dict[str, dict[str, Any]]) -> str:
    """One bead → one Jira-shaped markdown doc. Pure and deterministic."""
    id = str(d["id"])
    labels = d.get("labels") or []
    lines: list[str] = []

    lines.append("---")
    lines.append("tags: [bead" + (", " + ", ".join(labels) if labels else "") + "]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {id} — {d.get('title', '')}")
    lines.append("")
    chip = (
        f"**Status:** {d.get('status', '?')} · **Priority:** P{d.get('priority', 3)} · "
        f"**Type:** {d.get('issue_type', 'task')}"
    )
    if d.get("assignee"):
        chip += f" · **Assignee:** {d['assignee']}"
    lines.append(chip)
    lines.append("")
    lines.append(f"{SENTINEL} — a rendered view; edit via `bd`, never here.")
    lines.append("")

    lines.append("## Details")
    lines.append("")
    lines.append("| field | value |")
    lines.append("| --- | --- |")
    created = _d(d.get("created_at"))
    if d.get("created_by"):
        created += f" · {d['created_by']}"
    rows = [
        ("Created", created),
        ("Updated", _d(d.get("updated_at"))),
        ("Started", _d(d.get("started_at"))),
        ("Closed", _d(d.get("closed_at"))),
        ("Defer until", _d(d.get("defer_until"))),
        ("Labels", ", ".join(labels)),
        ("Owner", str(d.get("owner") or "")),
    ]
    for k, v in rows:
        if v:
            lines.append(f"| {k} | {_cell(v)} |")
    lines.append("")

    for heading, key in (
        ("Description", "description"),
        ("Acceptance criteria", "acceptance_criteria"),
        ("Design", "design"),
        ("Notes", "notes"),
    ):
        text = str(d.get(key) or "").strip()
        if text:
            lines.append(f"## {heading}")
            lines.append("")
            lines.append(text)
            lines.append("")

    # linked issues, both directions — outbound from this bead's deps, inbound by reverse scan
    groups: dict[str, list[str]] = {}
    for dep in d.get("dependencies") or []:
        label = _DEP_LABELS_OUT.get(str(dep.get("type")), "Related")
        target = str(dep.get("depends_on_id"))
        groups.setdefault(label, []).append(_link_line(issues.get(target), target))
    for other in issues.values():
        for dep in other.get("dependencies") or []:
            if str(dep.get("depends_on_id")) != id:
                continue
            label = _DEP_LABELS_IN.get(str(dep.get("type")), "Related")
            groups.setdefault(label, []).append(_link_line(other, str(other["id"])))
    if groups:
        lines.append("## Linked issues")
        lines.append("")
        for label in ("Parent", "Children", "Blocked by", "Blocks",
                      "Discovered from", "Discoveries filed from this", "Related"):
            if label in groups:
                items = sorted(set(groups[label]))
                lines.append(f"**{label}** ({len(items)})")
                lines.extend(items)
                lines.append("")

    close_reason = str(d.get("close_reason") or "").strip()
    if close_reason:
        lines.append("## Resolution")
        lines.append("")
        lines.append(close_reason)
        lines.append("")

    comments = d.get("comments") or []
    if comments:
        lines.append(f"## Comments ({len(comments)})")
        lines.append("")
        for c in sorted(comments, key=lambda c: str(c.get("created_at", ""))):
            author = str(c.get("author") or "?")
            when = str(c.get("created_at") or "")[:16].replace("T", " ")
            lines.append(f"**{author}** · {when}")
            lines.append("")
            body = str(c.get("text") or "").strip()
            lines.extend(f"> {ln}" if ln.strip() else ">" for ln in body.splitlines())
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def sync_tickets(jsonl_path: Path, out_dir: Path, *, force: bool = False) -> TicketSync:
    """Incrementally mirror the export into per-bead ticket docs. Fast path: a stamp file
    records the export's mtime_ns + renderer version; an unmoved export is a no-op stat."""
    res = TicketSync()
    if not jsonl_path.exists():
        return res
    stamp_path = out_dir / STAMP_NAME
    stamp = {"mtime_ns": jsonl_path.stat().st_mtime_ns, "renderer": RENDERER_VERSION}
    if not force and stamp_path.exists():
        try:
            if json.loads(stamp_path.read_text()) == stamp:
                res.skipped = True
                return res
        except (json.JSONDecodeError, OSError):
            pass  # unreadable stamp → full sync

    issues = load_issues(jsonl_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    live = set()
    for id, d in issues.items():
        if not _safe_id(id):
            continue
        live.add(f"{id}.md")
        target = out_dir / f"{id}.md"
        content = render_ticket(d, issues)
        if target.exists() and target.read_text() == content:
            res.unchanged += 1
            continue
        target.write_text(content)
        res.written += 1
    # prune docs for vanished beads — but ONLY files wearing the GENERATED sentinel
    for p in out_dir.glob("*.md"):
        if p.name in live:
            continue
        try:
            if SENTINEL in p.read_text()[:600]:
                p.unlink()
                res.pruned += 1
        except OSError:
            continue
    stamp_path.write_text(json.dumps(stamp))
    return res
