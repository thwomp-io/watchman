"""levels.fyi — the SANCTIONED agent surface (keyless markdown routes), read-only.

`https://www.levels.fyi/llms.txt` invites agents to the `.md` twins of the salary routes
("structured, bot-friendly data with proper attribution and licensing"). What this module knows
about that surface, all verified live 2026-09:

- **A company ladder** lives at `/companies/{slug}/salaries/{family}/locations/{geo}.md`. The
  page carries a header block (`**Scope:**` / `**Location:**` / `**Currency:**`), an aggregate
  median, a `### Levels Breakdown` table (level code → median TC) and a FAQ whose sentences hold
  the per-level YOE, the median base/stock/bonus mix, the highest package and — when the site
  states it — a title for a level ("A Senior Software Engineer at X is level IC5"). Everything the
  parser reads is on that page; nothing is inferred.
- **Pin the geography.** The unpinned `/salaries/{family}.md` localizes on the requester's egress
  (the India ladder in INR came back for a US caller once) — every fetch here goes through the
  `locations/{geo}` form and the caller checks the `Currency` line. Never print a rupee ladder as
  a US metro.
- **An empty 200 is a real failure mode — and it is a User-Agent gate, not pacing.** On a CDN
  cache MISS the origin renders the `.md` page only for browser-shaped User-Agents; a tool UA
  (`harness/…`, `curl/…`, any other string tried) gets `200`,
  `cache-control: no-store`, zero bytes, while a cached copy keeps serving to anyone. So a page
  nobody has opened in a browser recently reads as empty here. The tool keeps the descriptive UA
  (the good-citizen posture) and reports the empty page by name; whether the harness may present
  a browser UA to this route is an operator opt-in, not a default.
- **The operator opt-in: an overlay setting sends a browser UA HERE ONLY.** A probe showed the
  gate is per-request UA classification (a browser fetch does not warm the edge for a tool UA;
  any extra token in the UA fails; identity in separate headers passes). So when the tracker
  overlay carries `career.levels.user_agent: browser`, the `.md` page fetch sends the minimal
  recognized-browser UA plus honest identity headers — `From:` (the overlay's
  `career.applicant.email`) and `X-Harness-Contact:` (`career.levels.contact`) — and
  announces it once per run. Default absent = the descriptive `harness/<version>` UA, as
  everywhere. The setting lives in the operator's private config, never in code; the sitemaps
  keep the descriptive UA (they serve it fine).
- **Level pages exist but are not in the sitemap** (`…/levels/{level}.md`), and they slice
  differently from the country page (P70 read $325.8k on one and $681.8k on the other). The
  country-pinned page is the one this lane trusts; the per-level page is not fetched.
- **The sitemaps are the slug authority** (`/sitemap.xml` → companies ×80 / job-family ×20 /
  industry ×3 / base): the family + sub-path forms are NOT guessable (`/t/site-reliability-
  engineer.md` 404s). Children are ~26-32 MB each and served uncompressed; stream them.
- **Pacing + attribution + no redistribution** are the license: one page per call, a min-interval
  throttle, the `Data source: Levels.fyi (https://www.levels.fyi)` line on every render, and the
  cache stays in the vault. `robots.txt` disallows training crawlers site-wide; the `.md` routes
  and the sitemaps are what `llms.txt` sanctions. The Data License page publishes no further
  public-endpoint terms.
- The keyed REST/MCP/CLI API (`/openapi.json`, ApiKeyAuth, "contact sales") is Tier-1 and
  deliberately NOT built against.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx

from harness._http import USER_AGENT, describe_http_error, get_with_retry
from harness.errors import ProviderError
from harness.settings import overlay_get

BASE = "https://www.levels.fyi"
ATTRIBUTION = "Data source: Levels.fyi (https://www.levels.fyi)"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"

# Self-pacing (the burst-prone-fan-out rule): a process never fires two levels.fyi requests closer
# than this. The empty-200 pacing response is the failure shape this guards against.
MIN_INTERVAL_S = 2.0
_last_call = 0.0


def _pace() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


# The minimal recognized-browser UA (verified live 2026-09: passes; a bare "Mozilla/5.0" or any
# appended tool token fails). Sent ONLY under the overlay opt-in, ONLY on the .md page fetch.
BROWSER_UA = "Mozilla/5.0 AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
_announced = False


def request_headers() -> dict[str, str]:
    """Headers for a levels.fyi page fetch. Default: the descriptive harness UA (supplied by
    `get_with_retry`). With `career.levels.user_agent: browser` in the overlay: the minimal
    browser UA + `From:` + `X-Harness-Contact:` (the operator opt-in, announced once per run)."""
    global _announced
    headers = {"Accept": "text/markdown, text/plain;q=0.9, */*;q=0.1"}
    if str(overlay_get("career", "levels", "user_agent", default="")).strip().lower() != "browser":
        return headers
    headers["User-Agent"] = BROWSER_UA
    email = overlay_get("career", "applicant", "email")
    contact = overlay_get("career", "levels", "contact")
    if email:
        headers["From"] = str(email)
    if contact:
        headers["X-Harness-Contact"] = str(contact)
    if not _announced:
        print("levels.fyi: browser UA per overlay (career.levels.user_agent)", file=sys.stderr)
        _announced = True
    return headers


def ladder_url(slug: str, family: str, location: str) -> str:
    """The geo-pinned company ladder route (the only ladder form this lane fetches)."""
    return f"{BASE}/companies/{slug}/salaries/{family}/locations/{location}.md"


def fetch_levels_page(url: str) -> str:
    """One markdown page, paced. 404 → a named ProviderError (the slug is wrong, or the family /
    geo form does not exist for this company); an empty or HTML body → a named ProviderError."""
    _pace()
    try:
        resp = get_with_retry(url, headers=request_headers(), allow_status={404})
    except httpx.HTTPError as e:
        raise ProviderError(f"levels.fyi {url}: {describe_http_error(e)}") from e
    if resp.status_code == 404:
        raise ProviderError(
            f"levels.fyi has no page at {url} (HTTP 404) — check the slug with "
            "`hn career slugs <name>`, and the family/location forms with `hn career families`"
        )
    text = resp.text
    if not text.strip():
        raise ProviderError(
            f"levels.fyi returned an empty page for {url} — on a CDN cache miss the origin renders "
            "the .md page only for browser User-Agents (a tool UA gets a bare 200); cached copies "
            "still serve, so a page recently opened in a browser will read here. Nothing cached"
        )
    if text.lstrip().startswith("<"):
        raise ProviderError(f"levels.fyi served HTML, not markdown, at {url} — no .md twin for this route")
    return text


# ————— the ladder page parser —————

_MONEY = re.compile(r"\$?([\d][\d,]*(?:\.\d+)?)\s*([KMkm])?")


def parse_money(s: str) -> int | None:
    """'$205,597' / '$205.6K' / '$1.3M' → whole currency units; None when no figure."""
    m = _MONEY.search(s)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").upper()
    if unit == "K":
        n *= 1_000
    elif unit == "M":
        n *= 1_000_000
    return int(round(n))


_HEADER = re.compile(r"^\*\*(URL|Generated|Scope|Location|Currency):\*\*\s*(.+?)\s*$", re.M)
_AGG = re.compile(r"^- ([A-Za-z][A-Za-z ]+?):\s*(.+?)\s*$", re.M)
_LEVEL_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(\$[\d,\.]+[KMkm]?)\s*\|\s*$", re.M)
_YOE = re.compile(
    r"How many years of experience does an? .+? (\S+) have\?\s*\n"
    r".*?typically have ([\d]+(?:[–-][\d]+)?\+?) years",
    re.M,
)
# The pay-mix sentence drops the components a company has no data for ("…is $200,000 base
# salary, $170,000 in stock per year." — no bonus clause, seen live 2026-09-09), so
# base is the only required capture; stock + bonus are optional.
_MIX = re.compile(
    r"is \$([\d,]+) base salary(?:,? (?:and )?\$([\d,]+) in stock per year)?"
    r"(?:,? (?:and )?\$([\d,]+) bonus)?"
)
_HIGH = re.compile(r"sits at a yearly total compensation of \$([\d,]+)")
_TITLE = re.compile(r"What level is an? (.+?) at .+?\?\s*\n.*? is level (\S+?)[,.]", re.M)


def parse_ladder_md(text: str) -> dict[str, Any]:
    """Structure the `.md` ladder page. Returns the raw dict the comp model validates: header
    fields, aggregates, the level rows (code → median TC), per-level YOE + site-stated titles,
    the pay mix, the highest package. A page with no level rows is a THIN page (aggregate only) —
    the caller says "no per-level data"; this never invents rows."""
    out: dict[str, Any] = {"header": {}, "aggregates": {}, "levels": [], "yoe": {}, "titles": {}}
    for k, v in _HEADER.findall(text):
        out["header"][k.lower()] = v.strip()
    m = re.search(r"^# (.+)$", text, re.M)
    out["title"] = m.group(1).strip() if m else ""
    for label, val in _AGG.findall(text):
        money = parse_money(val) if "$" in val else None
        out["aggregates"][label.strip()] = money if money is not None else val.strip()
    # Level rows sit only under the Levels Breakdown heading; the FAQ tables (none today) would
    # otherwise be swept in — scope the regex to that section.
    section = text.split("### Levels Breakdown", 1)
    if len(section) == 2:
        body = section[1].split("\n---", 1)[0]
        for level, money in _LEVEL_ROW.findall(body):
            if level.lower() == "level":
                continue
            out["levels"].append({"level": level, "median_tc": parse_money(money)})
    for level, yoe in _YOE.findall(text):
        out["yoe"][level] = yoe.replace("-", "–")
    for title, level in _TITLE.findall(text):
        out["titles"][level] = title.strip()
    mm = _MIX.search(text)
    if mm:
        out["mix"] = {
            k: int(g.replace(",", ""))
            for k, g in zip(("base", "stock", "bonus"), mm.groups(), strict=True) if g
        }
    hm = _HIGH.search(text)
    out["high_tc"] = int(hm.group(1).replace(",", "")) if hm else None
    return out


def currency_code(field: str) -> str:
    """'USD ($)' → 'USD'; the header's currency line, first token."""
    return field.split()[0].strip().upper() if field.strip() else ""


