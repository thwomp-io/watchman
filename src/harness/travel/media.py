"""Download + compress + store images into the tracker vault, ready to embed in reports.

Local-download (NOT remote hotlinking) is deliberate: self-contained reports, offline-viewable,
no per-render pings to external servers (privacy), durable against link-rot. Pillow downscales +
compresses so the vault stays lean. Images are 100% personal-use (a personal-use disclaimer covers
any future OSS state), so source selection is driven by relevance × quota-cost, not licensing.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from harness._http import get_with_retry
from harness.travel.models import ImageCandidate, ImageResult

_MAX_EDGE = 1280
_JPEG_QUALITY = 82
_SHEET_PAD = 12
_SHEET_LABEL_H = 26
_SHEET_BG = (245, 245, 247)
_SHEET_FG = (29, 29, 31)
# Folder-per-destination layout: assets live at travel/destinations/{slug}/assets/ (vault-relative).
_DEST_PARTS = ("travel", "destinations")


def dest_dir_parts(dest: str, vault_root: Path) -> tuple[str, ...]:
    """Vault-relative parts to a doc's FOLDER (no `assets/`). `dest` is polymorphic, mirroring
    `viz --dest`:

    - a **bare slug** (no '/', e.g. `san-diego`) → the destination's folder, resolved to wherever it's
      filed — flat `travel/destinations/{slug}/` OR archetype-nested
      `travel/destinations/{archetype}/{slug}/` (so recategorizing never breaks resolution); falls back
      to the flat layout for a brand-new destination.
    - a **vault path under travel/** (contains '/', e.g. `visits/2026-05-30-family-visit`,
      `destinations/{archetype}/{slug}`) → `travel/{dest}`.
    """
    if "/" in dest:
        return ("travel", *dest.split("/"))
    dest_root = vault_root / "travel" / "destinations"
    match = next((d for d in sorted(dest_root.glob(f"**/{dest}")) if d.is_dir()), None)
    if match is not None:
        return match.relative_to(vault_root).parts
    return (*_DEST_PARTS, dest)


def _assets_parts(dest: str, vault_root: Path) -> tuple[str, ...]:
    """The doc's `assets/` dir — the folder (see `dest_dir_parts`) plus `assets`."""
    return (*dest_dir_parts(dest, vault_root), "assets")

# The media CDN (upload.wikimedia.org) aggressively throttles non-browser clients (429), so image
# *byte* downloads use browser-like headers. (API *search* calls keep a descriptive tool UA — good
# etiquette where it isn't being blocked.) All personal-use; relevance × reliability drives this.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "image"


def _download_bytes(
    url: str, referer: str, client: httpx.Client | None, timeout: float
) -> bytes:
    """GET image bytes with browser-like headers (upload.wikimedia.org 429s non-browser clients).
    Retry/backoff is handled by the shared get_with_retry helper."""
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    try:
        resp = get_with_retry(
            url, headers=headers, client=client, timeout=timeout, follow_redirects=True
        )
    except httpx.HTTPError as e:
        raise RuntimeError(f"image download failed: {e}") from e
    return resp.content


def store_image(
    candidate: ImageCandidate,
    vault_root: Path,
    dest: str,
    subject: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> ImageResult:
    """Download the candidate's image, downscale/compress to JPEG, write under the doc's `assets/`
    dir, and return an embeddable ImageResult. `dest` resolves via `_assets_parts` — a bare slug
    maps to `travel/destinations/{slug}/assets`, a path under travel/ maps to `travel/{dest}/assets`."""
    raw = _download_bytes(candidate.image_url, candidate.source_url, client, timeout)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((_MAX_EDGE, _MAX_EDGE))

    parts = _assets_parts(dest, vault_root)
    assets_dir = vault_root.joinpath(*parts)
    assets_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(subject)}.jpg"
    abs_path = assets_dir / filename
    img.save(abs_path, format="JPEG", quality=_JPEG_QUALITY, optimize=True)

    rel_path = "/".join((*parts, filename))
    return ImageResult(
        subject=subject,
        source=candidate.source,
        source_url=candidate.source_url,
        attribution=candidate.attribution,
        rel_path=rel_path,
        abs_path=str(abs_path),
        width=img.width,
        height=img.height,
        size_bytes=abs_path.stat().st_size,
    )


def build_contact_sheet(
    images: list[Path], out_path: Path, *, cols: int = 4, thumb_w: int = 360
) -> Path:
    """Tile `images` into a single labeled grid PNG at `out_path` — an *eyeball aid* so a whole
    batch of fetched candidates can be reviewed in one look before embedding (the mandatory
    eyeball-before-embed discipline; catches wrong-place mis-resolves). Each cell shows the image
    scaled to `thumb_w` wide + its filename label. Not a vault artifact — write it to a cache dir.
    """
    if not images:
        raise ValueError("build_contact_sheet: no images given")
    thumbs: list[tuple[str, Image.Image]] = []
    for p in images:
        im = Image.open(p).convert("RGB")
        im.thumbnail((thumb_w, thumb_w * 10))  # cap width; preserve aspect (tall cap is effectively none)
        thumbs.append((p.name, im))
    cols = max(1, min(cols, len(thumbs)))
    rows = (len(thumbs) + cols - 1) // cols
    cell_w = thumb_w + _SHEET_PAD * 2
    cell_h = max(im.height for _, im in thumbs) + _SHEET_LABEL_H + _SHEET_PAD * 2
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), _SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()  # bundled — no external font-file dependency
    for idx, (name, im) in enumerate(thumbs):
        r, c = divmod(idx, cols)
        x, y = c * cell_w + _SHEET_PAD, r * cell_h + _SHEET_PAD
        sheet.paste(im, (x, y))
        draw.text((x, y + im.height + 6), name, fill=_SHEET_FG, font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "PNG")
    return out_path
