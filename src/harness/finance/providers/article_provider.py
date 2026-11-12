"""Article body reader — `hn finance wire read <url>` (keyless, stdlib extraction).

The wire carries headlines + a dek; a take usually needs the BODY (the repair timeline, the quote,
the number). This fetches one article page and extracts its text, in order of trust:

1. **JSON-LD `articleBody`** — the publisher's own structured copy of the story (most news sites
   ship it for search engines); exact text, no layout guessing.
2. **`<article>` paragraphs** — the `<p>` text inside the page's article element(s).
3. **Page paragraphs** — every substantial `<p>` outside nav/header/footer/aside/form.
4. **The dek only** — `og:description` / `description` when nothing else extracts.

Honest boundaries, printed as notes, never papered over:
- A **paywall** returns what it returns (a dek, a first paragraph) — `isAccessibleForFree: false`
  or a body under ~80 words is labeled partial.
- **Google News links** (`news.google.com/rss/articles/…` — every gnews source-scoped lane:
  AP, Reuters, Bloomberg, Benzinga…) are resolved to the publisher's URL first, through the same
  handshake the Google News page itself runs (the article page carries a signature + timestamp;
  its `batchexecute` endpoint trades them for the target URL). Keyless, the tool UA works. It is
  an undocumented page mechanism: if it changes, the verb refuses by name instead of guessing.
- A **403/401** is a bot wall (AP, Reuters, Utility Dive). A browser User-Agent was tested and
  unlocks nothing there, so the verb keeps the descriptive `harness/<version>` UA and says the
  headline + dek on the wire is the reachable read.

- A **PDF** (a regulator's deck, a filing exhibit) is detected by its content type or its `%PDF` magic,
  written to a fresh private temp directory, and converted with poppler's `pdftotext` (found by path,
  never run from the download's directory). The text keeps its pages, so a quote can be cited by page;
  `grep_article` finds a phrase across line breaks. sec.gov hosts get the SEC fair-access contact UA.

Pure extraction (`extract`) is separate from the fetch, so it is tested on fixtures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from harness._http import USER_AGENT, get_with_retry
from harness.errors import ProviderError

_SKIP_TAGS = {"nav", "header", "footer", "aside", "form", "script", "style", "noscript", "figcaption"}
_MIN_PARA = 40  # characters — shorter <p> are bylines, captions, share buttons
PARTIAL_WORDS = 80


@dataclass
class Article:
    url: str
    final_url: str = ""
    title: str = ""
    site: str = ""
    published: str = ""
    dek: str = ""
    image: str = ""  # og:image / twitter:image — the article's own lead picture
    body: str = ""
    method: str = ""  # jsonld | article | paragraphs | dek | pdf | page-text | none
    words: int = 0
    partial: bool = False
    notes: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)  # a PDF's text, one entry per page (page 1 first)
    mirror: str = ""  # the syndication host the body was read from, when the original was a bot wall
    original: str = ""  # that original URL


class BotWall(ProviderError):
    """A 401/403 from the article's host: a bot wall, not a missing page."""

    def __init__(self, message: str, *, url: str, host: str) -> None:
        super().__init__(message)
        self.url, self.host = url, host


