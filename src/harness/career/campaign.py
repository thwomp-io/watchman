"""The campaign — where the search stands, in one payload (the Career ▸ Campaign tab).

Four reads, all local, all read-only (stage edits stay the user's, in applications.yaml):

- **In flight** — one card per `applications.yaml` row that isn't closed: stage, next step and its
  date, last touch and days since, the channel it arrived on, the employer-screen line, the note.
  The next step's DATE comes from the career calendar (a milestone whose `ref` names the row's note)
  or, failing that, a month/day written in the next step itself. **Stale** = no touch in
  `career.campaign.stale_days` (overlay, default 21) and no dated next step — the "decide or archive"
  cue.
- **Drift** — an `inbound/*.md` note whose company has no pipeline row renders as a card flagged
  `drift` ("not in the pipeline"): an inbound that never made it into the yaml stays visible.
- **The career calendar** — `role-hunt/reference/career-calendar.yaml` (operator-authored
  milestones: screens, vests, bonus, operator-defined deadlines) from today on, with a day count.
- **Recruiter mail** — humans only: the recruiter / interview / ats classes of the mail index, never
  the Noise folder or job alerts. The mail lane is an optional private module; without it the list
  is empty and `mail_note` says why.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from harness.career.applications import Application, load_applications
from harness.career.pool import pool_flags, screen_out
from harness.career.service import slugify
from harness.yamlio import load_yaml

MAIL_CLASSES = ("recruiter", "interview", "ats")
DEFAULT_STALE_DAYS = 21
_FRONT = re.compile(r"\A---\n(.*?)\n---", re.S)
# a next step's date is written after its weekday ("Tue 3/4") — a bare month/day without a weekday is ignored
_MD = re.compile(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+(\d{1,2})/(\d{1,2})(?![\d/])", re.I)


class InFlight(BaseModel):
    company: str
    role: str = ""
    stage: str = "inbound"
    next_step: str = ""
    next_date: str | None = None  # YYYY-MM-DD
    next_time: str = ""
    last_touch: str = ""
    days_since: int | None = None
    channel: str = "other"
    screen_line: str = "clear"
    ref: str = ""
    url: str = ""
    location: str = ""
    comp_signal: str = ""
    drift: bool = False
    stale: bool = False


class Milestone(BaseModel):
    date: str
    label: str
    kind: str = ""
    time: str = ""
    ref: str = ""
    approx: bool = False
    days: int = 0


class CampaignReport(BaseModel):
    as_of: str
    stale_days: int
    in_flight: list[InFlight] = Field(default_factory=list)
    calendar: list[Milestone] = Field(default_factory=list)
    calendar_notes: list[str] = Field(default_factory=list)
    mail: list[dict[str, Any]] = Field(default_factory=list)
    mail_note: str = ""


def frontmatter(path: Path) -> dict[str, Any]:
    try:
        m = _FRONT.match(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    if not m:
        return {}
    try:
        raw = load_yaml(m.group(1))
    except Exception:  # noqa: BLE001 — a malformed note is skipped, never fatal
        return {}
    return raw if isinstance(raw, dict) else {}


def _iso(v: object) -> str:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    s = str(v or "")
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


def channel_of(source: str) -> str:
    s = source.lower()
    for key in ("gmail", "proton", "linkedin", "referral"):
        if key in s:
            return key
    return "email" if "email" in s else "other"


def screen_line(company: str, flags: dict[str, str], tags: list[str], out: frozenset[str]) -> str:
    """out:<axis> for a screen OUT flag · gray:<axis> for a gray flag or a gray-* note tag · clear."""
    flag = flags.get(slugify(company)) or flags.get(company.lower())
    if flag and flag in out:
        return f"out: {flag}"
    if flag and flag != "none":
        return f"gray: {flag.removeprefix('gray-')}"
    gray = next((t for t in tags if str(t).startswith("gray-")), None)
    return f"gray: {str(gray).removeprefix('gray-')}" if gray else "clear"


def date_in(text: str, after: date) -> str | None:
    """The first weekday-led month/day in `text` ("Tue 3/4, 14:00"), placed in the first year
    that puts it on or after `after`. None when the text names no such date."""
    m = _MD.search(text or "")
    if not m:
        return None
    mo, dd = int(m.group(1)), int(m.group(2))
    for year in (after.year, after.year + 1):
        try:
            d = date(year, mo, dd)
        except ValueError:
            return None
        if d >= after:
            return d.isoformat()
    return None


def load_calendar(role_hunt: Path) -> tuple[list[dict[str, Any]], list[str]]:
    p = role_hunt / "reference" / "career-calendar.yaml"
    if not p.exists():
        return [], []
    raw = load_yaml(p.read_text(encoding="utf-8")) or {}
    rows = [m for m in (raw.get("milestones") or []) if isinstance(m, dict)]
    return rows, [str(n) for n in raw.get("notes") or []]


def _mail(limit: int) -> tuple[list[dict[str, Any]], str]:
    try:
        from harness.private.mail.store import MailStore  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return [], "the mail lane is not installed on this checkout"
    try:
        store = MailStore()
        rows: list[Any] = []
        for cls in MAIL_CLASSES:
            rows += store.list(cls=cls, limit=limit)
    except Exception as e:  # noqa: BLE001 — mail is one band of the tab; it never sinks the rest
        return [], f"mail index unreadable: {e}"
    out = []
    for r in rows:
        if "noise" in str(getattr(r, "folder", "")).lower():
            continue
        out.append({
            "uid": getattr(r, "uid", None), "date": str(getattr(r, "date", ""))[:16],
            "class": getattr(r, "cls", ""), "company": getattr(r, "company", ""),
            "from": getattr(r, "from_name", "") or getattr(r, "from_addr", ""),
            "subject": getattr(r, "subject", ""), "folder": getattr(r, "folder", ""),
        })
    out.sort(key=lambda m: str(m["date"]), reverse=True)
    return out[:limit], ""


def build_campaign(
    tracker: Path,
    role_hunt: Path,
    *,
    today: date | None = None,
    stale_days: int | None = None,
    mail_limit: int = 12,
    with_mail: bool = True,
) -> CampaignReport:
    from harness.settings import overlay_get

    today = today or date.today()
    if stale_days is None:
        try:
            stale_days = int(str(overlay_get("career", "campaign", "stale_days", default=DEFAULT_STALE_DAYS)))
        except (TypeError, ValueError):
            stale_days = DEFAULT_STALE_DAYS
    flags = pool_flags(role_hunt)
    out = screen_out(role_hunt)
    milestones, notes = load_calendar(role_hunt)
    by_ref = {str(m.get("ref")): m for m in milestones if m.get("ref")}

    # inbound notes by company: the newest date + the union of tags + the newest note's fields
    inbound: dict[str, dict[str, Any]] = {}
    for p in sorted((role_hunt / "inbound").glob("*.md")):
        fm = frontmatter(p)
        company = str(fm.get("company") or "").strip()
        if not company:
            continue
        key = slugify(company)
        d = _iso(fm.get("date")) or p.name[:10]
        cur = inbound.setdefault(key, {"company": company, "date": "", "tags": [], "files": []})
        cur["tags"] += [str(t) for t in fm.get("tags") or []]
        cur["files"].append(p)
        if d >= cur["date"]:
            cur.update(date=d, role=str(fm.get("role") or ""), source=str(fm.get("source") or ""),
                       ref=f"role-hunt/inbound/{p.name}")

    def card(a: Application, *, drift: bool = False, extra: dict[str, Any] | None = None) -> InFlight:
        ib = inbound.get(slugify(a.company), {})
        touch = max(filter(None, [_iso(a.updated), ib.get("date", "")]), default="")
        days = (today - date.fromisoformat(touch)).days if touch else None
        ms = by_ref.get(a.ref) if a.ref else None
        nd = _iso(ms.get("date")) if ms else None
        nd = nd or date_in(a.next_step, today)
        if nd and nd < today.isoformat():
            nd = None  # a date already passed is not a next step
        c = InFlight(
            company=a.company, role=a.role, stage=a.stage, next_step=a.next_step, next_date=nd,
            next_time=str(ms.get("time") or "") if ms else "", last_touch=touch, days_since=days,
            channel=channel_of(a.source), screen_line=screen_line(a.company, flags, ib.get("tags", []), out),
            ref=a.ref or ib.get("ref", ""), url=a.url, location=a.location, comp_signal=a.comp_signal,
            drift=drift,
        )
        c.stale = nd is None and days is not None and days >= stale_days
        if extra:
            c = c.model_copy(update=extra)
        return c

    pipe = load_applications(tracker, root=role_hunt).applications
    cards = [card(a) for a in pipe if a.stage != "closed"]
    known = {slugify(a.company) for a in pipe}
    for key, ib in inbound.items():
        if key in known:
            continue
        stub = Application(company=ib["company"], role=ib.get("role", ""), stage="inbound",
                           source=ib.get("source", ""), updated=ib["date"], ref=ib.get("ref", ""))
        cards.append(card(stub, drift=True))
    # dated next steps first (soonest first), then the undated by most recent touch, stale last
    cards.sort(key=lambda c: (c.stale, c.next_date is None, c.next_date or "",
                              c.days_since if c.days_since is not None else 10_000))

    cal = []
    for m in milestones:
        d = _iso(m.get("date"))
        if not d or d < today.isoformat():
            continue
        cal.append(Milestone(date=d, label=str(m.get("label", "")), kind=str(m.get("kind", "")),
                             time=str(m.get("time") or ""), ref=str(m.get("ref") or ""),
                             approx=bool(m.get("approx")), days=(date.fromisoformat(d) - today).days))
    cal.sort(key=lambda m: m.date)

    mail, note = _mail(mail_limit) if with_mail else ([], "")
    return CampaignReport(as_of=today.isoformat(), stale_days=stale_days, in_flight=cards, calendar=cal,
                          calendar_notes=notes, mail=mail, mail_note=note)
