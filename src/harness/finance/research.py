"""Event-anchored research deep-dive — the agentic catch-up artifact.

Composes the lane's primitives into narrative inputs: daily bars → big-move-day detection →
date-windowed Google News slices (keyless RSS, `after:`/`before:` operators) anchored to those days
+ month-by-month context + the EDGAR material-filings timeline + a next-print estimate from filing
cadence (Yahoo's calendar endpoint is crumb-locked; cadence estimation is the honest keyless
substitute). Writes a persistent report into `corpus/finance/research/{SYM}/` (the travel-report
pattern: date-stamped, citable, accreting) + a line-viz price chart.

Division of labor: this module GATHERS mechanically (repeatable, quota-free); the agent reads the
artifact and writes the synthesis on top. Google calls are self-paced (~1s) per the
burst-pacing-in-the-provider rule — a research run fans out ~10-20 queries.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from pydantic import BaseModel, Field

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import Bar, Filing, NewsItem
from harness.viz import VizError, render_diagram

_GNEWS_URL = "https://news.google.com/rss/search"
_GNEWS_MIN_INTERVAL = 1.0  # polite self-pacing across the fan-out
_last_gnews_call = 0.0


class MoveDay(BaseModel):
    day: str  # YYYY-MM-DD
    pct: float  # close-over-close move
    close: float
    headlines: list[NewsItem] = Field(default_factory=list)


class MonthSlice(BaseModel):
    month: str  # YYYY-MM
    headlines: list[NewsItem] = Field(default_factory=list)


class ResearchBundle(BaseModel):
    symbol: str
    query: str
    start: str
    end: str
    first_close: float | None = None
    last_close: float | None = None
    window_pct: float | None = None
    move_days: list[MoveDay] = Field(default_factory=list)
    months: list[MonthSlice] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    current: list[NewsItem] = Field(default_factory=list)
    next_print_estimate: str | None = None  # "≈ YYYY-MM-DD (est. from 10-Q/10-K cadence)"
    notes: list[str] = Field(default_factory=list)


def fetch_google_news(
    query: str, after: str, before: str, limit: int = 3, symbol: str = ""
) -> list[NewsItem]:
    """Date-windowed headlines from Google News RSS (keyless; self-paced)."""
    global _last_gnews_call
    wait = _GNEWS_MIN_INTERVAL - (time.monotonic() - _last_gnews_call)
    if wait > 0:
        time.sleep(wait)
    _last_gnews_call = time.monotonic()
    q = f"{query} after:{after} before:{before}"
    resp = get_with_retry(_GNEWS_URL, params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if resp.status_code != 200:
        raise ProviderError(f"google news {q!r}: HTTP {resp.status_code}")
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise ProviderError(f"google news {q!r}: bad XML") from e
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = (item.findtext("pubDate") or "").strip()
        published = ""
        if pub:
            try:
                published = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                published = pub
        out.append(
            NewsItem(
                symbol=symbol,
                title=title,
                url=(item.findtext("link") or "").strip(),
                source=(item.findtext("source") or "").strip(),
                published=published,
            )
        )
        if len(out) >= limit:
            break
    return out


def detect_move_days(bars: list[Bar], threshold: float = 3.0, cap: int = 8) -> list[MoveDay]:
    """Close-over-close moves ≥ threshold%, largest |move| first, capped (then chronological)."""
    moves: list[MoveDay] = []
    for prev, cur in zip(bars, bars[1:], strict=False):
        if not prev.c:
            continue
        pct = (cur.c - prev.c) / prev.c * 100.0
        if abs(pct) >= threshold:
            moves.append(MoveDay(day=cur.t[:10], pct=round(pct, 2), close=cur.c))
    moves.sort(key=lambda m: abs(m.pct), reverse=True)
    return sorted(moves[:cap], key=lambda m: m.day)


def estimate_next_print(filings: list[Filing]) -> str | None:
    """Next 10-Q/10-K window estimated from the filing cadence — honest, keyless, approximate."""
    prints = sorted(
        (f.filed for f in filings if f.form in {"10-Q", "10-K", "10-Q/A", "10-K/A"}), reverse=True
    )
    if len(prints) < 2:
        return None
    newest = date.fromisoformat(prints[0])
    gaps = [
        (date.fromisoformat(a) - date.fromisoformat(b)).days
        for a, b in zip(prints, prints[1:], strict=False)
    ]
    cadence = sorted(gaps)[len(gaps) // 2]  # median gap, robust to a 10-K straggler
    return f"≈ {(newest + timedelta(days=cadence)).isoformat()} (est. from 10-Q/10-K filing cadence)"


def _month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return out


def resolve_research_dir(symbol: str, tracker_path: Path, held_symbols: set[str]) -> Path:
    """Where a symbol's research lives (— the positions/candidates/themes taxonomy).

    Location-agnostic: an EXISTING dir anywhere under finance/research/ wins (so lifecycle
    `git mv`s — candidate→position on buy, sector re-filing — stick; the travel destinations-reorg
    pattern). New symbols file deterministically: held (in portfolio.yaml) → research/positions/;
    else → research/candidates/ root (a human/agent sorts it into a sector bucket after)."""
    research = tracker_path / "finance" / "research"
    matches = sorted(p for p in research.glob(f"**/{symbol}") if p.is_dir())
    if matches:
        return matches[0]
    bucket = "positions" if symbol in held_symbols else "candidates"
    return research / bucket / symbol


def write_research_report(
    bundle: ResearchBundle, bars: list[Bar], tracker_path: Path, held_symbols: set[str] | None = None
) -> Path:
    """Persist the catch-up artifact + its price chart; returns the report path."""
    today = date.today().isoformat()
    out_dir = resolve_research_dir(bundle.symbol, tracker_path, held_symbols or set())
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_rel = None
    if bars:
        try:
            render_diagram(
                "line",
                {
                    "title": f"{bundle.symbol} — daily close, {bundle.start} → {bundle.end}",
                    "series": [
                        {
                            "label": bundle.symbol,
                            "points": [{"x": b.t[:10], "y": b.c} for b in bars],
                        }
                    ],
                },
                out_dir / "visuals" / f"{today}-price.svg",
            )
            chart_rel = f"{out_dir.relative_to(tracker_path)}/visuals/{today}-price.svg"
        except VizError as e:
            bundle.notes.append(f"price chart skipped: {e}")

    L: list[str] = [
        "---",
        "tags:",
        "  - finance",
        "  - research",
        f"  - {bundle.symbol.lower()}",
        "  - deep-dive",
        "---",
        "",
        f"# {bundle.symbol} — research catch-up · {today}",
        "",
        f"> Event-anchored deep-dive over **{bundle.start} → {bundle.end}** (`hn finance research`).",
        "> Mechanical gather — headlines as the wires ran them; the synthesis layer is the agent's.",
        "> Sounding-board posture: observation, not advice.",
        "",
        "## At a glance",
        "",
        "**What they do**: ⚠️ _agent fills post-gather_ — the core business/commodity in plain words",
        "(the reader may not know this name yet).",
        "",
        "**Why surfaced / fit**: ⚠️ _agent fills post-gather_ — what surfaced it (thesis/shortcut) +",
        "values-screen status + current verdict.",
        "",
    ]
    if bundle.first_close and bundle.last_close and bundle.window_pct is not None:
        sign = "+" if bundle.window_pct >= 0 else ""
        L.append(
            f"**Window**: ${bundle.first_close:,.2f} → ${bundle.last_close:,.2f} "
            f"(**{sign}{bundle.window_pct:.1f}%**) · {len(bundle.move_days)} big-move days ≥ threshold"
        )
    if bundle.next_print_estimate:
        L.append(f"**Next print**: {bundle.next_print_estimate}")
    L.append("")
    if chart_rel:
        L += [f"![[{chart_rel}|860]]", ""]

    L += ["## Big-move days — what the wires said", ""]
    L.append("| Day | Move | Close | Coverage that day (±1d) |")
    L.append("|---|---|---|---|")
    for m in bundle.move_days:
        sign = "+" if m.pct >= 0 else ""
        heads = "<br>".join(
            f"[{h.title}]({h.url}) — {h.source}" for h in m.headlines
        ) or "—"
        L.append(f"| {m.day} | **{sign}{m.pct}%** | ${m.close:,.2f} | {heads} |")
    L.append("")

    L += ["## Month by month", ""]
    for ms in bundle.months:
        L.append(f"### {ms.month}")
        if not ms.headlines:
            L.append("- *(no headlines captured)*")
        for h in ms.headlines:
            L.append(f"- {h.published} — [{h.title}]({h.url}) — {h.source}")
        L.append("")

    L += ["## Material filings in the window", ""]
    if bundle.filings:
        L.append("| Form | Filed | Link |")
        L.append("|---|---|---|")
        for f in bundle.filings:
            L.append(f"| {f.form} | {f.filed} | [{f.form}]({f.url}) |")
    else:
        L.append("*(none / no CIK)*")
    L.append("")

    L += ["## Current wire (as of the run)", ""]
    for h in bundle.current:
        L.append(f"- {h.published} — [{h.title}]({h.url})")
    if bundle.notes:
        L += ["", "## Gather notes", ""] + [f"- {n}" for n in bundle.notes]
    L.append("")

    out = out_dir / f"{today}-catchup.md"
    out.write_text("\n".join(L))
    return out
