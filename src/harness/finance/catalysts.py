"""Catalyst wire hygiene — what reaches the Inbox WIRE band, and how it reads there.

Three deterministic steps on the single-name catalysts the watch gathers:

1. **Junk filter** — per-ticker Google News searches match quote and option-chain pages ("ACME Oct
   2026 300.000 call (ACME261009C00300000) Stock Historical Prices & Data", "Interactive Stock Chart",
   "Stock forecasts"). Those are not news. A rule list (packaged defaults + `catalyst_filters:` in
   feeds.yaml) names the match; a matched item is kept OUT of the digest's fresh news but still
   published, flagged, so the Inbox can count and reveal what was filtered instead of silently
   losing it.
2. **Story key** — the same story arrives under several tickers (one chipmaker story filed under two
   holdings) and twice under one (with and without Google's " - Outlet" suffix). `story_key`
   normalizes a headline so the Inbox can collapse those rows into one with every ticker chip.
3. **Link resolution** — a Google News link (`news.google.com/rss/articles/…`) is resolved to the
   publisher's article through the wire reader's resolver, on the publish path only, cached
   (resolved links never change) and capped per run so a backlog never stalls the standing agent.
   A link that won't resolve keeps the Google link — the reader labels it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from harness.finance.models import NewsItem

# Generic quote/option-page shapes — not user data; the config list extends these.
DEFAULT_RULES: list[tuple[str, str]] = [
    ("option-contract", r"\b[A-Z]{1,6}\d{6}[CP]\d{8}\b"),  # an OCC option symbol
    ("option-contract", r"\b\d{4}\s+\d+(?:\.\d+)?\s+(?:call|put)\b"),  # "Oct 2026 300.000 call"
    ("quote-page", r"\bhistorical prices\s*(?:&|and)\s*data\b"),
    ("quote-page", r"\binteractive stock chart\b"),
    ("quote-page", r"\bstock forecasts?\b"),
    ("quote-page", r"\bstock (?:price|quote)\s*(?:&|and|\||-|—)\s*(?:news|chart|quote|history)\b"),
    ("quote-page", r"\bstock quote\b"),
    ("quote-page", r"\bquote\s*(?:&|and)\s*history\b"),  # "Stock Price, News, Quote & History"
    ("quote-page", r"\bstock price today:?\s*\$\d"),  # "Stock Price Today: $204.19"
]


def load_rules(extra: Iterable[dict[str, Any]] | None = None) -> list[tuple[str, re.Pattern[str]]]:
    """The packaged rules plus config rows `{name, pattern}` (case-insensitive regex). A config
    pattern that doesn't compile is skipped, never fatal — a typo must not stop the standing agent."""
    rules: list[tuple[str, re.Pattern[str]]] = [
        (name, re.compile(pat, re.IGNORECASE)) for name, pat in DEFAULT_RULES
    ]
    for row in extra or []:
        name, pat = str(row.get("name", "custom")), str(row.get("pattern", ""))
        if not pat:
            continue
        try:
            rules.append((name, re.compile(pat, re.IGNORECASE)))
        except re.error:
            continue
    return rules


def config_rules() -> list[tuple[str, re.Pattern[str]]]:
    """The packaged rules + feeds.yaml `catalyst_filters:` (the same file the wire roster lives in)."""
    from harness.finance.config.settings import get_settings
    from harness.yamlio import load_yaml

    path = get_settings().feeds_path
    extra: list[dict[str, Any]] = []
    if path.exists():
        data = load_yaml(path.read_text()) or {}
        extra = [r for r in data.get("catalyst_filters") or [] if isinstance(r, dict)]
    return load_rules(extra)


def classify(item: NewsItem, rules: list[tuple[str, re.Pattern[str]]]) -> str:
    """The first rule the headline matches ("" = real news)."""
    for name, pat in rules:
        if pat.search(item.title):
            return name
    return ""


def triage(
    items: list[NewsItem], rules: list[tuple[str, re.Pattern[str]]]
) -> tuple[list[NewsItem], list[NewsItem]]:
    """Split fresh news into (kept, filtered). Only symbol-tagged single-name items are judged —
    the broad wire and SEC filings pass through untouched."""
    kept: list[NewsItem] = []
    dropped: list[NewsItem] = []
    for it in items:
        rule = classify(it, rules) if it.symbol and it.source != "sec.gov" else ""
        if rule:
            dropped.append(it.model_copy(update={"filtered": rule}))
        else:
            kept.append(it)
    return kept, dropped


def story_key(title: str, publisher: str = "") -> str:
    """A headline normalized for same-story grouping: the outlet suffix dropped, case and
    punctuation folded ("Prediction: $5,000 Invested… - The Globe and Mail" == "prediction 5000
    invested…")."""
    t = title
    if publisher and t.endswith(f" - {publisher}"):
        t = t[: -len(publisher) - 3]
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


# ---- link resolution (publish path only) ----

def _cache_path() -> Any:
    from harness.paths import cache_file

    return cache_file("gnews-resolved.json")


def load_resolved() -> dict[str, str]:
    try:
        data = json.loads(_cache_path().read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_resolved(cache: dict[str, str]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # bounded: the newest 3000 entries (insertion order) — older catalysts have aged off the bus
    keep = dict(list(cache.items())[-3000:])
    path.write_text(json.dumps(keep))


def _default_resolver(url: str) -> str:
    import httpx

    from harness.finance.providers.article_provider import resolve_gnews
    from harness.finance.scan import paced

    with paced(url), httpx.Client(timeout=15.0, follow_redirects=True) as client:
        return resolve_gnews(url, client)


def resolve_links(
    items: list[NewsItem],
    *,
    cap: int = 40,
    resolver: Callable[[str], str] | None = None,
    cache: dict[str, str] | None = None,
) -> tuple[list[NewsItem], str]:
    """Fill `resolved_url` for Google News links: cached hits first (free), then up to `cap` fresh
    resolutions this run. Returns the items and a run-log note ("" when nothing was attempted)."""
    from harness.errors import ProviderError
    from harness.finance.providers.article_provider import _is_gnews

    res = resolver or _default_resolver
    known = load_resolved() if cache is None else cache
    out: list[NewsItem] = []
    tried = ok = 0
    for it in items:
        if it.resolved_url or not _is_gnews(it.url):
            out.append(it)
            continue
        hit = known.get(it.url)
        if hit is None and tried < cap:
            tried += 1
            try:
                hit = res(it.url)
                known[it.url] = hit
                ok += 1
            except (ProviderError, OSError, ValueError):
                hit = None
        out.append(it.model_copy(update={"resolved_url": hit}) if hit else it)
    if cache is None and ok:
        save_resolved(known)
    note = f"links: {ok}/{tried} resolved" if tried else ""
    return out, note
