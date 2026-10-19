"""Keyless news provider — Yahoo Finance per-ticker RSS (wire headlines) + SEC EDGAR recent
filings (data.sec.gov submissions JSON, the primary-source "something material happened" rail).

Probe-validated. Source notes:
- Yahoo RSS is a broker-console-style wire feed; parsed with stdlib ElementTree (no feedparser
  dependency — the API-over-library rule; RSS 2.0 is trivially walkable).
- EDGAR's browse-edgar Atom feed is WAF-blocked for undeclared tools, and SEC's declared-UA policy
  (contact info) conflicts with the non-PII UA posture — so filings come from the data.sec.gov
  submissions endpoint instead, the same open host the fundamentals provider already uses.
- Google News RSS probed live and held as a documented fallback seam (breadth / no-ticker cases);
  not built until a real need lands.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import Filing, FilingRef, NewsItem
from harness.finance.scan import paced

_YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"

# The rail's job is "did something MATERIAL happen" — insider forms (4/144/5) fire constantly and
# drown the signal. Material set: events + the real prints + registrations + the proxy.
_MATERIAL_FORMS = {
    "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT", "S-1", "S-3", "DEF 14A", "20-F", "6-K",
    # merger / going-private proxies — the richest document for a name with a pending deal
    "DEFM14A", "PREM14A", "DEFM14C", "SC 13E3", "S-4",
}

# <content:encoded> lives in the RSS 1.0 content module namespace; ElementTree addresses it as {ns}tag.
_CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"


_MEDIA = "{http://search.yahoo.com/mrss/}"
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


def item_meta(item: ET.Element, title: str) -> dict[str, Any]:
    """The extra fields an RSS item ships (sampled live across the roster — see docs/WIRE-FEEDS.md):
    image (media:content medium=image or an image type, media:thumbnail, an image <enclosure>),
    author (dc:creator / <author>), the item's <category> tags, and Google News's <source> — the real
    outlet behind a news.google.com link. Returns the fields plus the title with Google's trailing
    " - Outlet" removed when the outlet is known (the reader shows the outlet on its own line)."""
    image = ""
    for el in item.iter():
        tag = el.tag
        if tag in (f"{_MEDIA}content", f"{_MEDIA}thumbnail"):
            url = (el.get("url") or "").strip()
            medium, mtype = (el.get("medium") or ""), (el.get("type") or "")
            if url and (tag.endswith("thumbnail") or medium == "image" or mtype.startswith("image")
                        or (not medium and not mtype)):
                image = url
                break
        if tag == "enclosure" and (el.get("type") or "").startswith("image") and el.get("url"):
            image = (el.get("url") or "").strip()
            break
    author = (item.findtext(_DC_CREATOR) or item.findtext("author") or "").strip()
    tags = [t.strip() for t in (c.text or "" for c in item.findall("category")) if t.strip()]
    src = item.find("source")
    publisher = (src.text or "").strip() if src is not None else ""
    publisher_url = (src.get("url") or "").strip() if src is not None else ""
    clean = title
    if publisher and title.endswith(f" - {publisher}"):
        clean = title[: -len(publisher) - 3].rstrip()
    return {"image_url": image, "author": author, "tags": tags, "publisher": publisher,
            "publisher_url": publisher_url, "title": clean}


def _same_text(html: str, title: str) -> bool:
    """A description that only repeats the headline (Google News wraps the title in a link + the
    outlet) — not a summary worth a reader pane."""
    import re as _re
    from html import unescape

    def norm(s: str) -> str:
        return _re.sub(r"[^a-z0-9]+", "", s.lower())

    text = unescape(_re.sub(r"<[^>]+>", " ", html))
    return bool(norm(title)) and norm(text).startswith(norm(title))


def _extract_summary(item: ET.Element) -> str:
    """The RSS item's body text for the reader pane. Prefer <content:encoded> (the
    fuller article body some feeds carry) over <description> (the short
    summary). Raw as-published — may contain HTML (capture generously; the consumer sanitizes/renders).
    Returns "" when neither is present (most market feeds are headline-only — degrade gracefully)."""
    return (item.findtext(_CONTENT_ENCODED) or item.findtext("description") or "").strip()


def fetch_yahoo_headlines(symbol: str, limit: int = 5) -> list[NewsItem]:
    """Per-ticker wire headlines from Yahoo Finance RSS (keyless)."""
    with paced(_YAHOO_RSS_URL):
        resp = get_with_retry(
            _YAHOO_RSS_URL, params={"s": symbol, "region": "US", "lang": "en-US"}
        )
    if resp.status_code != 200:
        raise ProviderError(f"yahoo rss {symbol}: HTTP {resp.status_code}")
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise ProviderError(f"yahoo rss {symbol}: bad XML") from e
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title:
            continue
        pub = (item.findtext("pubDate") or "").strip()
        published = ""
        if pub:
            try:
                published = parsedate_to_datetime(pub).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                published = pub  # honest pass-through over a silent drop
        meta = item_meta(item, title)
        out.append(
            NewsItem(
                symbol=symbol,
                url=link,
                source=urlparse(link).netloc or "yahoo",
                published=published,
                summary=_extract_summary(item),
                **meta,
            )
        )
        if len(out) >= limit:
            break
    return out


def _submissions_recent(cik: str, *, label: str) -> dict[str, Any]:
    """Fetch + unwrap `filings.recent` from the data.sec.gov submissions JSON.

    Shared transport/parse guard for BOTH filing surfaces (news rail + filing_drop feed) — one
    host, one error contract (everything wraps to ProviderError; a standing agent must never die
    on a single source)."""
    import httpx as _httpx

    try:
        with paced(_SUBMISSIONS_URL.format(cik=cik)):
            resp = get_with_retry(_SUBMISSIONS_URL.format(cik=cik))
    except _httpx.HTTPError as e:
        raise ProviderError(f"{label}: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(f"{label}: HTTP {resp.status_code}")
    try:
        recent = resp.json().get("filings", {}).get("recent", {})
    except json.JSONDecodeError as e:
        raise ProviderError(f"{label}: bad JSON") from e
    if not isinstance(recent, dict):
        raise ProviderError(f"{label}: unexpected submissions shape")
    return recent


def fetch_filing_refs(cik: str, form: str = "8-K", limit: int = 8) -> list[FilingRef]:
    """Recent filing REFS (accession-bearing) for one form family — the filing-reader's discovery
    step. Same submissions JSON as the news rail; the difference is the payload:
    the rail shows display rows, this returns the accession numbers an Archives fetch is keyed on.

    `form` matches the family: "8-K" also matches "8-K/A" (amendments carry the corrected print —
    excluding them would silently read the superseded numbers)."""
    recent = _submissions_recent(cik, label=f"edgar filing refs CIK{cik}")
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    out: list[FilingRef] = []
    for f, date, accession, doc in zip(forms, dates, accessions, docs, strict=False):
        if not (f == form or f.startswith(f"{form}/")):
            continue
        if not accession:
            continue
        out.append(FilingRef(form=f, filed=date, accession=accession, primary_doc=doc or ""))
        if len(out) >= limit:
            break
    return out


def fetch_recent_filings(cik: str, limit: int = 5) -> list[Filing]:
    """Most-recent SEC filings for a (10-digit zero-padded) CIK via the submissions JSON."""
    recent = _submissions_recent(cik, label=f"edgar submissions CIK{cik}")
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cik_int = str(int(cik))
    out: list[Filing] = []
    for form, date, accession, doc in zip(forms, dates, accessions, docs, strict=False):
        if form not in _MATERIAL_FORMS:
            continue  # insider/noise forms (4, 144, 5, 3, SC 13G…) — material rail only
        url = ""
        if accession:
            url = _FILING_URL.format(cik_int=cik_int, accession=accession.replace("-", ""), doc=doc or "")
        out.append(Filing(form=form, filed=date, url=url))
        if len(out) >= limit:
            break
    return out


def fetch_rss(url: str, source: str, *, limit: int = 5, symbol: str = "") -> list[NewsItem]:
    """Generic RSS 2.0 fetch (the feeds.yaml layer) — same stdlib path as Yahoo.

    Wraps transport errors into ProviderError so ONE dead feed degrades gracefully (a standing
    agent must never die on a single source)."""
    import httpx as _httpx

    try:
        with paced(url):
            resp = get_with_retry(url)
    except _httpx.HTTPError as e:
        raise ProviderError(f"feed {source}: {e}") from e
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise ProviderError(f"feed {source}: unparseable XML: {e}") from e
    # Parse EVERY item, sort by publication time, THEN cap. Most feeds are not newest-first in
    # document order — Google News searches are relevance-ordered and the big wires interleave
    # updated stories — so "the first N items" routinely dropped most of the N newest (measured
    # across the roster: 23 of 33 feeds out of order, up to 8 of the newest 8 lost per run), and a
    # story could be missing from one sweep and present in a later one once the feed reordered.
    dated: list[tuple[datetime | None, int, NewsItem]] = []
    for pos, item in enumerate(root.iter("item")):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        raw_pub = item.findtext("pubDate") or ""
        when: datetime | None = None
        pub = raw_pub
        try:
            when = parsedate_to_datetime(raw_pub)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            pub = when.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        meta = item_meta(item, title)
        summary = _extract_summary(item)
        if meta["publisher"] and _same_text(summary, meta["title"]):
            summary = ""  # Google News's description is the headline again, linked
        dated.append((when, pos, NewsItem(
            symbol=symbol, url=(item.findtext("link") or "").strip(),
            source=source, published=pub, summary=summary, **meta,
        )))
    # newest first; undated items after every dated one; document order breaks ties
    dated.sort(key=lambda t: (t[0] is None, -(t[0].timestamp() if t[0] else 0.0), t[1]))
    return [it for _, _, it in dated[:limit]]


def audit_feed(url: str, source: str, *, sample: int = 8) -> dict[str, Any]:
    """What one feed's newest items actually carry — the counts behind docs/WIRE-FEEDS.md: of the
    first `sample` items, how many ship an image, an author, category tags, a Google News outlet
    (<source>), and a real summary. A dead feed reports its error instead of raising."""
    import httpx as _httpx

    row: dict[str, Any] = {"feed": source, "items": 0, "image": 0, "author": 0, "tags": 0,
                           "publisher": 0, "summary": 0, "error": ""}
    try:
        with paced(url):
            resp = get_with_retry(url)
        root = ET.fromstring(resp.text)
    except (_httpx.HTTPError, ET.ParseError, ProviderError) as e:
        row["error"] = str(e)[:120]
        return row
    for item in list(root.iter("item"))[:sample]:
        title = (item.findtext("title") or "").strip()
        meta = item_meta(item, title)
        summary = _extract_summary(item)
        row["items"] += 1
        row["image"] += bool(meta["image_url"])
        row["author"] += bool(meta["author"])
        row["tags"] += bool(meta["tags"])
        row["publisher"] += bool(meta["publisher"])
        row["summary"] += bool(summary) and not (meta["publisher"] and _same_text(summary, meta["title"]))
    return row


_GNEWS_SEARCH_URL = "https://news.google.com/rss/search"


def fetch_gnews_ticker(symbol: str, *, limit: int = 5) -> list[NewsItem]:
    """Per-ticker single-name catalysts via Google News search (keyless) — the breadth layer
    BEYOND Yahoo's per-ticker RSS. Realizes the gnews fallback seam this module's header documented;
    built to catch single-name catalysts a plain per-ticker feed misses — Yahoo's single per-ticker
    feed lags catalysts that other publishers break first.

    Query `<SYMBOL> stock` scopes to the equity + disambiguates a bare ticker. Google News titles
    carry the publisher inline ("Headline - Benzinga"), so the real source rides for free through the
    generic RSS path. Minor cross-listing noise (e.g. RY.TO) is expected and degrades via the
    seen-cache + the agent relevance-filter, same as the whole-site feeds.yaml feeds. Source label is
    `gnews:<SYMBOL>` so the rail still groups by ticker."""
    url = (
        f"{_GNEWS_SEARCH_URL}?q={quote_plus(symbol + ' stock')}"
        "&hl=en-US&gl=US&ceid=US:en"
    )
    return fetch_rss(url, f"gnews:{symbol}", limit=limit, symbol=symbol)


def fetch_edgar_filing_feed(cik: str, symbol: str, *, limit: int = 3) -> list[NewsItem]:
    """Recent 8-K entries per filer — the filing_drop substrate (data.sec.gov submissions JSON).

    An 8-K is the company speaking on the record; fresh entries here beat any wire.

    Replaced the www.sec.gov browse-edgar Atom feed — which WAF-403s
    undeclared tools, the EXACT trap this module's header already documented — with the same
    data.sec.gov submissions endpoint the rest of the lane uses. One host, no UA-policy conflict;
    the 8-K `items` codes (e.g. 2.02 results-of-operations) ride the same response for free."""
    recent = _submissions_recent(cik, label=f"EDGAR filings {symbol}")
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    accepted = recent.get("acceptanceDateTime", [])
    item_codes = recent.get("items", [])
    cik_int = str(int(cik))
    out: list[NewsItem] = []
    for i, form in enumerate(forms):
        if form not in {"8-K", "8-K/A"}:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        url = ""
        if accession:
            doc = (docs[i] if i < len(docs) else "") or ""
            url = _FILING_URL.format(cik_int=cik_int, accession=accession.replace("-", ""), doc=doc)
        # acceptanceDateTime is the precise timestamp; filingDate is the date-only fallback
        when = (accepted[i] if i < len(accepted) else "") or (dates[i] if i < len(dates) else "")
        codes = item_codes[i] if i < len(item_codes) else ""
        title = f"{form} filed" + (f" (items {codes})" if codes else "")
        out.append(NewsItem(
            symbol=symbol, title=title, url=url,
            source="sec.gov", published=when[:16].replace("T", " "),
        ))
        if len(out) >= limit:
            break
    return out
