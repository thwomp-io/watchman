"""Posting capture — a job URL → the application folder (`posting.md` + `form.json` + an
`answers.yaml` skeleton). The first step of the apply pipeline (capture → assemble → submit).

The form schema is the whole point: the ATS's OWN application form (every field, its type, whether
it is required, its select options, the EEO/consent blocks, and whether the form carries an AI-usage
attestation) captured verbatim, so the packet assembled later is validated against what the form
will actually ask. Both supported ATSes expose it keyless (Greenhouse on the Job Board API with
`?questions=true`; Ashby via the hosted page's GraphQL). Capture is read-only: this module never
writes `applications.yaml`.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from harness.career.providers.greenhouse import extract_salary
from harness.career.service import slugify
from harness.errors import ProviderError
from harness.textutil import html_to_text
from harness.yamlio import load_yaml

FieldType = Literal[
    "text", "textarea", "file", "select", "multiselect", "boolean", "email", "phone",
    "number", "location", "lookup",
]
# `lookup`: a typeahead over a list too large to ship (Greenhouse's school list, a country picker).
# Authored as free text; the packet does not validate it against options; the browser fill types
# it and picks the matching suggestion (exact if offered, else the first).
FieldKind = Literal["system", "custom", "compliance"]


class PostingRef(BaseModel):
    """A URL resolved to (ats, board, job id) — what the fetchers need."""

    ats: Literal["greenhouse", "ashby"]
    board: str
    job_id: str


class FormOption(BaseModel):
    label: str
    value: str | int | bool


class FormField(BaseModel):
    key: str  # the ATS's field name/path — what the hosted form submits under
    label: str
    type: FieldType
    required: bool = False
    section: str = ""
    kind: FieldKind = "custom"
    options: list[FormOption] = []
    description: str = ""  # question help text, as plain text


class AiPolicy(BaseModel):
    """Whether the form carries an AI-usage attestation (a question on how AI may be used). When
    present, `apply` requires human-drafted provenance on every free-text answer."""

    present: bool = False
    url: str = ""
    question_key: str = ""


class FormSchema(BaseModel):
    ats: str
    board: str
    job_id: str
    apply_url: str
    ai_policy: AiPolicy = AiPolicy()
    fields: list[FormField] = []


class Posting(BaseModel):
    company: str
    title: str
    ats: str
    board: str
    job_id: str
    url: str
    apply_url: str
    location: str = ""
    salary: str | None = None  # as posted, unnormalized
    updated: str = ""
    captured: str = ""
    description: str = ""  # plain text


# ----- URL resolution -----

_GH_HOSTED = re.compile(r"https?://(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")
_GH_EMBED = re.compile(r"[?&]gh_jid=(\d+)")
_ASHBY = re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-fA-F-]{36})")


def resolve_posting_url(url: str, *, board_token: str | None = None) -> PostingRef:
    """Recognize the ATS + board + job id in a posting URL.

    Greenhouse hosted (`job-boards.greenhouse.io/{token}/jobs/{id}`) and Ashby hosted
    (`jobs.ashbyhq.com/{org}/{uuid}`) carry everything. A Greenhouse EMBED on a company's own site
    (`…?gh_jid={id}`) carries only the id — the board token must come from the caller (the
    watchlist row via `--company`). Anything else is an ATS without a keyless form (Workday,
    Eightfold, own portals): fail loud with the manual path named."""
    m = _GH_HOSTED.search(url)
    if m:
        return PostingRef(ats="greenhouse", board=m.group(1), job_id=m.group(2))
    m = _ASHBY.search(url)
    if m:
        return PostingRef(ats="ashby", board=m.group(1), job_id=m.group(2).lower())
    m = _GH_EMBED.search(url)
    if m:
        if not board_token:
            raise ProviderError(
                "a Greenhouse embed (?gh_jid=) carries no board token — pass --company <watchlist "
                "name> so the token comes from role-hunt/watchlist.yml"
            )
        return PostingRef(ats="greenhouse", board=board_token, job_id=m.group(1))
    raise ProviderError(
        f"no keyless application form for {url!r} — only Greenhouse and Ashby postings can be "
        "captured; for Workday / Eightfold / own portals the hosted form is the (browser) path"
    )


# ----- normalizers -----

_GH_TYPES: dict[str, FieldType] = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
}
_GH_SYSTEM = {"first_name", "last_name", "email", "phone", "resume", "cover_letter"}
# The `_text` companions of file fields (paste-instead-of-upload). The packet carries the file.
_GH_SKIP = {"resume_text", "cover_letter_text"}

# An AI-usage attestation: the label names AI + policy/usage/guidance in either order, or the
# help text links a candidate-AI page. Deliberately loose — a miss here silently drops the
# human-drafted provenance check, so over-match beats under-match.
_AI_POLICY_RE = re.compile(
    r"(\bAI\b.*\b(policy|usage|guidance|guidelines)\b)|(\b(policy|usage|guidance|guidelines)\b.*\bAI\b)",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href="([^"]+)"')


def _opt_value(v: dict[str, Any]) -> str | int | bool:
    """An option's submit value as the ATS typed it (int ids on Greenhouse, string keys on Ashby);
    a missing value falls back to the label so the option is still selectable by name."""
    val = v.get("value")
    if isinstance(val, (bool, int)):
        return val
    return str(val if val is not None else v.get("label", ""))


def _detect_ai_policy(label: str, description_html: str, key: str) -> AiPolicy | None:
    text = html.unescape(description_html or "")
    if _AI_POLICY_RE.search(label) or "candidate-ai" in text.lower():
        m = _HREF_RE.search(text)
        return AiPolicy(present=True, url=m.group(1) if m else "", question_key=key)
    return None


def _gh_fields(questions: list[dict[str, Any]], *, kind: FieldKind, section: str = "") -> tuple[
    list[FormField], AiPolicy | None
]:
    out: list[FormField] = []
    policy: AiPolicy | None = None
    for q in questions or []:
        label = str(q.get("label") or "")
        desc_html = str(q.get("description") or "")
        for f in q.get("fields") or []:
            name = str(f.get("name") or "")
            if not name or name in _GH_SKIP:
                continue
            ftype = _GH_TYPES.get(str(f.get("type")), "text")
            opts = [
                FormOption(label=str(v.get("label")), value=_opt_value(v))
                for v in (f.get("values") or [])
                if isinstance(v, dict) and "label" in v
            ]
            out.append(
                FormField(
                    key=name,
                    label=label,
                    type=ftype,
                    required=bool(q.get("required")),
                    section=section,
                    kind="system" if name in _GH_SYSTEM else kind,
                    options=opts,
                    description=html_to_text(desc_html) if desc_html else "",
                )
            )
            if policy is None and kind != "compliance":
                policy = _detect_ai_policy(label, desc_html, name)
    return out, policy


def greenhouse_extra_fields(raw: dict[str, Any], degrees: list[str] | None = None) -> list[FormField]:
    """The Greenhouse form fields the `questions` array does NOT carry:

    - the phone widget's `Country` picker (`#country`, a typeahead over country names) — rendered
      on every hosted/embed form, required on some; not derivable from the schema, so captured as
      an optional `lookup` the applicant block fills (`career.applicant.country`);
    - the Education block when the payload's `education` flag is set (`education_required` /
      `education_optional`): `school--0` (a typeahead over Greenhouse's paginated school list →
      `lookup`) + `degree--0` (a short list the boards API serves keyless → `select` with options).
      The form's "Add another" rows (`--1`, `--2`…) are the human's; one row is captured."""
    out = [FormField(
        key="country", label="Country (phone)", type="lookup", required=False, section="",
        kind="system", description="the phone widget's country picker; fills from the applicant block",
    )]
    edu = str(raw.get("education") or "")
    if edu.startswith("education"):
        req = edu.endswith("required")
        out.append(FormField(
            key="school--0", label="School", type="lookup", required=req, section="Education",
            kind="custom",
            description="typeahead over Greenhouse's school list — author the school's name as listed",
        ))
        out.append(FormField(
            key="degree--0", label="Degree", type="select", required=req, section="Education",
            kind="custom",
            options=[FormOption(label=d, value=d) for d in (degrees or [])],
        ))
    return out


def normalize_greenhouse(
    raw: dict[str, Any], company: str, *, degrees: list[str] | None = None
) -> tuple[Posting, FormSchema]:
    """The Job Board API's `jobs/{id}?questions=true` payload → (Posting, FormSchema).
    `degrees` is the board's degree list (fetched when the payload asks for education)."""
    url = str(raw.get("absolute_url") or "")
    fields, policy = _gh_fields(raw.get("questions") or [], kind="custom")
    fields += greenhouse_extra_fields(raw, degrees)
    loc_fields, loc_policy = _gh_fields(
        raw.get("location_questions") or [], kind="custom", section="Location"
    )
    for lf in loc_fields:
        # The hosted form's location autocomplete derives these from the typed city; a packet
        # cannot author coordinates by hand, so they are never a required-coverage failure.
        if lf.label.strip().lower() in ("longitude", "latitude"):
            lf.required = False
            lf.kind = "system"
            lf.description = "derived by the hosted form's location autocomplete — leave null"
    fields += loc_fields
    policy = policy or loc_policy
    for block in raw.get("compliance") or []:
        cf, _ = _gh_fields(
            block.get("questions") or [], kind="compliance",
            section=str(block.get("type") or "compliance").upper(),
        )
        fields += cf
    content = html.unescape(str(raw.get("content") or ""))
    board = str(raw.get("_board") or "")
    job_id = str(raw.get("id") or "")
    posting = Posting(
        company=company,
        title=str(raw.get("title") or ""),
        ats="greenhouse",
        board=board,
        job_id=job_id,
        url=url,
        apply_url=greenhouse_apply_url(url, board, job_id),
        location=str((raw.get("location") or {}).get("name") or ""),
        salary=extract_salary(str(raw.get("content") or "")),
        updated=str(raw.get("updated_at") or "")[:10],
        captured=date.today().isoformat(),
        description=html_to_text(content),
    )
    form = FormSchema(
        ats="greenhouse",
        board=posting.board,
        job_id=posting.job_id,
        apply_url=posting.apply_url,
        ai_policy=policy or AiPolicy(),
        fields=fields,
    )
    return posting, form


