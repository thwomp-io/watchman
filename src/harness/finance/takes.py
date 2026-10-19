"""Write a market take — the one create-only corpus write an agent lane may be granted.

A take is a dated markdown file in `finance/market/takes/` (the Market tab's doc series reads that
directory; each take stands alone and the next one continues from it). This writer is deliberately
narrow so it can be handed to a sealed, otherwise read-only agent:

- the caller supplies only the BODY (plus a session label and tags) — never a path. The file name is
  the clock's (`YYYY-MM-DD-HHMM-take.md`, local time), the directory is fixed under the corpus root;
- the file is CREATED, never overwritten or appended to: `O_CREAT|O_EXCL|O_NOFOLLOW`, so an existing
  file or a symlink planted at the name refuses the write, and the takes directory itself must be a
  real directory inside the corpus (no symlinked escape);
- the writer owns the frontmatter (date, time, session, tags, author); a body that brings its own
  frontmatter is refused, a body without an H1 gets the standard timestamped one;
- the author is the caller's identity, set by the host (an env var the agent cannot change);
- no git: committing the file is left to you.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TAKES_REL = Path("finance") / "market" / "takes"
MAX_BODY_BYTES = 64 * 1024
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
# US zone abbreviations → the corpus's two-letter labels ("20:50 PT"); anything else keeps %Z.
_TZ_LABEL = {"PDT": "PT", "PST": "PT", "MDT": "MT", "MST": "MT", "CDT": "CT", "CST": "CT",
             "EDT": "ET", "EST": "ET"}


class TakeError(ValueError):
    """A refused write — the message says why, for the agent to correct and retry."""


@dataclass
class TakeWritten:
    path: str  # corpus-relative
    author: str
    date: str
    time: str
    bytes: int


def _clean_label(value: str, what: str) -> str:
    v = value.strip().lower()
    if not _SLUG.match(v):
        raise TakeError(f"{what} must be a short lowercase slug (a-z, 0-9, '-'): {value!r}")
    return v


def write_take(body: str, *, tracker_path: Path, author: str, session: str = "",
               tags: list[str] | None = None, now: datetime | None = None) -> TakeWritten:
    """Create one new take file. Raises TakeError on any refusal; never touches an existing file."""
    if not isinstance(body, str) or not body.strip():
        raise TakeError("the take body is empty")
    text = body.replace("\r\n", "\n").strip() + "\n"
    if text.startswith("---"):
        raise TakeError("send the body only — the writer adds the frontmatter "
                        "(date, time, session, tags, author)")
    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise TakeError(f"the take is over {MAX_BODY_BYTES // 1024} KB")
    author = _clean_label(author or "agent", "author")
    session = _clean_label(session, "session") if session.strip() else ""
    clean_tags = ["take"]
    for t in (tags or []):
        if t.strip() and (tag := _clean_label(t, "tag")) not in clean_tags:
            clean_tags.append(tag)

    stamp = (now or datetime.now()).astimezone()
    tz = _TZ_LABEL.get(stamp.strftime("%Z"), stamp.strftime("%Z"))
    date, hhmm = stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M")
    if not text.startswith("# "):
        text = f"# Take: {stamp:%a} {stamp.month}/{stamp.day} {hhmm} {tz} ({author})\n\n" + text

    root = tracker_path.expanduser().resolve(strict=True)
    takes = root / TAKES_REL
    if takes.is_symlink() or not takes.is_dir():
        raise TakeError(f"{TAKES_REL} is not a real directory in the corpus")
    if not takes.resolve(strict=True).is_relative_to(root):
        raise TakeError(f"{TAKES_REL} resolves outside the corpus")
    name = f"{date}-{stamp:%H%M}-take.md"
    target = takes / name

    front = ["---", f"date: {date}", f'time: "{hhmm} {tz}"']
    if session:
        front.append(f"session: {session}")
    front += [f"tags: [{', '.join(clean_tags)}]", f"author: {author}", "---", ""]
    payload = ("\n".join(front) + "\n" + text).encode("utf-8")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError as exc:
        raise TakeError(f"{TAKES_REL / name} already exists — takes are never overwritten; "
                        "wait a minute and write a new one") from exc
    except OSError as exc:
        raise TakeError(f"could not create {TAKES_REL / name}: {exc.strerror}") from exc
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    return TakeWritten(path=str(TAKES_REL / name), author=author, date=date, time=f"{hhmm} {tz}",
                       bytes=len(payload))


def announce(written: TakeWritten) -> str | None:
    """The bus audit line for a written take. Best-effort — a bus hiccup never fails the write — but a
    failure is RETURNED (the caller reports it), never swallowed."""
    try:
        from harness.bus.models import EventDraft
        from harness.bus.service import BusService

        svc = BusService(None)
        try:
            svc.publish_many([EventDraft(
                producer="takes", lane="finance", kind="take_written", subject=written.path,
                title=f"take written by {written.author}: {written.path}",
                payload={"path": written.path, "author": written.author, "date": written.date,
                         "time": written.time, "bytes": written.bytes},
                idempotency_key=f"takes:{written.path}",
            )])
        finally:
            svc.close()
    except Exception as exc:  # noqa: BLE001 — the file is written; the audit line is secondary
        return f"bus audit failed: {exc}"
    return None
