"""Packet assembly — `answers.yaml` + the folder's files, validated against `form.json`, into
`packet.json` (the manifest) + `packet.md` (the fill sheet). Then it STOPS: no network, no
pipeline write. The middle step of the apply pipeline (capture → assemble → submit).

Every check is deterministic and named, and all failures are collected before raising so one run
shows the whole punch list. The model's job (tailoring the résumé, refining answers) happens
BEFORE this in the folder; the human's job (reviewing the sheet, the browser fill) happens after.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from harness.career.posting import FormField, FormSchema, load_form, load_posting_frontmatter
from harness.errors import ProviderError
from harness.yamlio import load_yaml

HUMAN_PROVENANCE = {"human", "human+refined"}


class PacketError(ProviderError):
    """Assembly failed one or more named checks (`.failures`)."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__("; ".join(failures))


class Applicant(BaseModel):
    """The identity block from the overlay (`career.global_settings.applicant`) — fills a form's
    system fields when the answer is left null. Never sourced from engine code."""

    model_config = ConfigDict(extra="ignore")

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    country: str = ""  # the phone widget's country picker on Greenhouse forms, e.g. "United States"
    linkedin: str = ""
    github: str = ""
    website: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_empty(cls, v: object) -> object:
        # The packaged overlay template ships every applicant key as an explicit `null`
        # placeholder; an unset value means "nothing to fill", never a validation error.
        return "" if v is None else v

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)


class AnswerEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    label: str = ""
    type: str = "text"
    required: bool = False
    value: Any = None
    drafted_by: str | None = None


class AnswersFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    posting: str = ""
    answers: list[AnswerEntry] = []


class PacketField(BaseModel):
    key: str
    label: str
    type: str
    required: bool = False
    section: str = ""
    kind: str = "custom"
    value: Any = None


class PacketFile(BaseModel):
    key: str
    path: str  # folder-relative
    sha256: str
    bytes: int


class Packet(BaseModel):
    slug: str
    company: str
    title: str
    ats: str
    apply_url: str
    assembled_at: str
    ai_policy_url: str = ""
    fields: list[PacketField] = []
    files: list[PacketFile] = []
    checks: list[str] = []


