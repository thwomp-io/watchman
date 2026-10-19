"""SEC filing content reader — fetch + read a filing's documents from the EDGAR Archives
(the earnings-print reader).

The gap this closes: the lane could DETECT a print landing (the news filings rail + the pulse's
filing_drop/print_landed flags) and read STRUCTURED XBRL once the 10-Q follows (fundamentals), but
had no hands to read the print itself — the 8-K's EX-99 press-release exhibit, which carries the
headline numbers, guidance, and KPI tables that drive the post-print repricing, and lands on EDGAR
within minutes of the wire release. This module fetches that content and converts it to
terminal/agent-readable text.

Composition over invention: discovery rides the submissions JSON (news_provider.fetch_filing_refs),
CIK resolution rides the bundled map (cik_resolver via the service), and the Archives fetch follows
the N-PORT module's proven UA posture. The one genuinely-new piece is the stdlib HTML→text
converter (API-over-library rule: no bs4/lxml/html2text dependency for what HTMLParser handles —
press-release HTML is table-heavy but structurally simple).

UA posture (same finding as nport.py): the directory listings + documents live
on www.sec.gov/Archives, which WAF-requires an email-style contact UA (SEC fair-access policy).
Shipping a contact would violate the non-PII default, so the fetch reads ``HARNESS_SEC_CONTACT``
from the environment (the gitignored .env is its home) and fails LOUD with instructions when unset.
Nothing personal ships in this tree.

Honest boundaries (say them, don't paper over): the 8-K exhibit is the print's 80/20 — call
transcripts are NOT SEC documents (third-party ecosystem, no keyless source — a known
follow-up) and IR slide decks are per-company-fragile unless filed as exhibits (when filed, they
appear in `documents` and are fetchable via --doc).
"""

from __future__ import annotations

import re

from harness._http import get_with_retry
from harness.errors import ProviderError

_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/index.json"
_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"

# The AUTHORITATIVE exhibit-type map: the filing's `-index.htm` page — the EDGAR-RENDERED document
# table (Seq | Description | Document | Type | Size), uniform across every filer. Cross-filer
# validation proved both weaker sources insufficient: filenames alone miss real filers (earnings
# releases ship as `erq1fy26.htm` / `a2026q1earningsrelease.htm` — no "ex99" anywhere in the name),
# and the SGML `-index-headers.html` is INCONSISTENT across filers (some carry per-document blocks,
# others only the submission-level header — an empty map for four of the first five filers checked).
_INDEX_HTM_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{accession}-index.htm"
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# Filename backstop (used only when the type map is unavailable/unparseable): vendor spellings
# like "d123456dex991.htm" / "exhibit99-1.htm" carry the exhibit number in the name.
_EX99_RE = re.compile(r"ex[-_]?(?:hibit)?[-_]?99", re.IGNORECASE)

# EDGAR sidecar/meta files that are never the content — excluded from any fallback pick.
_META_RE = re.compile(r"^(\d{10}-\d{2}-\d{6}|R\d+\.htm$|report\.css$|Show\.js$)", re.IGNORECASE)


def _sec_contact_headers() -> dict[str, str]:
    # Resolved via Settings (env var > .env file — pydantic-settings precedence), not bare
    # os.environ: the var's documented home IS the .env file. See config/settings.py.
    from harness.finance.config.settings import get_settings

    contact = get_settings().harness_sec_contact.strip()
    if not contact:
        raise ProviderError(
            "SEC archive fetches require an email-contact User-Agent (SEC fair-access policy; "
            "www.sec.gov WAF-blocks anonymous UAs). Set HARNESS_SEC_CONTACT in the harness .env "
            "(gitignored): HARNESS_SEC_CONTACT=<your contact address>. "
            "The contact is sent ONLY to www.sec.gov, never shipped."
        )
    return {"User-Agent": f"harness-filing ({contact})"}