_GH_EMBED_FORM = "https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job_id}"


def greenhouse_apply_url(absolute_url: str, board: str, job_id: str) -> str:
    """Where the Greenhouse application FORM renders. A hosted board's `absolute_url`
    (`job-boards.greenhouse.io/{token}/jobs/{id}`) carries the form on the page. An EMBED board's
    `absolute_url` is the company's own role page (Stripe: a description with an "Apply" button, the
    form elsewhere) and the hosted `jobs/{id}` URL redirects straight back to it — the fill opened
    the description and filled nothing. Greenhouse's standalone form,
    `embed/job_app?for={token}&token={id}`, renders for every board (verified live 2026-09) — that is
    the apply URL whenever `absolute_url` is off greenhouse.io."""
    host = re.match(r"https?://([^/]+)", absolute_url or "")
    if host and host.group(1).endswith("greenhouse.io"):
        return absolute_url
    if board and job_id:
        return _GH_EMBED_FORM.format(board=board, job_id=job_id)
    return absolute_url


_ASHBY_TYPES: dict[str, FieldType] = {
    "File": "file",
    "String": "text",
    "LongText": "textarea",
    "Email": "email",
    "Phone": "phone",
    "Location": "location",
    "Number": "number",
    "Boolean": "boolean",
    "ValueSelect": "select",
    "MultiValueSelect": "multiselect",
    "SocialLink": "text",
    "Date": "text",
}
_BOOL_OPTIONS = [FormOption(label="Yes", value=True), FormOption(label="No", value=False)]