# ————— sitemaps (streamed) —————

_LOC = re.compile(r"<loc>(.*?)</loc>(?:\s*<lastmod>(.*?)</lastmod>)?", re.S)


def iter_sitemap_locs(url: str, *, chunk_size: int = 1 << 16) -> Iterator[tuple[str, str]]:
    """Yield (loc, lastmod-or-'') from one sitemap file WITHOUT loading it whole — the children are
    ~30 MB each (one `<url>` per company × 30 hreflang alternates), served uncompressed. A carry
    buffer keeps a tag split across chunk boundaries intact. Paced like every other fetch."""
    _pace()
    buf = ""
    try:
        with httpx.stream(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True
        ) as resp:
            if resp.status_code >= 400:
                raise ProviderError(f"levels.fyi sitemap {url}: HTTP {resp.status_code}")
            for chunk in resp.iter_text(chunk_size=chunk_size):
                buf += chunk
                last = 0
                for m in _LOC.finditer(buf):
                    yield m.group(1).strip(), (m.group(2) or "").strip()
                    last = m.end()
                # keep the unconsumed tail (a partial <loc> or its <lastmod>) for the next chunk
                buf = buf[last:] if last else buf[-2048:] if len(buf) > 1 << 20 else buf
    except httpx.HTTPError as e:
        raise ProviderError(f"levels.fyi sitemap {url}: {describe_http_error(e)}") from e
    for m in _LOC.finditer(buf):
        yield m.group(1).strip(), (m.group(2) or "").strip()