def load_answers(folder: Path) -> AnswersFile:
    path = folder / "answers.yaml"
    if not path.exists():
        raise ProviderError(f"no answers.yaml in {folder} — `hn career posting` seeds it")
    try:
        raw = load_yaml(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ProviderError(f"answers.yaml parse failed: {e}") from e
    return AnswersFile.model_validate(raw)


# ----- identity fill -----

def _identity_value(field: FormField, applicant: Applicant) -> str:
    """What the applicant block offers for a system/profile field, by key then by label."""
    key, label = field.key.lower(), field.label.lower()
    if key in ("first_name",):
        return applicant.first_name
    if key in ("last_name",):
        return applicant.last_name
    if key in ("email", "_systemfield_email") or field.type == "email":
        return applicant.email
    if key in ("phone",) or field.type == "phone":
        return applicant.phone if "alt" not in label else ""
    if key == "_systemfield_name" or ("name" in label and "full" in label):
        return applicant.full_name
    if key == "_systemfield_location" or field.type == "location":
        return applicant.location
    if key == "country":
        return applicant.country
    if "linkedin" in label:
        return applicant.linkedin
    if "github" in label:
        return applicant.github
    if "portfolio" in label or "website" in label:
        return applicant.website
    return ""


# ----- checks -----

def _option_ok(field: FormField, value: Any) -> bool:
    if not field.options:
        return True
    labels = {o.label.lower() for o in field.options}
    values = {str(o.value).lower() for o in field.options}
    vals = value if isinstance(value, list) else [value]
    if field.type == "select" and isinstance(value, list):
        return False
    if field.type == "multiselect" and not isinstance(value, list):
        return False
    for v in vals:
        sv = str(v).lower()
        if sv not in labels and sv not in values:
            return False
    return True


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def _to_labels(field: FormField, value: Any) -> Any:
    """Normalize a select/multiselect/boolean answer to the form's option LABEL(S) — what the fill
    sheet shows and what the browser fill picks by. An authored value (an int id, `true`, a
    lower-cased label) resolves to its option's label; an unknown value passes through untouched
    (option validity has already been checked)."""
    if not field.options:
        return value
    by_value = {str(o.value).lower(): o.label for o in field.options}
    by_label = {o.label.lower(): o.label for o in field.options}

    def one(v: Any) -> Any:
        s = str(v).lower()
        return by_label.get(s) or by_value.get(s) or v

    if isinstance(value, list):
        return [one(v) for v in value]
    return one(value)


def assemble(
    folder: Path,
    applicant: Applicant,
    *,
    now: datetime | None = None,
) -> Packet:
    """Validate answers + files against the form and build the packet. Raises `PacketError` with
    every failure named; never writes."""
    form: FormSchema = load_form(folder)
    answers = load_answers(folder)
    fm = load_posting_frontmatter(folder)
    by_key = {a.key: a for a in answers.answers}
    failures: list[str] = []
    fields: list[PacketField] = []
    files: list[PacketFile] = []
    checks: list[str] = []

    for f in form.fields:
        if f.kind == "compliance":
            continue  # answered at the form by the human; never in a packet
        a = by_key.get(f.key)
        value = a.value if a else None
        drafted = (a.drafted_by or "").strip().lower() if a else ""
        identity_filled = False

        if _empty(value) and f.type != "file":
            filled = _identity_value(f, applicant)
            if filled:
                value = filled
                identity_filled = True  # a fact from the overlay, not prose — no provenance needed
        if f.type == "file":
            if _empty(value) and f.key == "resume" and (folder / "resume.pdf").exists():
                value = "resume.pdf"
            if _empty(value) and f.key == "_systemfield_resume" and (folder / "resume.pdf").exists():
                value = "resume.pdf"
            if not _empty(value):
                rel = str(value)
                path = (folder / rel)
                try:
                    inside = path.resolve().is_relative_to(folder.resolve())
                except (OSError, ValueError):
                    inside = False
                if not inside or not path.is_file():
                    failures.append(f"file not found in the folder: {f.key} → {rel}")
                else:
                    data = path.read_bytes()
                    files.append(PacketFile(
                        key=f.key, path=rel, sha256=hashlib.sha256(data).hexdigest(),
                        bytes=len(data),
                    ))
        if f.required and _empty(value):
            failures.append(f"required unanswered: {f.key} ({f.label})")
        if not _empty(value) and f.type in ("select", "multiselect", "boolean"):
            if not _option_ok(f, value):
                opts = ", ".join(o.label for o in f.options)
                failures.append(f"not one of the form's options: {f.key} = {value!r} (options: {opts})")
            else:
                value = _to_labels(f, value)
        if form.ai_policy.present and f.kind == "custom" and f.type in ("text", "textarea"):
            if not _empty(value) and not identity_filled and drafted not in HUMAN_PROVENANCE:
                failures.append(
                    f"AI-policy provenance: {f.key} ({f.label}) drafted_by={drafted or 'null'} — this "
                    f"form carries an AI-usage attestation ({form.ai_policy.url or 'see the form'}); "
                    "draft it yourself, then refine (drafted_by: human | human+refined)"
                )
        if form.ai_policy.present and f.key == form.ai_policy.question_key:
            if _empty(value) or drafted != "human":
                failures.append(
                    f"the AI-usage attestation ({f.key}) must be answered by you: set value + "
                    "drafted_by: human"
                )
        fields.append(PacketField(
            key=f.key, label=f.label, type=f.type, required=f.required, section=f.section,
            kind=f.kind, value=None if _empty(value) else value,
        ))

    if failures:
        raise PacketError(failures)
    checks = ["required-coverage", "option-validity", "files-resolve", "identity-fill"]
    if form.ai_policy.present:
        checks.append("ai-policy-provenance")
    stamp = (now or datetime.now()).replace(microsecond=0).isoformat()
    return Packet(
        slug=folder.name,
        company=str(fm.get("company") or ""),
        title=str(fm.get("title") or ""),
        ats=form.ats,
        apply_url=form.apply_url or str(fm.get("apply_url") or ""),
        assembled_at=stamp,
        ai_policy_url=form.ai_policy.url if form.ai_policy.present else "",
        fields=fields,
        files=files,
        checks=checks,
    )


# ----- the fill sheet -----

def fill_sheet(packet: Packet, comp_note: str | None = None) -> str:
    """`packet.md`: the form's questions in order with the answers beneath — what the human reviews
    and what the browser step reads from. Compliance/EEO blocks are named as the human's.
    `comp_note` = the salary-benchmark line from the vault's comp cache: the
    evaluation's salary dimension cites the ladder when one is cached, never a guess."""
    lines = [
        f"# Application packet — {packet.company} · {packet.title}",
        "",
        f"> Apply at: {packet.apply_url}",
        f"> Assembled {packet.assembled_at} · ATS `{packet.ats}` · checks: {', '.join(packet.checks)}",
    ]
    if packet.ai_policy_url:
        lines.append(f"> AI-usage attestation on this form — policy: {packet.ai_policy_url}")
    if comp_note:
        lines.append(f"> Comp benchmark (levels.fyi cache): {comp_note}")
    lines.append("")
    section = None
    for f in packet.fields:
        if f.section != section:
            section = f.section
            lines += [f"## {section or 'Application'}", ""]
        req = " *(required)*" if f.required else ""
        lines.append(f"**{f.label}**{req}  ")
        if f.type == "file":
            lines.append(f"→ file: `{f.value or '—'}`")
        elif isinstance(f.value, list):
            lines.append("→ " + ", ".join(str(v) for v in f.value))
        elif f.value is None:
            lines.append("→ —")
        elif f.type == "textarea":
            lines += ["", str(f.value).strip(), ""]
        else:
            lines.append(f"→ {f.value}")
        lines.append("")
    if packet.files:
        lines += ["## Files", ""]
        for pf in packet.files:
            lines.append(f"- `{pf.path}` · {pf.bytes:,} bytes · sha256 `{pf.sha256[:12]}…`")
        lines.append("")
    lines += [
        "---",
        "",
        "_Compliance / EEO / consent blocks are answered at the form by you; they are never "
        "pre-filled from a file. Record the submission afterwards with "
        f"`hn career apply {packet.slug} --submit --confirm --receipt \"…\"`._",
        "",
    ]
    return "\n".join(lines)


def write_packet(folder: Path, packet: Packet, comp_note: str | None = None) -> tuple[Path, Path]:
    pj = folder / "packet.json"
    pm = folder / "packet.md"
    pj.write_text(json.dumps(packet.model_dump(), indent=2, ensure_ascii=False))
    pm.write_text(fill_sheet(packet, comp_note))
    return pj, pm