def normalize_ashby(job: dict[str, Any], form: dict[str, Any], company: str) -> tuple[
    Posting, FormSchema
]:
    """A board-list job + the hosted page's `applicationForm` node → (Posting, FormSchema)."""
    fields: list[FormField] = []
    policy: AiPolicy | None = None
    for section in form.get("sections") or []:
        title = str(section.get("title") or "")
        for entry in section.get("fieldEntries") or []:
            f = entry.get("field") or {}
            path = str(f.get("path") or "")
            if not path:
                continue
            ftype = _ASHBY_TYPES.get(str(f.get("type")), "text")
            opts = (
                _BOOL_OPTIONS
                if ftype == "boolean"
                else [
                    FormOption(label=str(v.get("label")), value=_opt_value(v))
                    for v in (f.get("selectableValues") or [])
                    if isinstance(v, dict) and "label" in v
                ]
            )
            label = str(f.get("title") or path)
            fields.append(
                FormField(
                    key=path,
                    label=label,
                    type=ftype,
                    required=bool(entry.get("isRequired")),
                    section=title,
                    kind="system" if path.startswith("_systemfield_") else "custom",
                    options=opts,
                    description=str(f.get("description") or ""),
                )
            )
            if policy is None:
                policy = _detect_ai_policy(label, str(f.get("description") or ""), path)
    comp = job.get("compensation") or {}
    loc = str(job.get("location") or "")
    if job.get("isRemote") and "remote" not in loc.lower():
        loc += " · remote"
    desc = str(job.get("descriptionPlain") or "") or html_to_text(str(job.get("descriptionHtml") or ""))
    posting = Posting(
        company=company,
        title=str(job.get("title") or ""),
        ats="ashby",
        board=str(job.get("_board") or ""),
        job_id=str(job.get("id") or ""),
        url=str(job.get("jobUrl") or ""),
        apply_url=str(job.get("applyUrl") or job.get("jobUrl") or ""),
        location=loc,
        salary=comp.get("scrapeableCompensationSalarySummary") or None,
        updated=str(job.get("publishedAt") or "")[:10],
        captured=date.today().isoformat(),
        description=desc,
    )
    schema = FormSchema(
        ats="ashby",
        board=posting.board,
        job_id=posting.job_id,
        apply_url=posting.apply_url,
        ai_policy=policy or AiPolicy(),
        fields=fields,
    )
    return posting, schema


