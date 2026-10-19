"""The gate — `hn career apply {slug} --submit --confirm`: the one execute-side act in the career
lane, and the last step of the apply pipeline (capture → assemble → submit).

Shape: ONE slug per run · nothing happens without `--confirm` · an existing receipt refuses a
second run · the transport is resolved from the captured form. Today no ATS on the watchlist has a
candidate-side application API (Greenhouse's POST is the board owner's — 401 for a candidate;
Ashby's form API needs the org key and the hosted page's submit mutation is undocumented), so
the working transport is `manual`: the human submits through the hosted form from the fill sheet
and this records the receipt + advances the pipeline. The `api` seam is here for the day an ATS
offers one; nothing else changes when it lands.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from harness.career.packet import Packet
from harness.career.posting import FormSchema
from harness.errors import ProviderError
from harness.yamlio import load_yaml


class SubmitNotAvailable(ProviderError):
    """No candidate-side API for this ATS — the browser path is named in the message."""


class Receipt(BaseModel):
    submitted_at: str
    via: str  # api | manual
    receipt: str  # what came back: confirmation id / page text / the ATS email subject / a time
    apply_url: str
    packet_sha256: str


_NO_API = {
    "greenhouse": "the Job Board API's POST needs the board owner's key (a candidate gets 401)",
    "ashby": "the application-form API needs the org key (401); the hosted page's submit "
    "mutation is undocumented and not a sanctioned path",
}


def api_submit(form: FormSchema, packet: Packet) -> Receipt:
    """The API transport seam. Raises `SubmitNotAvailable` for every ATS today (see module doc);
    a provider that gains a candidate-side POST implements it here and returns a Receipt."""
    reason = _NO_API.get(form.ats, "no candidate-side application API is known for this ATS")
    raise SubmitNotAvailable(
        f"{form.ats}: {reason}. Submit through the hosted form ({packet.apply_url}) from "
        f"packet.md, then record it: `hn career apply {packet.slug} --submit --confirm "
        f"--via manual --receipt \"<confirmation id / page text / email subject / time>\"`"
    )


def packet_sha256(folder: Path) -> str:
    import hashlib

    data = (folder / "packet.json").read_bytes()
    return hashlib.sha256(data).hexdigest()


def existing_receipt(folder: Path) -> Receipt | None:
    path = folder / "receipt.json"
    if not path.exists():
        return None
    try:
        return Receipt.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError) as e:
        raise ProviderError(f"could not read {path}: {e}") from e


def record_submission(
    folder: Path,
    packet: Packet,
    *,
    via: str,
    receipt_text: str,
    now: datetime | None = None,
) -> Receipt:
    """Write `receipt.json` — refused when one exists (a second submission is never automatic)."""
    if existing_receipt(folder) is not None:
        raise ProviderError(
            f"{folder / 'receipt.json'} exists — this packet was already recorded as submitted; "
            "a re-submission is a deliberate human act (remove the receipt first)"
        )
    if not receipt_text.strip():
        raise ProviderError("--receipt is required: what came back (id / page text / email subject / time)")
    rec = Receipt(
        submitted_at=(now or datetime.now()).replace(microsecond=0).isoformat(),
        via=via,
        receipt=receipt_text.strip(),
        apply_url=packet.apply_url,
        packet_sha256=packet_sha256(folder),
    )
    (folder / "receipt.json").write_text(json.dumps(rec.model_dump(), indent=2, ensure_ascii=False))
    return rec


# ----- applications.yaml — the one tool write, a line-level patch -----

_ITEM_RE = re.compile(r"^(\s*)- ")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w-]*):(.*)$")


def _entry_spans(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line spans of the top-level list items under `applications:` — each item runs
    from its `- ` line to the line before the next `- ` at the same indent (or EOF)."""
    start_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "applications:" or ln.rstrip().startswith("applications:"):
            start_idx = i
            break
    if start_idx is None:
        return []
    items: list[int] = []
    indent: int | None = None
    for i in range(start_idx + 1, len(lines)):
        m = _ITEM_RE.match(lines[i])
        if m:
            ind = len(m.group(1))
            if indent is None:
                indent = ind
            if ind == indent:
                items.append(i)
        elif lines[i].strip() and not lines[i].startswith(" ") and not lines[i].startswith("#"):
            break  # another top-level key
    spans: list[tuple[int, int]] = []
    for n, s in enumerate(items):
        e = items[n + 1] if n + 1 < len(items) else len(lines)
        # trim trailing blank lines out of the span so appends stay tidy
        while e > s + 1 and not lines[e - 1].strip():
            e -= 1
        spans.append((s, e))
    return spans


