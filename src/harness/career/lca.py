"""The DOL LCA filed-wage rail — the EDGAR analog for comp (rail 2).

Every H-1B Labor Condition Application files the offered wage by employer / job title / SOC /
worksite; OFLC publishes the quarterly disclosure workbooks
(`https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY{yyyy}_Q{n}.xlsx`,
FY2020 →). Keyless, no login — but **the DOL edge (Akamai) denies every request from this
harness's network (HTTP 403 on the page AND the files, browser headers included; verified
live 2026-09-09)**, so the download is a browser act: the verb names the URL and the drop folder,
and reads whatever workbook sits there. `flag.dol.gov` (reachable) hosts prevailing-wage
libraries, not the disclosures; levels.fyi's own `/h1b/` pages load their data from a JSON route
that 403s outside the page — neither is a rail.

Reading: the workbooks are 100+ MB with several hundred thousand rows. A stdlib streaming xlsx
reader (zipfile + iterparse over the sheet XML; shared strings resolved by index) ingests the
newest workbook ONCE into a slim JSON-lines twin in the cache dir (the ~14 columns this lane
uses), and every query greps the twin. Column names are matched by header, case-insensitive;
a missing required header is a named refusal listing what the sheet has — the record layout
is a moving target (the PDF that documents it sits behind the same edge).
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from harness._http import describe_http_error, get_with_retry
from harness.errors import ProviderError
from harness.paths import cache_file

LCA_URL = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx"
LCA_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"

_KEEP = (
    "CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE",
    "SOC_TITLE", "FULL_TIME_POSITION", "EMPLOYER_NAME", "WORKSITE_CITY", "WORKSITE_STATE",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY", "PREVAILING_WAGE",
    "PW_UNIT_OF_PAY", "PW_WAGE_LEVEL",
)
_REQUIRED = ("EMPLOYER_NAME", "JOB_TITLE", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY", "WORKSITE_STATE")

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_STEM = re.compile(r"LCA_Disclosure_Data_FY(\d{4})_Q(\d)", re.I)


def lca_dir() -> Path:
    return cache_file("lca")


def candidate_quarters(today: date | None = None, n: int = 6) -> list[tuple[int, int]]:
    """(fy, q) newest-first for the last `n` federal quarters (FY starts Oct 1; a quarter's
    workbook lands ~a month after it closes, so the newest candidate is usually absent)."""
    d = today or date.today()
    out: list[tuple[int, int]] = []
    cur = d
    while len(out) < n:
        fy = cur.year + 1 if cur.month >= 10 else cur.year
        q = ((cur.month - 10) % 12) // 3 + 1
        if (fy, q) not in out:
            out.append((fy, q))
        cur = (cur.replace(day=1) - timedelta(days=1))
    return out


def local_workbooks() -> list[Path]:
    """Dropped workbooks, newest (FY, Q) first."""
    found = []
    for p in lca_dir().glob("*.xlsx"):
        m = _STEM.search(p.name)
        if m:
            found.append(((int(m.group(1)), int(m.group(2))), p))
    return [p for _, p in sorted(found, reverse=True)]


def drop_instructions(today: date | None = None) -> str:
    fy, q = candidate_quarters(today)[1]  # the most recently CLOSED quarter is the likely newest file
    return (
        f"no LCA workbook in {lca_dir()} — the DOL edge denies downloads from this network (HTTP 403 "
        f"for every client here), so fetch it in a browser: {LCA_URL.format(fy=fy, q=q)} (or the newest "
        f"listed at {LCA_PAGE}) and drop the .xlsx into that folder; the reader picks the newest FY/Q"
    )


def ensure_workbook(*, try_download: bool = True, today: date | None = None) -> Path:
    """The newest local workbook; else one attempt per candidate URL (newest-first, stops at the
    first 403 — the edge answers the same for every file); else the drop instructions."""
    local = local_workbooks()
    if local:
        return local[0]
    if try_download:
        for fy, q in candidate_quarters(today):
            url = LCA_URL.format(fy=fy, q=q)
            try:
                resp = get_with_retry(url, allow_status={403, 404}, timeout=120.0)
            except httpx.HTTPError as e:
                raise ProviderError(f"LCA download {url}: {describe_http_error(e)}") from e
            if resp.status_code == 404:
                continue  # not published yet — try the prior quarter
            if resp.status_code == 403:
                break  # the edge, not the file: the manual path is the answer
            dest = lca_dir() / f"LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
    raise ProviderError(drop_instructions(today))


# ————— the streaming xlsx reader (stdlib) —————


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out: list[str] = []
    with z.open("xl/sharedStrings.xml") as f:
        for _, el in ET.iterparse(f):
            if el.tag == f"{_NS}si":
                out.append("".join(t.text or "" for t in el.iter(f"{_NS}t")))
                el.clear()
    return out


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def _first_sheet(z: zipfile.ZipFile) -> str:
    names = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not names:
        raise ProviderError("workbook has no worksheets")
    return names[0]


def iter_rows(path: Path) -> Iterator[list[str]]:
    """Rows as lists of cell strings (shared + inline strings resolved, numbers as their text),
    streamed — the sheet is never materialized. Excel date serials are left numeric; callers
    convert the columns they know are dates."""
    with zipfile.ZipFile(path) as z:
        strings = _shared_strings(z)
        with z.open(_first_sheet(z)) as f:
            for _, el in ET.iterparse(f):
                if el.tag != f"{_NS}row":
                    continue
                row: list[str] = []
                for c in el.iter(f"{_NS}c"):
                    idx = _col_index(c.get("r", ""))
                    while len(row) <= idx:
                        row.append("")
                    kind = c.get("t", "")
                    if kind == "s":
                        v = c.find(f"{_NS}v")
                        row[idx] = strings[int(v.text)] if v is not None and v.text else ""
                    elif kind == "inlineStr":
                        row[idx] = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
                    else:
                        v = c.find(f"{_NS}v")
                        row[idx] = (v.text or "") if v is not None else ""
                el.clear()
                yield row


def excel_date(v: str) -> str:
    """An Excel serial ('45900' / '45900.0') → ISO date; anything else passes through."""
    try:
        n = float(v)
    except ValueError:
        return v
    if n < 20000 or n > 80000:
        return v
    return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()


def slim_path(workbook: Path) -> Path:
    return workbook.with_suffix(".slim.jsonl")


def ingest(workbook: Path, *, force: bool = False) -> Path:
    """Stream the workbook once into its slim twin (the kept columns, header-matched). Returns
    the twin. A required header missing = a named refusal listing the sheet's headers."""
    out = slim_path(workbook)
    if out.exists() and not force and out.stat().st_mtime >= workbook.stat().st_mtime:
        return out
    rows = iter_rows(workbook)
    try:
        header = [h.strip().upper() for h in next(rows)]
    except StopIteration as e:
        raise ProviderError(f"{workbook.name}: empty sheet") from e
    pos = {h: i for i, h in enumerate(header)}
    missing = [h for h in _REQUIRED if h not in pos]
    if missing:
        raise ProviderError(
            f"{workbook.name}: required columns missing {missing}; the sheet has: "
            + ", ".join(header[:40]) + (" …" if len(header) > 40 else "")
        )
    keep = [(k, pos[k]) for k in _KEEP if k in pos]
    tmp = out.with_suffix(".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            rec: dict[str, str] = {}
            for k, i in keep:
                v = row[i] if i < len(row) else ""
                rec[k] = excel_date(v) if k.endswith("_DATE") else v
            if not rec.get("EMPLOYER_NAME"):
                continue
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            n += 1
    tmp.replace(out)
    if n == 0:
        raise ProviderError(f"{workbook.name}: no data rows read")
    return out


def annualize(wage: str, unit: str) -> int | None:
    """A filed wage in whatever unit the form used → yearly dollars (Hour ×2080, Week ×52,
    Bi-Weekly ×26, Month ×12); None when the figure does not parse."""
    try:
        w = float(str(wage).replace("$", "").replace(",", ""))
    except ValueError:
        return None
    u = (unit or "").strip().lower()
    mult = {"hour": 2080, "week": 52, "bi-weekly": 26, "biweekly": 26, "month": 12, "year": 1}.get(u, 1)
    return int(round(w * mult))


def query(
    slim: Path, employer: str, *, title: str | None = None, state: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Filed-wage rows for an employer (name substring, case-insensitive), optionally narrowed by
    title substring + worksite state. Rows carry `annual` (wage-from annualized) for reading."""
    emp, ttl, st = employer.lower(), (title or "").lower(), (state or "").upper()
    out: list[dict[str, Any]] = []
    with slim.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if emp not in rec.get("EMPLOYER_NAME", "").lower():
                continue
            if ttl and ttl not in rec.get("JOB_TITLE", "").lower():
                continue
            if st and rec.get("WORKSITE_STATE", "").upper() != st:
                continue
            rec["annual"] = annualize(rec.get("WAGE_RATE_OF_PAY_FROM", ""), rec.get("WAGE_UNIT_OF_PAY", ""))
            out.append(rec)
    out.sort(key=lambda r: (-(r["annual"] or 0), r.get("JOB_TITLE", "")))
    return out[:limit] if limit else out
