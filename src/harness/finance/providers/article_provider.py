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
    method: str = ""  # jsonld | article | paragraphs | dek | none
    words: int = 0
    partial: bool = False
    notes: list[str] = field(default_factory=list)


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
    return u.netloc.endswith("news.google.com") and "/articles/" in u.path


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


def read_article(url: str) -> Article:
    """Fetch + extract one article. Raises ProviderError on a refusal / network failure (named)."""
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
                resp = get_with_retry(target, client=client, headers={"Accept": "text/html"}, retries=2)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                wall = " — a bot wall; the wire's headline + dek is the reachable read" if code in (
                    401, 403) else ""
                raise ProviderError(f"{urlparse(target).netloc} answered HTTP {code}{wall}") from e
    except httpx.HTTPError as e:
        raise ProviderError(f"fetch failed: {e}") from e
    art = extract(resp.text, target)
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