# ————— the leaderboard (HTML — no .md twin) —————
# Grammar from the base sitemap: /leaderboard/{Family}/{Level}/{city|region|country}/{Geo}/ with the
# levels Entry-Level-Engineer / Software-Engineer / Senior-Engineer / Staff-Engineer /
# Principal-Engineer. Verified live 2026-09-09: this route serves the DESCRIPTIVE UA even on a CDN miss (no
# UA gate here — the browser-UA opt-in stays scoped to the .md ladders); one page = the top-20 rows;
# each row carries rank · company slug (`/company/{slug}/salaries/`) · the site's level label ·
# total comp + base/stock/bonus as hidden inputs. The page states its own cut: `<h1>Top Paying
# Companies in {Geo}</h1>`, the selected level `<option>`, and a canonical link — the pin check.


def leaderboard_url(family: str, level: str, kind: str, geo: str) -> str:
    return f"{BASE}/leaderboard/{family}/{level}/{kind}/{geo}/"


def fetch_levels_html(url: str) -> str:
    """One HTML page, paced, descriptive UA (this route needs no browser UA)."""
    _pace()
    try:
        resp = get_with_retry(url, allow_status={404})
    except httpx.HTTPError as e:
        raise ProviderError(f"levels.fyi {url}: {describe_http_error(e)}") from e
    if resp.status_code == 404:
        raise ProviderError(f"levels.fyi has no leaderboard at {url} (HTTP 404) — check the cut's forms "
                            "against `hn career levels-index` (leaderboard families / levels / geos)")
    if not resp.text.strip():
        raise ProviderError(f"levels.fyi returned an empty page for {url}")
    return resp.text


