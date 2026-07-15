"""SEC Form N-PORT — the FULL mutual-fund holdings roster, keyless.

A registered fund's complete portfolio (every position, weights + values + shares) is public
quarterly via N-PORT, ~60 days after each fiscal quarter — the fund company's top-10 web page is
marketing; the filing is law. This module discovers the newest public filing for a fund (EDGAR
full-text search), fetches its primary_doc.xml, parses the roster, and can regenerate the corpus
reference yaml (finance/reference/<fund>-holdings.yaml) while PRESERVING per-name enrichment
(sector / us / note) — plus a roster diff vs the prior file (entries/exits/weight shifts = the
manager's hand made visible — e.g. a top holding's weight shifting sharply between filings).

UA posture (probed 2026-07-13): efts.sec.gov accepts the harness's non-PII UA; **www.sec.gov/Archives
WAF-requires an email-style contact UA** (SEC fair-access policy). Shipping a contact would violate
the non-PII default (cf. news_provider's same finding), so the archive fetch reads
``HARNESS_SEC_CONTACT`` from the environment (the gitignored .env is its home, same as the Alpaca
keys) and fails LOUD with instructions when unset. Nothing personal ships in this tree.
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field

from harness._http import get_with_retry
from harness.errors import ProviderError

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/primary_doc.xml"


class NportHolding(BaseModel):
    name: str
    pct: float  # % of net assets (sums to ~100.x — leverage/payables skew is normal)
    val_usd: float
    shares: str | None = None
    cusip: str | None = None
    country: str | None = None


class NportFiling(BaseModel):
    """One parsed N-PORT filing — the roster as-of `period` (the ~1-quarter lag is the design)."""

    series: str
    period: str  # holdings as-of date (YYYY-MM-DD)
    accession: str
    filed: str  # filing date (YYYY-MM-DD)
    total_assets_usd: float | None = None
    holdings: list[NportHolding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NportDiff(BaseModel):
    """Roster delta between two filings/files — the manager's rotation, itemized."""

    entries: list[str] = Field(default_factory=list)  # "NAME (new at X.XX%)"
    exits: list[str] = Field(default_factory=list)  # "NAME (was X.XX%)"
    shifts: list[str] = Field(default_factory=list)  # "NAME X.XX% -> Y.YY%" (moves >= threshold)


def _sec_contact_headers() -> dict[str, str]:
    contact = os.environ.get("HARNESS_SEC_CONTACT", "").strip()
    if not contact:
        raise ProviderError(
            "SEC archive fetches require an email-contact User-Agent (SEC fair-access policy; "
            "www.sec.gov WAF-blocks anonymous UAs — probed 2026-07-13). Set HARNESS_SEC_CONTACT "
            "in the harness .env (gitignored): HARNESS_SEC_CONTACT=<your contact address>. "
            "The contact is sent ONLY to www.sec.gov, never shipped."
        )
    return {"User-Agent": f"harness-nport ({contact})"}


def latest_filing_ref(query: str, cik: str) -> tuple[str, str]:
    """Newest public NPORT-P accession for `query` under `cik` via EDGAR full-text search.

    Returns (accession, file_date). EFTS accepts the standard non-PII UA.
    """
    resp = get_with_retry(_EFTS_URL, params={"q": f'"{query}"', "forms": "NPORT-P"})
    hits = (resp.json().get("hits", {}) or {}).get("hits", []) or []
    cik_int = str(int(cik))
    dated: list[tuple[str, str]] = []
    for h in hits:
        src = h.get("_source", {}) or {}
        ciks = [str(int(c)) for c in src.get("ciks", []) if str(c).strip().isdigit()]
        if cik_int not in ciks:
            continue
        accession = str(h.get("_id", "")).split(":")[0]
        file_date = str(src.get("file_date", ""))
        if accession and file_date:
            dated.append((file_date, accession))
    if not dated:
        raise ProviderError(
            f"no NPORT-P full-text hits for {query!r} under CIK {cik} — check the query "
            "(the fund's exact series name) or the CIK (the TRUST files, not the fund)."
        )
    dated.sort(reverse=True)
    return dated[0][1], dated[0][0]


def fetch_filing(cik: str, accession: str, filed: str = "") -> NportFiling:
    """Fetch + parse primary_doc.xml for an accession. Requires HARNESS_SEC_CONTACT (see module doc)."""
    url = _DOC_URL.format(cik_int=int(cik), accession_nodash=accession.replace("-", ""))
    resp = get_with_retry(url, headers=_sec_contact_headers())
    return parse_primary_doc(resp.text, accession=accession, filed=filed)


