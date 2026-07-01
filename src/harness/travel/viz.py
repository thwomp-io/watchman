"""Travel-lane viz: the lane-specific data-prep helpers (events→timeline, weather→strip,
reference→calendar) + the Obsidian embed.

The lane-agnostic render engine (``render_diagram`` / ``VizError`` / ``KNOWN_TYPES`` / the
``viz/render.js`` path) was promoted to ``harness.viz`` so both lanes share it
without a finance→travel cross-lane import. It's re-exported here for back-compat (call sites +
tests reference ``harness.travel.viz.*``).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from harness.travel.config.settings import get_settings
from harness.travel.models import CityEvents, EventResult, WeatherForecast
from harness.viz import KNOWN_TYPES, VIZ_RENDER_JS, VizError, render_diagram, render_map_annotate

__all__ = [
    "KNOWN_TYPES",
    "VIZ_RENDER_JS",
    "VizError",
    "concise_event_label",
    "embed_markdown",
    "events_to_timeline",
    "reference_to_calendar",
    "render_diagram",
    "render_map_annotate",
    "short_time",
    "weather_to_strip",
]


def embed_markdown(svg_path: Path, *, width: int = 640) -> str:
    """Obsidian vault-relative embed wikilink for an SVG under the tracker vault."""
    tracker = get_settings().tracker_path.resolve()
    try:
        rel = svg_path.resolve().relative_to(tracker)
    except ValueError:
        rel = svg_path  # outside the vault — fall back to the raw path
    return f"![[{Path(rel).as_posix()}|{width}]]"


# ---- timeline data-prep (events → diagram data) ----

_VS = re.compile(r"^(.*?)\s+vs\.?\s+(.*)$", re.IGNORECASE)


def concise_event_label(name: str) -> str:
    """Tighten event names for chips upstream of render-time truncation.

    'Metro City Rovers vs. Anytown United' → 'Rovers vs Anytown' (drop a leading city/article
    prefix; keep the team token + the opponent's city token). Non-matchup names pass through.
    """
    n = re.sub(r"^(The)\s+", "", name.strip())
    m = _VS.match(n)
    if not m:
        return n
    left = m.group(1).split()
    right = m.group(2).split()
    home = left[-1] if left else m.group(1)
    opp = right[0] if right else m.group(2)
    return f"{home} vs {opp}"


def short_time(t: str | None) -> str | None:
    """'19:10:00' → '7:10p'; '13:10:00' → '1:10p'. Best-effort; passes odd input through."""
    if not t:
        return None
    parts = t.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return t
    suffix = "a" if h < 12 else "p"
    return f"{h % 12 or 12}:{m:02d}{suffix}"


def _lane_for(segment: str | None) -> str:
    seg = (segment or "").lower()
    if seg == "sports":
        return "sports"
    if "art" in seg or "theat" in seg:
        return "show"
    if seg == "music":
        return "music"
    return seg or "event"


def events_to_timeline(
    events: CityEvents,
    *,
    start: str,
    end: str,
    title: str,
    subtitle: str | None = None,
) -> dict[str, Any]:
    """Map a :class:`CityEvents` scan into ``timeline`` diagram data."""
    items = [
        {
            "date": ev.local_date,
            "time": short_time(ev.local_time),
            "label": concise_event_label(ev.name),
            "lane": _lane_for(ev.segment),
        }
        for ev in events.events
    ]
    return {"title": title, "subtitle": subtitle, "start": start, "end": end, "items": items}


def weather_to_strip(
    forecast: WeatherForecast,
    *,
    title: str,
    subtitle: str | None = None,
) -> dict[str, Any]:
    """Map a :class:`WeatherForecast` (the weather *sense*) into ``weather-strip`` diagram data —
    the live builder so you never hand-author weather JSON (mirrors events_to_timeline)."""
    days = [
        {"date": d.date, "hi": d.temp_max, "lo": d.temp_min, "precip": d.precip_prob or 0}
        for d in forecast.days
        if d.temp_max is not None and d.temp_min is not None
    ]
    return {
        "title": title,
        "subtitle": subtitle,
        "unit": (forecast.temperature_unit or "°F").lstrip("°"),
        "days": days,
    }


_SEG_KIND = {"Sports": "sports", "Holiday": "holiday", "Personal": "personal", "Mega-event": "mega-event"}


def reference_to_calendar(
    entries: list[EventResult],
    *,
    start: date,
    end: date,
    title: str,
    subtitle: str | None = None,
) -> dict[str, Any]:
    """Bucket reference-almanac entries into a 12-ish-month `calendar` grid (one cell per month in
    [start, end]) — the 'when to travel' planning view. Each item carries a kind (holiday/sports/
    personal/mega-event) for color; the month's density = item count."""
    single_year = start.year == end.year
    buckets: dict[tuple[int, int], list[dict[str, str]]] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        buckets[(y, m)] = []
        m += 1
        if m > 12:
            m, y = 1, y + 1
    for e in entries:
        try:
            d = date.fromisoformat(e.local_date)
        except ValueError:
            continue
        key = (d.year, d.month)
        if key in buckets:
            buckets[key].append({"label": e.name, "kind": _SEG_KIND.get(e.segment, "")})
    abbr = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    months = [
        {"name": abbr[mo] if single_year else f"{abbr[mo]} {yr}", "items": items}
        for (yr, mo), items in buckets.items()
    ]
    return {"title": title, "subtitle": subtitle, "months": months}