_LB_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_LB_RANK = re.compile(r"<td>\s*<strong>(\d+)</strong>")
_LB_SLUG = re.compile(r'href="/company/([^/"]+)/salaries/')
_LB_NAME = re.compile(r'alt="([^"]*)"')
_LB_LABEL = re.compile(r'<td class="d-none d-sm-table-cell">\s*<a[^>]*>([^<]*)</a>')
_LB_HIDDEN = re.compile(r'class="d-none (total-comp|base-salary|stock-grant|yearly-bonus)" value="([^"]*)"')
_LB_H1 = re.compile(r"<h1[^>]*>\s*Top Paying Companies in (.*?)\s*</h1>", re.S)
_LB_SELECTED = re.compile(r"<option[^>]*selected[^>]*>([^<]+)</option>")
_LB_CANON = re.compile(r'<link rel="canonical" href="([^"]+)"')


def parse_leaderboard_html(html: str) -> dict[str, Any]:
    """{geo, level_label, canonical, rows:[{rank, slug, company, label, total, base, stock,
    bonus}]} — everything read off the page; a row missing its total is skipped, never invented."""
    body = html[html.find("<tbody"):html.find("</tbody>")] if "<tbody" in html else html
    rows: list[dict[str, Any]] = []
    for chunk in _LB_ROW.findall(body):
        rank = _LB_RANK.search(chunk)
        slug = _LB_SLUG.search(chunk)
        nums = {k: int(v) for k, v in _LB_HIDDEN.findall(chunk) if v.strip().lstrip("-").isdigit()}
        if not (rank and slug and nums.get("total-comp")):
            continue
        name = _LB_NAME.search(chunk)
        label = _LB_LABEL.search(chunk)
        rows.append({
            "rank": int(rank.group(1)), "slug": slug.group(1),
            "company": (name.group(1).strip() if name else slug.group(1)),
            "label": label.group(1).strip() if label else "",
            "total": nums["total-comp"], "base": nums.get("base-salary"),
            "stock": nums.get("stock-grant"), "bonus": nums.get("yearly-bonus"),
        })
    h1 = _LB_H1.search(html)
    selected = [s.strip() for s in _LB_SELECTED.findall(html) if s.strip()]
    canon = _LB_CANON.search(html)
    return {
        "geo": re.sub(r"\s+", " ", h1.group(1)).strip() if h1 else "",
        "level_label": selected[-1] if selected else "",
        "canonical": canon.group(1) if canon else "",
        "rows": rows,
    }