def parse_primary_doc(xml: str, accession: str = "", filed: str = "") -> NportFiling:
    """Parse the N-PORT XML. Regex over <invstOrSec> blocks — namespace-proof and proven against
    the live filing shape (the 2026-07-10 feasibility probe); a malformed doc fails loud."""

    def tag(block: str, name: str) -> str | None:
        m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
        return m.group(1).strip() if m else None

    series = tag(xml, "seriesName") or "?"
    period = tag(xml, "repPdDate") or "?"
    total = tag(xml, "totAssets")
    blocks = re.findall(r"<invstOrSec>(.*?)</invstOrSec>", xml, re.S)
    if not blocks:
        raise ProviderError(
            f"no <invstOrSec> holdings blocks in accession {accession or '?'} — "
            "not an N-PORT primary_doc, or the schema moved (verify by eyeball on EDGAR)."
        )
    holdings = []
    for b in blocks:
        try:
            holdings.append(
                NportHolding(
                    name=tag(b, "name") or "?",
                    pct=float(tag(b, "pctVal") or 0.0),
                    val_usd=float(tag(b, "valUSD") or 0.0),
                    shares=tag(b, "balance"),
                    cusip=tag(b, "cusip"),
                    country=tag(b, "invCountry"),
                )
            )
        except ValueError:
            continue  # a non-numeric oddity row (derivatives legs etc.) — skip, count below
    holdings.sort(key=lambda h: -h.pct)
    notes = []
    skipped = len(blocks) - len(holdings)
    if skipped:
        notes.append(f"{skipped} non-parseable holding block(s) skipped (derivative legs etc.)")
    return NportFiling(
        series=series,
        period=period,
        accession=accession,
        filed=filed,
        total_assets_usd=float(total) if total else None,
        holdings=holdings,
        notes=notes,
    )


def diff_rosters(
    old: list[tuple[str, float]], new: list[tuple[str, float]], shift_threshold: float = 0.25
) -> NportDiff:
    """Entries / exits / weight shifts (>= threshold pct-points) between two (name, pct) rosters."""
    old_map = dict(old)
    new_map = dict(new)
    entries = [
        f"{n} (new at {p:.2f}%)"
        for n, p in sorted(new_map.items(), key=lambda kv: -kv[1])
        if n not in old_map
    ]
    exits = [
        f"{n} (was {p:.2f}%)"
        for n, p in sorted(old_map.items(), key=lambda kv: -kv[1])
        if n not in new_map
    ]
    shifts = [
        f"{n} {old_map[n]:.2f}% -> {p:.2f}%"
        for n, p in sorted(new_map.items(), key=lambda kv: -(abs(kv[1] - old_map.get(kv[0], kv[1]))))
        if n in old_map and abs(p - old_map[n]) >= shift_threshold
    ]
    return NportDiff(entries=entries, exits=exits, shifts=shifts)


_ROW_RE = re.compile(r'- \{(.*)\}\s*$', re.M)
_KV_RE = re.compile(r'(\w+): (?:"([^"]*)"|([^,}]+))')


def read_reference_rows(yaml_text: str) -> list[dict[str, str]]:
    """Flow-mapping holdings rows from a reference yaml (no pyyaml dependency — same parser family
    as the derive script; keep in sync)."""
    rows = []
    for m in _ROW_RE.finditer(yaml_text):
        rows.append({k: (a if a != "" else b.strip()) for k, a, b in _KV_RE.findall(m.group(1))})
    return rows


def merge_reference_yaml(
    existing_text: str, filing: NportFiling, fetched: str
) -> tuple[str, dict[str, int]]:
    """Regenerate the reference yaml from `filing`, PRESERVING enrichment (sector/us/note) for
    names that persist. Returns (new_text, stats). The header above `meta:` is kept verbatim;
    meta fields are restamped; new names get sector: unknown + a NEW-marker note."""
    enrich = {r.get("name", ""): r for r in read_reference_rows(existing_text)}
    head_m = re.search(r"^(.*?)(?=^meta:)", existing_text, re.S | re.M)
    header = head_m.group(1) if head_m else "# fund holdings — regenerated by `hn finance fund-holdings`\n"
    lines = [header.rstrip("\n"), "meta:"]
    series_line = f"  fund: {filing.series}"
    # preserve an existing richer fund line if present
    fund_m = re.search(r"^  fund: (.+)$", existing_text, re.M)
    if fund_m:
        series_line = f"  fund: {fund_m.group(1)}"
    filer_m = re.search(r"^  filer: (.+)$", existing_text, re.M)
    lines.append(series_line)
    if filer_m:
        lines.append(f"  filer: {filer_m.group(1)}")
    lines += [
        "  form: NPORT-P",
        f'  accession: "{filing.accession}"',
        f'  filed: "{filing.filed}"',
        f'  period: "{filing.period}"   # holdings as-of — the honest staleness stamp',
        f'  fetched: "{fetched}"',
        f"  positions: {len(filing.holdings)}",
        "holdings:  # sorted by weight, descending",
    ]
    stats = {"kept": 0, "new": 0, "gone": 0}
    new_names = set()
    for h in filing.holdings:
        new_names.add(h.name)
        e = enrich.get(h.name)
        base = (
            f'  - {{name: "{h.name}", pct: {h.pct:.2f}, val_usd: {h.val_usd:.0f}, '
            f'country: {h.country or "??"}, cusip: "{h.cusip or ""}", shares: "{h.shares or ""}"'
        )
        if e and e.get("sector"):
            stats["kept"] += 1
            us = e.get("us", "null")
            us_part = f'"{us}"' if us not in ("null", "", None) else "null"
            base += f', sector: {e["sector"]}, us: {us_part}, note: "{e.get("note", "")}"'
        else:
            stats["new"] += 1
            base += ', sector: unknown, us: null, note: "NEW vs prior filing — enrich + research"'
        lines.append(base + "}")
    stats["gone"] = len([n for n in enrich if n and n not in new_names])
    return "\n".join(lines) + "\n", stats
