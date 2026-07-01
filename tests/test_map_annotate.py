"""map-annotate prep: image dims (stdlib header read) + base64 embed + grid injection.

The Node/D3 render itself is exercised manually / live (like the rest of the viz engine), so here we
stub `render_diagram` and assert on the data dict the prep builds.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

import harness.viz as viz
from harness.viz import VizError, image_info, render_map_annotate


def _png(width: int, height: int) -> bytes:
    # Minimal bytes whose PNG signature + IHDR width/height are valid (image_info only reads the header).
    return (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
        + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\x00" * 8
    )


def _jpeg(width: int, height: int) -> bytes:
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" * 6


def test_image_info_png(tmp_path: Path) -> None:
    p = tmp_path / "m.png"
    p.write_bytes(_png(2013, 1182))
    assert image_info(p) == (2013, 1182, "image/png")


def test_image_info_jpeg(tmp_path: Path) -> None:
    p = tmp_path / "m.jpg"
    p.write_bytes(_jpeg(640, 480))
    assert image_info(p) == (640, 480, "image/jpeg")


def test_image_info_unsupported_raises(tmp_path: Path) -> None:
    p = tmp_path / "m.gif"
    p.write_bytes(b"GIF89a\x00\x00")
    with pytest.raises(VizError, match="unsupported image type"):
        image_info(p)


def test_render_map_annotate_embeds_and_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # image referenced RELATIVE to image_base (the tracker root)
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "screenshots" / "map.png").write_bytes(_png(800, 600))

    captured: dict[str, Any] = {}

    def fake_render(diagram_type: str, data: dict[str, Any], out_path: Path) -> Path:
        captured["type"] = diagram_type
        captured["data"] = data
        return out_path

    monkeypatch.setattr(viz, "render_diagram", fake_render)

    spec = {
        "image": "screenshots/map.png",
        "title": "Test walk",
        "pins": [{"xf": 0.5, "yf": 0.5, "caption": "spot", "star": True}],
    }
    out = render_map_annotate(spec, tmp_path / "out.svg", image_base=tmp_path, grid=True)

    assert out == tmp_path / "out.svg"
    assert captured["type"] == "map-annotate"
    d = captured["data"]
    assert d["width"] == 800 and d["height"] == 600
    assert d["imageDataUri"].startswith("data:image/png;base64,")
    assert d["grid"] is True
    assert d["title"] == "Test walk" and d["pins"][0]["caption"] == "spot"  # spec passed through


def test_render_map_annotate_missing_image_raises(tmp_path: Path) -> None:
    with pytest.raises(VizError, match="image not found"):
        render_map_annotate({"image": "nope.png"}, tmp_path / "o.svg", image_base=tmp_path)