class _Scan(HTMLParser):
    """One pass over the page: meta tags, JSON-LD blocks, <article> paragraphs, page paragraphs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.jsonld: list[str] = []
        self.article_paras: list[str] = []
        self.page_paras: list[str] = []
        self._in_title = False
        self._ld: list[str] | None = None
        self._article_depth = 0
        self._skip_depth = 0
        self._para: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld = []
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "article":
            self._article_depth += 1
        if tag == "p" and not self._skip_depth:
            self._para = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
            return
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
        if tag == "p" and self._para is not None:
            text = re.sub(r"\s+", " ", "".join(self._para)).strip()
            if len(text) >= _MIN_PARA:
                (self.article_paras if self._article_depth else self.page_paras).append(text)
            self._para = None

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
        elif self._in_title:
            self.title += data
        elif self._para is not None:
            self._para.append(data)


def _ld_articles(blocks: list[str]) -> list[dict[str, Any]]:
    """Every Article-typed object in the JSON-LD blocks (top level, @graph, or lists)."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(isinstance(t, str) and t.endswith("Article") for t in types):
                out.append(node)
            for key in ("@graph", "mainEntity", "mainEntityOfPage"):
                if key in node:
                    walk(node[key])

    for raw in blocks:
        try:
            walk(json.loads(raw.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def extract(html: str, url: str) -> Article:
    """Pure: page HTML → Article (see the module docstring for the order of trust)."""
    scan = _Scan()
    try:
        scan.feed(html)
    except Exception:  # noqa: BLE001 — a malformed page degrades to what was scanned
        pass
    art = Article(url=url, final_url=url)
    m = scan.meta
    art.title = unescape(m.get("og:title") or scan.title.strip())
    art.site = m.get("og:site_name") or urlparse(url).netloc
    art.dek = unescape(m.get("og:description") or m.get("description") or "")
    art.published = m.get("article:published_time") or m.get("datepublished") or ""
    image = (m.get("og:image") or m.get("og:image:url") or m.get("twitter:image") or "").strip()
    art.image = image if image.startswith(("http://", "https://")) else ""

    lds = _ld_articles(scan.jsonld)
    free = True
    for ld in lds:
        art.published = art.published or str(ld.get("datePublished") or "")
        if str(ld.get("isAccessibleForFree", "true")).lower() in ("false", "0"):
            free = False
    ld_body = next((str(ld["articleBody"]) for ld in lds if ld.get("articleBody")), "")

    if len(ld_body) > 200:
        art.body, art.method = re.sub(r"[ \t]+", " ", unescape(ld_body)).strip(), "jsonld"
    elif scan.article_paras:
        art.body, art.method = "\n\n".join(scan.article_paras), "article"
    elif scan.page_paras:
        art.body, art.method = "\n\n".join(scan.page_paras), "paragraphs"
    elif art.dek:
        art.body, art.method = art.dek, "dek"
    else:
        art.method = "none"
    art.words = len(art.body.split())
    if not free:
        art.partial = True
        art.notes.append("paywalled (isAccessibleForFree: false) — this is the free portion only")
    elif art.method in ("dek", "none") or art.words < PARTIAL_WORDS:
        art.partial = True
        art.notes.append(f"only {art.words} words extracted — a paywall, a JS-rendered page, or a stub")
    return art


def _is_gnews(url: str) -> bool:
    u = urlparse(url)
    return (u.hostname or "").lower() == "news.google.com" and "/articles/" in u.path


_GN_PAGE = "https://news.google.com/rss/articles/{aid}"
_GN_BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def resolve_gnews(url: str, client: httpx.Client) -> str:
    """A Google News article link → the publisher's URL (see the module docstring)."""
    aid = urlparse(url).path.rstrip("/").split("/")[-1]
    try:
        page = get_with_retry(_GN_PAGE.format(aid=aid), client=client, retries=2)
    except httpx.HTTPError as e:
        raise ProviderError(f"Google News link did not resolve: {e}") from e
    sig = re.search(r'data-n-a-sg="([^"]+)"', page.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
    if not (sig and ts):
        raise ProviderError("Google News link did not resolve (the page carried no redirect "
                            "signature — the mechanism may have changed); read the headline + dek")
    inner = [
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None,
          0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        aid, int(ts.group(1)), sig.group(1),
    ]
    body = {"f.req": json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])}
    try:
        resp = client.post(_GN_BATCH, data=body, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ProviderError(f"Google News link did not resolve: {e}") from e
    m = re.search(r'\[\\"garturlres\\",\\"(.*?)\\"', resp.text)
    if not m:
        raise ProviderError("Google News link did not resolve (no target URL in the reply)")
    return m.group(1)


_MAX_PDF_BYTES = 60 * 1024 * 1024  # a board deck is a few MB; past this, refuse rather than parse


def _is_pdf(content_type: str, data: bytes) -> bool:
    return "application/pdf" in content_type.lower() or data[:5] == b"%PDF-"


def pdf_pages(data: bytes) -> list[str]:
    """A PDF's text per page via poppler's `pdftotext` (reading order). The bytes are untrusted: they go to a
    fresh 0700 temp directory that's removed after, and only the fixed tool reads them."""
    import subprocess
    import tempfile
    from pathlib import Path

    from harness.paths import which_tool

    tool = which_tool("pdftotext")
    if tool is None:
        raise ProviderError("reading a PDF needs poppler's pdftotext (brew install poppler)")
    if len(data) > _MAX_PDF_BYTES:
        mb = len(data) // (1024 * 1024)
        raise ProviderError(f"PDF is {mb} MB; over the {_MAX_PDF_BYTES // (1024 * 1024)} MB cap")
    with tempfile.TemporaryDirectory(prefix="harness-pdf-") as tmp:
        src = Path(tmp) / "document.pdf"
        src.write_bytes(data)
        try:
            run = subprocess.run(  # noqa: S603 — a fixed tool, given a file this function wrote
                [tool, "-enc", "UTF-8", str(src), "-"], capture_output=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError("pdftotext took over 2 minutes") from e
    if run.returncode != 0:
        err = run.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ProviderError(f"pdftotext failed: {err[-1] if err else f'exit {run.returncode}'}")
    pages = run.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()  # pdftotext ends the last page with a form feed too
    return pages


def pdf_title(data: bytes) -> str:
    """The document's own Title metadata via poppler's `pdfinfo`, or '' (a deck's first line is often a
    classification stamp, not its title)."""
    import subprocess
    import tempfile
    from pathlib import Path

    from harness.paths import which_tool

    tool = which_tool("pdfinfo")
    if tool is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="harness-pdf-") as tmp:
        src = Path(tmp) / "document.pdf"
        src.write_bytes(data)
        try:
            run = subprocess.run(  # noqa: S603 — a fixed tool, given a file this function wrote
                [tool, "-enc", "UTF-8", str(src)], capture_output=True, timeout=30, check=False,
            )
        except subprocess.TimeoutExpired:
            return ""
    for line in run.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("Title:"):
            return line[len("Title:"):].strip()
    return ""


def pdf_article(data: bytes, url: str) -> Article:
    """An Article from a PDF: the pages, a body with page markers, and the first line as a title."""
    pages = pdf_pages(data)
    body = "\n\n".join(f"[page {i}]\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    lines = [ln.strip() for p in pages[:2] for ln in p.splitlines() if ln.strip()]
    # the first line of three words or more: a deck's first line is often a stamp ("PUBLIC")
    first = next((ln for ln in lines if len(ln.split()) >= 3), lines[0] if lines else "")
    words = sum(len(p.split()) for p in pages)
    title = (pdf_title(data) or first)[:160]
    art = Article(url=url, title=title, site=urlparse(url).netloc, body=body, method="pdf", words=words,
                  pages=pages)
    if not words:
        art.notes.append("the PDF has no extractable text (a scanned image?); OCR isn't attempted")
    return art


def grep_article(art: Article, needle: str, *, width: int = 160) -> list[tuple[int | None, str]]:
    """Every place `needle` occurs, case-insensitively, as (page or None, a snippet around it). Whitespace is
    collapsed first, so a sentence that wraps across lines in the PDF still matches."""
    want = re.sub(r"\s+", " ", needle).strip().lower()
    if not want:
        return []
    chunks = list(enumerate(art.pages, 1)) if art.pages else [(None, art.body)]
    hits: list[tuple[int | None, str]] = []
    for page, text in chunks:
        flat = re.sub(r"\s+", " ", text)
        low = flat.lower()
        at = low.find(want)
        while at >= 0:
            lo, hi = max(0, at - width // 2), min(len(flat), at + len(want) + width // 2)
            hits.append((page, ("…" if lo else "") + flat[lo:hi].strip() + ("…" if hi < len(flat) else "")))
            at = low.find(want, at + len(want))
    return hits


def _headers_for(host: str) -> dict[str, str]:
    """Accept HTML or a PDF. sec.gov's archives refuse an anonymous UA, so they get the fair-access contact
    when it's configured (the filing reader's own header); without it the refusal names the cause."""
    headers = {"Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"}
    if host == "sec.gov" or host.endswith(".sec.gov"):
        try:
            from harness.finance.filing import _sec_contact_headers

            headers.update(_sec_contact_headers())
        except ProviderError:
            pass
    return headers


def read_article(url: str) -> Article:
    """Fetch + extract one article, or a PDF's text. Raises ProviderError on a refusal / network failure."""
    if not urlparse(url).scheme.startswith("http"):
        raise ProviderError(f"not an http(s) URL: {url}")
    notes: list[str] = []
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            target = url
            if _is_gnews(url):
                target = resolve_gnews(url, client)
                notes.append(f"resolved from a Google News link → {urlparse(target).netloc}")
            try:
                resp = get_with_retry(target, client=client, headers=_headers_for(urlparse(target).netloc),
                                      retries=2)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                wall = " — a bot wall; the wire's headline + dek is the reachable read" if code in (
                    401, 403) else ""
                msg = f"{urlparse(target).netloc} answered HTTP {code}{wall}"
                if code in (401, 403):
                    raise BotWall(msg, url=target, host=urlparse(target).netloc) from e
                raise ProviderError(msg) from e
    except httpx.HTTPError as e:
        raise ProviderError(f"fetch failed: {e}") from e
    if _is_pdf(resp.headers.get("content-type", ""), resp.content[:8]):
        art = pdf_article(resp.content, target)
        art.url, art.final_url = url, str(resp.url)
        art.notes = notes + art.notes
        return art
    art = extract(resp.text, target)
    if art.method == "none":
        # No article structure at all, as in an EDGAR filing (inline XBRL: divs, spans and tables, no <p>):
        # the page's whole text, labeled. A page with a dek keeps the dek; this never replaces a paywall's.
        from harness.textutil import html_to_text

        text = html_to_text(resp.text).strip()
        if text:
            art.body, art.method, art.words = text, "page-text", len(text.split())
            art.partial = False
            art.notes.append("no article structure; this is the page's full text")
    art.url, art.final_url = url, str(resp.url)
    art.notes = notes + art.notes
    return art


def article_card(url: str, *, refresh: bool = False) -> dict[str, str]:
    """The lightweight card the Inbox reader shows for an opened catalyst: the article's lead image,
    outlet name, dek and final URL — one page fetch, cached (a published article's card doesn't
    change). A failure is returned as `error` (and not cached), never raised: the reader just
    shows no picture."""
    from harness.paths import cache_file

    path = cache_file("article-cards.json")
    try:
        cache: dict[str, dict[str, str]] = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    if not refresh and url in cache:
        return cache[url]
    try:
        art = read_article(url)
    except ProviderError as e:
        return {"url": url, "error": str(e)}
    card = {"url": url, "final_url": art.final_url, "image": art.image, "site": art.site,
            "dek": art.dek[:400], "error": ""}
    cache[url] = card
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(list(cache.items())[-1500:])))
    return card


# Wire publishers whose stories are licensed to syndication partners, and where those copies live. A copy
# is used only when its headline matches exactly; nothing here gets past a paywall (a paywalled publisher
# such as the FT syndicates nothing, so it has no entry and stays dek-only). The user overlay's
# finance.wire.mirrors replaces this map.
DEFAULT_MIRRORS: dict[str, list[str]] = {
    "reuters.com": ["yahoo.com", "investing.com", "aol.com"],
    "apnews.com": ["yahoo.com", "aol.com"],
}
_GNEWS_SEARCH = "https://news.google.com/rss/search"


def mirrors_for(host: str) -> list[str]:
    from harness.settings import overlay_get

    raw = overlay_get("finance", "wire", "mirrors", default=None)
    table: dict[str, Any] = raw if isinstance(raw, dict) else DEFAULT_MIRRORS
    host = host.lower().removeprefix("www.")
    for origin, mirrors in table.items():
        if host == origin or host.endswith("." + origin):
            return [str(m) for m in mirrors]
    return []


def _on(host: str, domains: list[str]) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in domains)


def normalize_headline(text: str) -> str:
    """Words only, lowercase: so 'City council approves…' matches across quote styles and dashes."""
    return " ".join(re.findall(r"[a-z0-9]+", unescape(text).lower()))


# The small words a publisher's URL slug leaves out ("…-council-votes-limit-new-towers" for
# "…council votes to limit new towers"). A slug-derived headline matches when the sequences
# agree without them.
_SLUG_DROPS = {"a", "an", "the", "to", "from", "of", "in", "on", "for", "and", "with", "at", "by", "as", "is"}


def headline_matches(candidate: str, want: str, *, from_slug: bool) -> bool:
    """Exact headline match: word for word, or, for a headline read off a URL slug, word for word once the
    slug's dropped small words are set aside on both sides."""
    a, b = normalize_headline(candidate).split(), normalize_headline(want).split()
    if not from_slug:
        return bool(b) and a == b
    a, b = [w for w in a if w not in _SLUG_DROPS], [w for w in b if w not in _SLUG_DROPS]
    return len(b) >= 4 and a == b


def headline_from_url(url: str) -> str:
    """A publisher's slug as words ('…/city-council-approves-…-2026-01-15/' -> the headline), without
    the trailing date or id. Empty when the path has no slug."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    slug = max(parts, key=len) if parts else ""
    words = slug.split("-")
    while words and (words[-1].isdigit() or re.fullmatch(r"[0-9a-f]{6,}", words[-1])):
        words.pop()
    return " ".join(words) if len(words) >= 4 else ""


def _strip_source(title: str, source: str) -> str:
    """Google News appends ' - <Source>' to every title."""
    suffix = f" - {source}"
    return title[: -len(suffix)] if source and title.endswith(suffix) else title


def find_mirror(headline: str, mirrors: list[str], client: httpx.Client, *,
                from_slug: bool = False) -> tuple[str, str] | None:
    """(Google News link, mirror host) for a copy of `headline` on one of `mirrors`, exact headline only.
    The search engine treats quotes loosely, so the match is checked here, word for word."""
    if not normalize_headline(headline):
        return None
    sites = " OR ".join(f"site:{m}" for m in mirrors)
    # Quoted first; unquoted when that finds nothing (a slug's words aren't a phrase anywhere). The search is
    # only a candidate list either way: the headline check below is what decides.
    items: list[str] = []
    for q in (f'"{headline}" ({sites})', f"{headline} ({sites})"):
        try:
            params: dict[str, str | int] = {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}
            resp = get_with_retry(_GNEWS_SEARCH, client=client, retries=1, params=params)
        except httpx.HTTPError:
            continue
        items = re.findall(r"<item>(.*?)</item>", resp.text, re.S)
        if items:
            break
    for item in items:
        t = re.search(r"<title>(.*?)</title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        src = re.search(r'<source url="([^"]*)">(.*?)</source>', item, re.S)
        if not (t and link and src):
            continue
        host = urlparse(unescape(src.group(1))).netloc
        title = _strip_source(unescape(t.group(1)).strip(), unescape(src.group(2)).strip())
        if _on(host, mirrors) and headline_matches(title, headline, from_slug=from_slug):
            return unescape(link.group(1)).strip(), host
    return None


def read_with_mirror(url: str, headline: str = "") -> Article:
    """`read_article`, and on a bot wall from a publisher that syndicates, the same story from a licensed
    copy found by its exact headline (the wire's title, else the URL's slug). The result names both."""
    try:
        return read_article(url)
    except BotWall as wall:
        mirrors = mirrors_for(wall.host)
        if not mirrors:
            raise
        from_slug = not headline
        head = _strip_source(headline, headline.rsplit(" - ", 1)[-1]) if " - " in headline else headline
        head = head or headline_from_url(wall.url)
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            found = find_mirror(head, mirrors, client, from_slug=from_slug) if head else None
        if found is None:
            raise ProviderError(f"{wall}; no syndicated copy with that exact headline on "
                                f"{', '.join(mirrors)}") from wall
        art = read_article(found[0])
        shown = art.title.split(" - ")[0].split(" | ")[0]  # a site's own suffix, as in "… | Reuters"
        if art.title and not headline_matches(shown, head, from_slug=from_slug):
            raise ProviderError(f"{wall}; the copy found on {found[1]} has a different headline "
                                f"({art.title[:80]!r}), so it isn't used") from wall
        art.mirror, art.original = urlparse(art.final_url or found[0]).netloc or found[1], wall.url
        art.notes.insert(0, f"{wall.host} refused (a bot wall); read from the syndicated copy on "
                            f"{art.mirror}")
        return art
