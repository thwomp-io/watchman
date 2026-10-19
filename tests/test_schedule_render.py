"""The schedule renderer through Node: untimed items are header chips (never a block at a guessed
hour), and a block title wraps across the lines its block holds. Skipped where the viz tree or
Node is absent (CI without the node install)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from harness import viz

try:
    viz.viz_render_js()
    _HAVE_VIZ = shutil.which("node") is not None
except viz.VizError:
    _HAVE_VIZ = False

pytestmark = pytest.mark.skipif(not _HAVE_VIZ, reason="viz renderer / node not installed")

DATA = {
    "title": "Sample visit", "start": "2030-05-01", "end": "2030-05-02",
    "dayStart": "08:00", "dayEnd": "22:00",
    "items": [
        {"date": "2030-05-01", "label": "Host working all day", "lane": "host"},
        {"date": "2030-05-01", "label": "Guests solo: harbor walk", "lane": "outdoors"},
        {"date": "2030-05-02", "start": "09:30", "end": "11:00", "lane": "dining",
         "label": "Corner Bakery breakfast with the whole group"},
    ],
}


def _texts(svg: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", t) for t in re.findall(r"<text[^>]*>.*?</text>", svg)]


def test_untimed_items_are_header_chips_and_titles_wrap(tmp_path: Path) -> None:
    out = viz.render_diagram("schedule", DATA, tmp_path / "s.svg")
    svg = out.read_text()
    texts = _texts(svg)
    # each untimed item appears once, as a chip label — no clock prefix, so no block at a guessed hour
    assert texts.count("Host working all day") == 1 and texts.count("Guests solo: harbor walk") == 1
    assert not any(t.startswith("12p ") for t in texts)
    # the 90-minute block's title runs onto a second line instead of one truncated line
    first = next(t for t in texts if t.startswith("9:30a "))
    i = texts.index(first)
    assert "Corner" in first and texts[i + 1].split()[0] in {"breakfast", "Bakery", "with"}