def _entry_dict(lines: list[str], span: tuple[int, int]) -> dict[str, object]:
    try:
        block = "\n".join(lines[span[0]:span[1]])
        data = load_yaml(block)
    except yaml.YAMLError:
        return {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _set_key(lines: list[str], span: tuple[int, int], key: str, value: str) -> tuple[int, int]:
    """Set `key: value` inside the item's span — replace the existing line (at the item's field
    indent only, so a `notes: >-` continuation can't be mistaken for a key), else insert right
    after the `- ` line. Returns the (possibly grown) span."""
    s, e = span
    dash = _ITEM_RE.match(lines[s])
    field_indent = (len(dash.group(1)) if dash else 0) + 2
    head = _KEY_RE.match(lines[s][field_indent:])  # the `- company: X` line's own key
    if head and head.group(1) == "" and head.group(2) == key:
        lines[s] = lines[s][:field_indent] + f"{key}: {value}"
        return span
    for i in range(s + 1, e):
        m = _KEY_RE.match(lines[i])
        if m and len(m.group(1)) == field_indent and m.group(2) == key:
            lines[i] = " " * field_indent + f"{key}: {value}"
            return span
    lines.insert(s + 1, " " * field_indent + f"{key}: {value}")
    return (s, e + 1)


def _q(value: str) -> str:
    """YAML-safe scalar for the values we write (quote when a plain scalar could mis-parse)."""
    if re.fullmatch(r"[A-Za-z0-9 ._/+-]+", value) and not value.strip().startswith(("-", "*")):
        return value
    return json.dumps(value, ensure_ascii=False)


def advance_pipeline(
    path: Path,
    *,
    slug: str,
    company: str,
    role: str,
    url: str,
    today: date | None = None,
) -> str:
    """Advance the matching row to `applied` (by `slug`, else company + role, case-insensitive),
    stamping `updated` and `url`, or append a new row. A LINE-LEVEL patch through a temp file +
    rename: the hand-edited file keeps its comments and formatting. Returns 'updated' | 'appended'."""
    d = (today or date.today()).isoformat()
    if path.exists():
        text = path.read_text()
    else:
        text = (
            "# Application / opportunity pipeline — hand-edited corpus; the one tool write\n"
            "# is `hn career apply --submit --confirm` advancing a row to applied.\n\napplications:\n"
        )
    lines = text.split("\n")
    # `applications: []` → open the list
    for i, ln in enumerate(lines):
        if ln.strip() in ("applications: []", "applications: [ ]"):
            lines[i] = "applications:"
    spans = _entry_spans(lines)
    match = None
    for span in spans:
        ent = _entry_dict(lines, span)
        if slug and str(ent.get("slug") or "") == slug:
            match = span
            break
    if match is None:
        for span in spans:
            ent = _entry_dict(lines, span)
            if (
                str(ent.get("company") or "").strip().lower() == company.strip().lower()
                and (not role or str(ent.get("role") or "").strip().lower() == role.strip().lower())
            ):
                match = span
                break
    if match is not None:
        span = match
        span = _set_key(lines, span, "stage", "applied")
        span = _set_key(lines, span, "updated", d)
        if url:
            span = _set_key(lines, span, "url", _q(url))
        if slug:
            span = _set_key(lines, span, "slug", _q(slug))
        action = "updated"
    else:
        if not any(ln.rstrip().startswith("applications:") for ln in lines):
            lines += ["applications:"]
        while lines and not lines[-1].strip():
            lines.pop()
        block = [
            "",
            f"  - company: {_q(company)}",
            f"    role: {_q(role)}",
            f"    slug: {_q(slug)}",
            "    stage: applied",
            "    source: direct apply",
            f"    updated: {d}",
            f"    url: {_q(url)}",
        ]
        lines += block
        action = "appended"
    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    # parse-check before touching the corpus file
    try:
        parsed = load_yaml(out)
    except yaml.YAMLError as e:
        raise ProviderError(f"refusing to write {path}: the patched YAML does not parse ({e})") from e
    if not isinstance(parsed, dict) or not isinstance(parsed.get("applications"), list):
        raise ProviderError(f"refusing to write {path}: patched YAML lost the applications list")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".applications.", suffix=".yaml", dir=str(path.parent))
    with os.fdopen(fd, "w") as fh:
        fh.write(out)
    os.replace(tmp, path)
    return action