def list_documents(cik: str, accession: str) -> list[str]:
    """Every filename in a filing's archive directory (the directory-listing index.json).

    The list is the re-aim surface: when the default exhibit pick is wrong, the caller sees what
    else the filing carries (additional EX-99.2 slide exhibits, the 8-K body, XBRL sidecars)."""
    import httpx

    url = _INDEX_URL.format(cik_int=int(cik), accession_nodash=accession.replace("-", ""))
    try:
        resp = get_with_retry(url, headers=_sec_contact_headers())
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        hint = " — no such accession for this filer (check it against --list)" if code == 404 else ""
        raise ProviderError(f"filing index CIK{cik} {accession}: HTTP {code}{hint}") from e
    except httpx.HTTPError as e:
        raise ProviderError(f"filing index CIK{cik} {accession}: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"filing index CIK{cik} {accession}: HTTP {resp.status_code}")
    try:
        items = resp.json().get("directory", {}).get("item", [])
    except ValueError as e:
        raise ProviderError(f"filing index CIK{cik} {accession}: bad JSON") from e
    names = [str(i.get("name", "")) for i in items if i.get("name")]
    if not names:
        raise ProviderError(f"filing index CIK{cik} {accession}: empty directory listing")
    return names


_WRAPPER_RE = re.compile(
    r"(-index-headers\.html|-index\.html?|\.txt|-xbrl\.zip|FilingSummary\.xml|\.xsd|_(cal|def|lab|pre)\.xml)$",
    re.IGNORECASE,
)


def merge_documents(listed: list[str], doc_types: dict[str, str]) -> tuple[list[str], bool]:
    """The filing's document set = the directory listing ∪ the `-index.htm` table.

    SEC's `index.json` listing has been seen TRUNCATED on large filings (a 10-K showing only its
    four wrapper files — the index pages, the full-submission .txt, the XBRL zip — while the rendered
    table listed every document), which made the primary document unreachable and `--doc` reject a
    file that plainly exists. Returns (documents, listing_looked_truncated)."""
    merged = list(dict.fromkeys([*listed, *doc_types]))
    content = [n for n in listed if not _WRAPPER_RE.search(n) and not _META_RE.match(n)]
    return merged, bool(doc_types) and not content


# EDGAR spells some form types with a space; users type them without one.
FORM_ALIASES = {
    "DEF14A": "DEF 14A", "PRE14A": "PRE 14A", "SC13D": "SC 13D", "SC13G": "SC 13G",
    "SC13E3": "SC 13E3", "SC14D9": "SC 14D9", "SCTO-T": "SC TO-T",
}


def normalize_form(form: str) -> str:
    key = form.strip().upper().replace(" ", "")
    return FORM_ALIASES.get(key, form.strip().upper())


_SGML_DOC_RE = re.compile(r"<DOCUMENT>.*?</DOCUMENT>", re.S | re.I)
_BINARY_TYPES = ("GRAPHIC", "ZIP", "EXCEL", "PDF", "XML", "JSON")


def clean_submission_text(raw: str) -> str:
    """A full-submission `.txt` carries NUL bytes and uuencoded/base64 binary exhibits (logos, zips,
    PDFs) inline. NULs make BSD grep call the file binary and match NOTHING; the encoded blocks
    produce false keyword hits. Drop both — the text documents stay whole."""
    raw = raw.replace("\x00", "")

    def keep(m: re.Match[str]) -> str:
        head = m.group(0)[:400].upper()
        t = re.search(r"<TYPE>\s*([^\s<]+)", head)
        if t and t.group(1).startswith(_BINARY_TYPES):
            return f"<DOCUMENT><TYPE>{t.group(1)} [binary exhibit omitted]</DOCUMENT>"
        if "\nBEGIN 644 " in head or "\nBEGIN 666 " in head:
            return "<DOCUMENT>[uuencoded exhibit omitted]</DOCUMENT>"
        return m.group(0)

    return _SGML_DOC_RE.sub(keep, raw)


def parse_index_types(html: str) -> dict[str, str]:
    """Parse the `-index.htm` document table into filename → SEC type ("a2026q1earningsrelease.htm"
    → "EX-99.1"). Row shape: Seq | Description | Document (an <a href>) | Type | Size. The href
    may be an iXBRL viewer link (`/ix?doc=/Archives/…/x.htm`) — the basename after the last slash
    is the filename either way. Unit-testable without network (the fetch wrapper is below)."""
    out: dict[str, str] = {}
    for row in _ROW_RE.findall(html):
        cells = _CELL_RE.findall(row)
        if len(cells) < 4:
            continue
        href = _HREF_RE.search(cells[2])
        if not href:
            continue
        fname = href.group(1).split("/")[-1]
        dtype = _TAG_RE.sub("", cells[3]).replace("&nbsp;", "").strip()
        if fname and dtype:
            out[fname] = dtype
    return out


def fetch_document_types(cik: str, accession: str) -> dict[str, str]:
    """The filing's document-type map, from the EDGAR-rendered `-index.htm` table (see the source
    note above — the uniform surface; SGML headers + filenames both proved unreliable). Degrades
    to an EMPTY map on any fetch/parse trouble — the caller's filename backstop takes over; a
    readout must never die on the metadata when the content itself is reachable."""
    url = _INDEX_HTM_URL.format(
        cik_int=int(cik), accession_nodash=accession.replace("-", ""), accession=accession
    )
    try:
        resp = get_with_retry(url, headers=_sec_contact_headers())
        if resp.status_code != 200:
            return {}
        return parse_index_types(resp.text)
    except Exception:  # noqa: BLE001 — metadata is best-effort by design (see docstring)
        return {}


def pick_press_release(
    names: list[str], primary_doc: str = "", doc_types: dict[str, str] | None = None
) -> tuple[str, str]:
    """Choose the document to read: the type map's EX-99 exhibit first (authoritative), else the
    filename heuristic, else the filing's primary document.

    Returns (name, why). Among EX-99 candidates the lowest-sorting TYPE wins (EX-99.1 = the press
    release files before EX-99.2 slides/supplements). Every fallback is named in the returned
    `why`, so a nonstandard filer degrades readable + honest, never empty."""
    typed_ex99 = sorted(
        ((t, n) for n, t in (doc_types or {}).items() if t.upper().startswith("EX-99")),
    )
    if typed_ex99:
        t, n = typed_ex99[0]
        return n, f"exhibit {t} per the filing's type map — {n} ({len(typed_ex99)} EX-99 doc(s))"
    htm = [n for n in names if n.lower().endswith((".htm", ".html")) and not _META_RE.match(n)]
    ex99 = sorted(n for n in htm if _EX99_RE.search(n))
    if ex99:
        return ex99[0], f"EX-99 exhibit by filename — {ex99[0]} of {len(ex99)} EX-99-named doc(s)"
    if primary_doc and primary_doc in names:
        return primary_doc, "no EX-99 exhibit found — fell back to the filing's primary document"
    if htm:
        return htm[0], "no EX-99 exhibit or primary doc — fell back to the first HTML document"
    raise ProviderError("filing carries no HTML documents to read (use the documents list + --doc)")


def fetch_document(cik: str, accession: str, doc: str) -> tuple[str, str]:
    """Fetch one document from the filing's archive directory. Returns (url, raw_html)."""
    import httpx

    url = _DOC_URL.format(cik_int=int(cik), accession_nodash=accession.replace("-", ""), doc=doc)
    try:
        resp = get_with_retry(url, headers=_sec_contact_headers(), timeout=120.0)
    except httpx.HTTPError as e:
        raise ProviderError(f"filing doc {doc}: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"filing doc {doc}: HTTP {resp.status_code}")
    text = resp.text
    if doc.lower().endswith(".txt"):
        text = clean_submission_text(text)
    return url, text


# `html_to_text` moved to the shared `harness.textutil` (the career lane reads job content
# through the same converter); re-exported here so existing imports keep working.
from harness.textutil import html_to_text  # noqa: E402,F401
