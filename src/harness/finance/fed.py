"""FOMC decision fetch — the Fed's own monetary-policy RSS + statement HTML (keyless, Fed-direct).

The post-FOMC "what did they actually decide" rail, so the market take is CONFIRMED, not tape-inferred.
Source: federalreserve.gov/feeds/press_monetary.xml (RSS 2.0; the statement item drops at 2pm ET on
decision day) → the statement HTML behind the item link. Keyless, no third party (privacy / least-priv),
and it composes with the existing RSS primitive (stdlib ElementTree, same as news_provider).

Scope (v1, honest): the STATEMENT — decision + policy language + target-rate range + vote. The
SEP/dot-plot is a SEPARATE item (PDF/HTML projection table, no clean API) — we surface its LINK and
flag it as a manual/eyeball input; the hawkish/dovish read of the dots stays the agent's job.

Source note: probed live 2026-06-17 — the default `harness/<version>` UA (set by get_with_retry) is
accepted by federalreserve.gov; the statement body sits in `<div id="article">`, between the vote line
("approved the following statement for release by a N – M vote:") and the "For media inquiries"
boilerplate. Anchors are stable Fed boilerplate; if they move, we fall back to the full stripped text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import FomcDecision

_FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"


def _parse_feed(xml: str) -> list[dict[str, str]]:
    """RSS items → [{title, link, pubDate}], in feed order (newest-first as the Fed publishes)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ProviderError("fed rss: bad XML") from e
    return [
        {
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "pubDate": (item.findtext("pubDate") or "").strip(),
        }
        for item in root.iter("item")
    ]


def parse_statement_html(html: str) -> tuple[str, str, str]:
    """(statement_text, target_rate, vote) from a Fed press-release page. Pure → fixture-testable.

    Strips tags, then slices the committee statement body out of the surrounding page chrome using
    stable Fed boilerplate anchors. Target-rate + vote are regex-pulled from the full text."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)  # decode &#8211; (en-dash), &#39; etc. — real pages mix entities + literals
    text = re.sub(r"\s+", " ", text).strip()

    body = text
    start = re.search(r"approved the following statement for release[^:]*:\s*", text)
    if start:
        body = text[start.end() :]
    end = re.search(r"\bFor media inquiries", body)
    if end:
        body = body[: end.start()].strip()

    target = ""
    m = re.search(
        r"target range for the federal funds rate (?:at|to) ([0-9/\-]+ to [0-9/\-]+ percent)", text
    )
    if m:
        target = m.group(1)

    vote = ""
    v = re.search(r"by a (\d+)\s*[–—-]\s*(\d+)\s+vote", text)
    if v:
        vote = f"{v.group(1)}-{v.group(2)}"

    return body, target, vote


def fetch_fomc_decision() -> FomcDecision:
    """Fetch the most recent FOMC statement (+ the SEP link if a projection meeting) from the Fed RSS."""
    resp = get_with_retry(_FEED_URL)
    if resp.status_code != 200:
        raise ProviderError(f"fed rss: HTTP {resp.status_code}")
    items = _parse_feed(resp.text)

    stmt = next((i for i in items if "fomc statement" in i["title"].lower()), None)
    if stmt is None:
        raise ProviderError("fed rss: no recent 'FOMC statement' item found in the feed")

    released = stmt["pubDate"]
    if released:
        try:
            released = parsedate_to_datetime(released).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            pass  # honest pass-through of the raw pubDate over a silent drop

    sep = next((i for i in items if "economic projections" in i["title"].lower()), None)
    sep_url = sep["link"] if sep else ""

    sresp = get_with_retry(stmt["link"])
    if sresp.status_code != 200:
        raise ProviderError(f"fed statement page: HTTP {sresp.status_code} ({stmt['link']})")
    body, target, vote = parse_statement_html(sresp.text)

    notes: list[str] = []
    if sep_url:
        notes.append(
            f"SEP meeting — economic projections / dot-plot published separately ({sep_url}); it's a "
            "PDF/HTML projection table (no clean API), so eyeball the dots — the hawkish/dovish read "
            "is interpretive."
        )
    if not target:
        notes.append("could not parse the target-rate range — read the statement text directly.")
    if not body:
        notes.append("could not isolate the statement body — the Fed page layout may have changed.")

    return FomcDecision(
        title=stmt["title"],
        released=released,
        statement_url=stmt["link"],
        statement_text=body,
        target_rate=target,
        vote=vote,
        sep_url=sep_url,
        notes=notes,
    )
