"""One image-size discipline for everything the travel lane writes into the vault.

Why: full-resolution photos archived as raw bytes bloat the vault's git history fast. A vault
image is embedded at ~480px and read on a phone; anything past ~1600px on the long edge is
invisible weight that every clone and every backup carries forever. This module makes the cap
ONE rule every write site shares, and gives the maintenance verb (`hn travel shrink-images`) the
same engine for the assets already on disk.

Rules:
- Long edge ≤ `MAX_EDGE` (1600), re-encoded (EXIF/GPS/ICC metadata dropped by construction —
  a downloaded photo's location tags are not corpus data).
- JPEG q85 for opaque images; PNG only when the image actually uses transparency.
- Best-effort byte cap `MAX_BYTES` (~400KB): quality steps down, then the edge, to floors that
  still read fine at embed size; a PNG that can't reach the cap is left at max-edge/optimized
  (flagged in the result, never silently converted — the file's extension is a link contract).
- In-place shrinks keep the file's own format so every `![[…]]` embed stays valid.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import Image

MAX_EDGE = 1600
JPEG_QUALITY = 85
MAX_BYTES = 400_000
# the floors the byte cap may step down to before accepting an over-cap result
_MIN_QUALITY = 60
_MIN_EDGE = 960

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Shrunk:
    data: bytes
    fmt: str  # "JPEG" | "PNG"
    width: int
    height: int
    over_cap: bool  # best-effort cap not reached (PNG with transparency, or a floor hit)

    @property
    def suffix(self) -> str:
        return ".png" if self.fmt == "PNG" else ".jpg"


def _has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        alpha = img.convert("RGBA").getchannel("A")
        lo, _hi = cast(tuple[int, int], alpha.getextrema())  # a single band → an (lo, hi) pair
        return lo < 255
    return False


def _encode(img: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG", optimize=True)
    else:
        img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def shrink_bytes(
    raw: bytes,
    *,
    max_edge: int = MAX_EDGE,
    quality: int = JPEG_QUALITY,
    max_bytes: int = MAX_BYTES,
    force_format: str | None = None,
) -> Shrunk:
    """Downscale + re-encode `raw` under the discipline above. `force_format` ("JPEG"/"PNG")
    pins the output format (in-place shrinks pass the file's own so links keep resolving)."""
    img: Image.Image = Image.open(io.BytesIO(raw))
    img.load()
    fmt = force_format or ("PNG" if _has_transparency(img) else "JPEG")
    if fmt == "PNG":
        # preserve alpha when it exists; an opaque PNG pinned by extension goes RGB (smaller)
        img = img.convert("RGBA") if _has_transparency(img) else img.convert("RGB")
    else:
        img = img.convert("RGB")
    edge = max_edge
    q = quality
    img.thumbnail((edge, edge))  # in place; never upscales
    data = _encode(img, fmt, q)
    over = len(data) > max_bytes
    # step quality, then edge, toward the cap — floors keep a phone-sized embed legible
    while over and fmt == "JPEG" and (q > _MIN_QUALITY or edge > _MIN_EDGE):
        if q > _MIN_QUALITY:
            q -= 10
        else:
            edge = max(_MIN_EDGE, int(edge * 0.8))
            img.thumbnail((edge, edge))
        data = _encode(img, fmt, q)
        over = len(data) > max_bytes
    return Shrunk(data=data, fmt=fmt, width=img.width, height=img.height, over_cap=over)


@dataclass
class ShrinkReport:
    path: Path
    before: int
    after: int
    width: int
    height: int
    over_cap: bool
    changed: bool

    @property
    def saved(self) -> int:
        return self.before - self.after


def shrink_file(
    path: Path,
    *,
    max_edge: int = MAX_EDGE,
    max_bytes: int = MAX_BYTES,
    dry_run: bool = False,
) -> ShrinkReport:
    """Shrink one vault image IN PLACE, keeping its extension (so embeds stay valid). Files already
    under the cap AND within the edge are left byte-identical. `dry_run` reports without writing."""
    before = path.stat().st_size
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as probe:
        w, h = probe.size
    if before <= max_bytes and max(w, h) <= max_edge:
        return ShrinkReport(path, before, before, w, h, over_cap=False, changed=False)
    fmt = "PNG" if path.suffix.lower() == ".png" else "JPEG"
    out = shrink_bytes(raw, max_edge=max_edge, max_bytes=max_bytes, force_format=fmt)
    if len(out.data) >= before:  # never grow a file; report it untouched
        return ShrinkReport(path, before, before, w, h, over_cap=out.over_cap, changed=False)
    if not dry_run:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(out.data)
        tmp.replace(path)
    return ShrinkReport(path, before, len(out.data), out.width, out.height, out.over_cap, True)


def shrink_tree(
    root: Path, *, max_edge: int = MAX_EDGE, max_bytes: int = MAX_BYTES, dry_run: bool = False,
    skip_png: bool = False,
) -> list[ShrinkReport]:
    """Shrink every image under `root` (recursive). `skip_png` leaves PNGs untouched — for trees
    whose PNGs are registration bases for SVG overlays (a map canvas whose pixel size an overlay
    is drawn against must not change)."""
    out: list[ShrinkReport] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if skip_png and p.suffix.lower() == ".png":
            continue
        try:
            out.append(shrink_file(p, max_edge=max_edge, max_bytes=max_bytes, dry_run=dry_run))
        except (OSError, ValueError):
            continue  # not an image after all (a mis-suffixed file) — skip, never crash the walk
    return out