# ----- writers -----

def default_slug(company: str, title: str) -> str:
    """`{company}-{title}` slugified, capped at 60 chars — the application folder key."""
    return slugify(f"{company}-{title}")[:60].rstrip("-")


def posting_markdown(p: Posting) -> str:
    fm = {
        "tags": ["career", "role-hunt", "posting"],
        "company": p.company,
        "title": p.title,
        "ats": p.ats,
        "board": p.board,
        "job_id": p.job_id,
        "url": p.url,
        "apply_url": p.apply_url,
        "location": p.location,
        "salary": p.salary or "",
        "posted": p.updated,
        "captured": p.captured,
    }
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return (
        f"---\n{head}\n---\n\n# {p.company} — {p.title}\n\n"
        f"> Captured {p.captured} by `hn career posting` from the ATS's public API. "
        f"Location: {p.location or '—'} · salary as posted: {p.salary or '—'}.\n\n"
        f"## Description\n\n{p.description}\n"
    )


def answers_skeleton(slug: str, posting: Posting, form: FormSchema) -> str:
    """The `answers.yaml` authoring surface: one entry per non-compliance field in form order,
    `value` and `drafted_by` null. Compliance/EEO blocks are deliberately absent — those are
    answered at the form by the human, never pre-filled from a file."""
    body = yaml.safe_dump(
        {"posting": slug, "answers": _skeleton_entries(form)}, sort_keys=False, allow_unicode=True
    )
    return _skeleton_header(posting, form) + body


def _skeleton_entries(form: FormSchema) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for f in form.fields:
        if f.kind == "compliance":
            continue
        e: dict[str, Any] = {"key": f.key, "label": f.label, "type": f.type, "required": f.required}
        if f.options:
            e["options"] = [o.label for o in f.options]
        e["value"] = None
        e["drafted_by"] = None
        entries.append(e)
    return entries


def _skeleton_header(posting: Posting, form: FormSchema) -> str:
    header = [
        f"# answers — {posting.company} · {posting.title}",
        "# One entry per form field, in the form's order. Fill `value`; for free-text answers set",
        "# `drafted_by` to human | human+refined | agent (the provenance the packet checks).",
        "# System fields (name/email/phone/links) fill from the harness.yaml `career.applicant`",
        "# block when left null. Select answers must match one of `options`.",
    ]
    if form.ai_policy.present:
        header += [
            "#",
            "# ⚠ THIS FORM CARRIES AN AI-USAGE ATTESTATION. Free-text answers must be drafted by you",
            "#   first (drafted_by: human or human+refined); agent-drafted text fails the packet.",
            f"#   Policy: {form.ai_policy.url or '(see the form question)'}",
        ]
    return "\n".join(header) + "\n\n"


