"""Shared Python ↔ D3 render engine (lane-agnostic).

Promoted to `harness.viz` at consolidation time: the render engine was travel-lane-local,
but both lanes render diagrams (`hn travel viz`, `hn finance viz`) — the finance lane previously reached
*cross-lane* into `harness.travel.viz` for it, a layering smell. The engine (render_diagram + VizError +
KNOWN_TYPES + the render.js path) lives here now; lane-specific data-prep helpers (events→timeline,
weather→strip, …) stay in their lane's `viz` module. `harness.travel.viz` re-exports these for back-compat.

Build the capability into the tool — never script around it. The Node
toolchain is isolated in `viz/`; this module is the only seam that calls it.
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# repo root: src/harness/viz.py → parents[2] == repo root (editable/uv-run layout)
_REPO_ROOT = Path(__file__).resolve().parents[2]
VIZ_RENDER_JS = _REPO_ROOT / "viz" / "render.js"

KNOWN_TYPES = (
    "timeline", "schedule", "schedule-bank", "food-bank", "radial", "compare", "matrix",
    "weather-strip", "map", "rank-bar", "calendar", "pie", "treemap", "sankey", "line",
    "scatter", "map-annotate", "flow",
)


class VizError(RuntimeError):
    """Render-pipeline failure (bad type, missing Node/renderer, non-zero render exit)."""


THEMES = ("light", "instrument")


def render_diagram(
    diagram_type: str, data: dict[str, Any], out_path: Path, *, theme: str = "light"
) -> Path:
    """Render ``data`` as ``diagram_type`` to ``out_path`` (.svg) via the Node/D3 renderer.

    ``theme`` selects the render.js token set: ``light`` is the original
    palette (default, output-stable); ``instrument`` mirrors the bus-app console so vault SVGs
    and the app's interactive viz read as one design system.

    Returns the written path. Raises :class:`VizError` on unknown type/theme, missing
    toolchain, or a non-zero render exit.
    """
    if diagram_type not in KNOWN_TYPES:
        raise VizError(f"unknown diagram type {diagram_type!r}; known: {', '.join(KNOWN_TYPES)}")
    if theme not in THEMES:
        raise VizError(f"unknown theme {theme!r}; known: {', '.join(THEMES)}")
    if not VIZ_RENDER_JS.exists():
        raise VizError(f"renderer not found at {VIZ_RENDER_JS} — run `npm install` in viz/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            tmp_path = Path(fh.name)
        try:
            proc = subprocess.run(
                ["node", str(VIZ_RENDER_JS), diagram_type, str(tmp_path), str(out_path), theme],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:  # `node` not on PATH
            raise VizError("`node` not found on PATH — install Node (brew install node)") from e
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise VizError(f"render failed (exit {proc.returncode}): {detail}")
    return out_path


def image_info(path: Path) -> tuple[int, int, str]:
    """(width, height, mime) for a PNG or JPEG, read from the file header — stdlib only (no Pillow;
    the API-over-library posture for a dims read). Raises VizError for unsupported/corrupt images."""
    raw = path.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        # IHDR is the first chunk: width/height are big-endian uint32 at bytes 16..24.
        w, h = struct.unpack(">II", raw[16:24])
        return w, h, "image/png"
    if raw[:2] == b"\xff\xd8":  # JPEG: scan segments for a Start-Of-Frame marker
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, seglen = raw[i + 1], struct.unpack(">H", raw[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOF (not DHT/JPG/DAC)
                h, w = struct.unpack(">HH", raw[i + 5 : i + 9])
                return w, h, "image/jpeg"
            i += 2 + seglen
    raise VizError(f"unsupported image type for {path.name} (map-annotate supports PNG/JPEG)")


def render_map_annotate(
    spec: dict[str, Any], out_path: Path, *, image_base: Path, grid: bool = False
) -> Path:
    """Render a `map-annotate` diagram: resolve + base64-embed `spec['image']`, read its native dims,
    then render the annotation overlays. `image_base` is the root that a relative `image` resolves
    against (the tracker corpus root). `grid=True` overlays a 0.1 coordinate grid (the coord-picker).

    base64-embed (not reference) is deliberate: an SVG that references an external local image does NOT
    reliably render in Obsidian; a data-URI is self-contained. Cost: ~1.33× the image size on disk."""
    img = Path(spec["image"])
    if not img.is_absolute():
        img = image_base / img
    if not img.exists():
        raise VizError(f"map-annotate image not found: {img}")
    w, h, mime = image_info(img)
    b64 = base64.b64encode(img.read_bytes()).decode()
    data = {
        **spec,
        "imageDataUri": f"data:{mime};base64,{b64}",
        "width": w,
        "height": h,
        "grid": grid or bool(spec.get("grid")),
    }
    return render_diagram("map-annotate", data, out_path)
