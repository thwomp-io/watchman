"""Keyless news provider — Yahoo Finance per-ticker RSS (wire headlines) + SEC EDGAR recent
filings (data.sec.gov submissions JSON, the primary-source "something material happened" rail).

Probe-validated 2026-06-09. Source notes:
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
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import Filing, NewsItem

_YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"

# The rail's job is "did something MATERIAL happen" — insider forms (4/144/5) fire constantly and
# drown the signal. Material set: events + the real prints + registrations + the proxy.
_MATERIAL_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "S-1", "S-3", "DEF 14A", "20-F", "6-K"}

# <content:encoded> lives in the RSS 1.0 content module namespace; ElementTree addresses it as {ns}tag.
_CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _extract_summary(item: ET.Element) -> str:
    """The RSS item's body text for the reader pane. Prefer <content:encoded> (the
    fuller article body some feeds carry — e.g. Al Jazeera) over <description> (the short
    summary). Raw as-published — may contain HTML (capture generously; the consumer sanitizes/renders).
    Returns "" when neither is present (most market feeds are headline-only — degrade gracefully)."""
    return (item.findtext(_CONTENT_ENCODED) or item.findtext("description") or "").strip()


def fetch_yahoo_headlines(symbol: str, limit: int = 5) -> list[NewsItem]:
    """Per-ticker wire headlines from Yahoo Finance RSS (keyless)."""
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
        out.append(
            NewsItem(
                symbol=symbol,
                title=title,
                url=link,
                source=urlparse(link).netloc or "yahoo",
                published=published,
                summary=_extract_summary(item),
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
    agent must never die on a single source — learned live when MarketWatch 301'd)."""
    import httpx as _httpx

    try:
        resp = get_with_retry(url)
    except _httpx.HTTPError as e:
        raise ProviderError(f"feed {source}: {e}") from e
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise ProviderError(f"feed {source}: unparseable XML: {e}") from e
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = item.findtext("pubDate") or ""
        try:
            pub = parsedate_to_datetime(pub).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        items.append(NewsItem(
            symbol=symbol, title=title, url=(item.findtext("link") or "").strip(),
            source=source, published=pub, summary=_extract_summary(item),
        ))
        if len(items) >= limit:
            break
    return items


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