def merge_answers(
    existing: str, slug: str, posting: Posting, form: FormSchema
) -> tuple[str, list[str], list[str]]:
    """Re-sync an authored `answers.yaml` to a re-captured form: rebuild the entries
    in the NEW form's order, carrying every authored `value` / `drafted_by` over by key, appending
    skeleton entries for fields the form gained, and moving entries for fields the form dropped to
    a `retired_answers` list (their authored text is never lost). Returns (text, added, retired)
    with the added/retired LABELS. Why: a `--force` re-capture (a company edits its form, a toolkit
    release captures more) used to leave the file untouched, so new required fields could only be
    answered by hand-authoring yaml with the right key and type."""
    try:
        old = load_yaml(existing) or {}
    except yaml.YAMLError:
        old = {}
    old_entries = old.get("answers") if isinstance(old, dict) else None
    old_entries = old_entries if isinstance(old_entries, list) else []
    by_key: dict[str, dict[str, Any]] = {
        str(e.get("key")): e for e in old_entries if isinstance(e, dict) and e.get("key")
    }
    retired_before = old.get("retired_answers") if isinstance(old, dict) else None
    retired: list[dict[str, Any]] = list(retired_before) if isinstance(retired_before, list) else []

    merged: list[dict[str, Any]] = []
    added: list[str] = []
    for e in _skeleton_entries(form):
        prev = by_key.pop(e["key"], None)
        if prev is None:
            added.append(str(e["label"]))
        else:
            e["value"] = prev.get("value")
            e["drafted_by"] = prev.get("drafted_by")
        merged.append(e)
    retired_labels = [str(v.get("label") or k) for k, v in by_key.items()]
    retired += list(by_key.values())

    doc: dict[str, Any] = {"posting": slug, "answers": merged}
    if retired:
        doc["retired_answers"] = retired
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    note = ""
    if retired:
        note = ("# `retired_answers`: fields the re-captured form no longer has — kept so nothing\n"
                "#   authored is lost; delete the block once you've checked it.\n\n")
    return _skeleton_header(posting, form) + note + body, added, retired_labels


def sync_answers(folder: Path, slug: str, posting: Posting, form: FormSchema) -> tuple[list[str], list[str]]:
    """Apply `merge_answers` to the folder's answers.yaml in place (temp file + rename). No-op
    when the file is absent (the skeleton is written by `write_posting_folder`)."""
    import os
    import tempfile

    path = folder / "answers.yaml"
    if not path.exists():
        return [], []
    text, added, retired = merge_answers(path.read_text(), slug, posting, form)
    fd, tmp = tempfile.mkstemp(prefix=".answers.", suffix=".yaml", dir=str(folder))
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return added, retired


def write_posting_folder(
    root: Path, slug: str, posting: Posting, form: FormSchema, *, force: bool = False
) -> Path:
    """Create `root/applications/{slug}/` with posting.md + form.json (+ answers.yaml if absent).
    Non-destructive: an existing posting.md is refused without `force`; answers.yaml is NEVER
    overwritten (it is the human's authoring surface)."""
    folder = root / "applications" / slug
    md = folder / "posting.md"
    if md.exists() and not force:
        raise ProviderError(f"{md} exists — pass --force to re-capture (answers.yaml is kept)")
    folder.mkdir(parents=True, exist_ok=True)
    md.write_text(posting_markdown(posting))
    (folder / "form.json").write_text(json.dumps(form.model_dump(), indent=2, ensure_ascii=False))
    answers = folder / "answers.yaml"
    if not answers.exists():
        answers.write_text(answers_skeleton(slug, posting, form))
    return folder


def load_form(folder: Path) -> FormSchema:
    path = folder / "form.json"
    if not path.exists():
        raise ProviderError(f"no form.json in {folder} — run `hn career posting <url>` first")
    try:
        return FormSchema.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError) as e:
        raise ProviderError(f"could not read {path}: {e}") from e


def load_posting_frontmatter(folder: Path) -> dict[str, Any]:
    """The posting.md frontmatter (company/title/urls) — the envelope's source."""
    path = folder / "posting.md"
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        data = load_yaml(text[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
