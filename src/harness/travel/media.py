"""Download + compress + store images into the tracker vault, ready to embed in reports.

Local-download (NOT remote hotlinking) is deliberate: self-contained reports, offline-viewable,
no per-render pings to external servers (privacy), durable against link-rot. Pillow downscales +
compresses so the vault stays lean. Images are 100% personal-use (a personal-use disclaimer covers
any future OSS state), so source selection is driven by relevance × quota-cost, not licensing.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from PIL import Image, ImageDraw, ImageFont

from harness._http import CONTACT_USER_AGENT, describe_http_error, get_with_retry
from harness.paths import cache_file
from harness.travel.config.settings import get_settings
from harness.travel.imaging import shrink_bytes
from harness.travel.models import ImageCandidate, ImageResult
from harness.travel.providers import get_image_provider
from harness.travel.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.travel.service import TravelService

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
    - a **vault path under travel/** (contains '/', e.g. `visits/2026-05-example-visit`,
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

# Image bytes (in practice upload.wikimedia.org) go out under the contact-carrying tool UA
# (`_http.CONTACT_USER_AGENT`): Wikimedia's robot policy asks clients to identify themselves rather
# than pose as a browser. Its CDN throttles bursts (429); the shared backoff + per-subject delay absorb them.


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "image"


def _download_bytes(
    url: str, referer: str, client: httpx.Client | None, timeout: float
) -> bytes:
    """GET image bytes under the contact tool UA; retry/backoff is the shared get_with_retry helper's."""
    headers = {
        "User-Agent": CONTACT_USER_AGENT,
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
        raise RuntimeError(f"image download failed: {describe_http_error(e)}") from e
    return resp.content


def store_image(
    candidate: ImageCandidate,
    vault_root: Path,
    dest: str,
    subject: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    keep_original: bool = False,
) -> ImageResult:
    """Download the candidate's image, downscale/compress to JPEG, write under the doc's `assets/`
    dir, and return an embeddable ImageResult. `dest` resolves via `_assets_parts` — a bare slug
    maps to `travel/destinations/{slug}/assets`, a path under travel/ maps to `travel/{dest}/assets`."""
    raw = _download_bytes(candidate.image_url, candidate.source_url, client, timeout)
    parts = _assets_parts(dest, vault_root)
    assets_dir = vault_root.joinpath(*parts)
    assets_dir.mkdir(parents=True, exist_ok=True)
    if keep_original:
        # the rare case (a print-quality asset wanted on purpose) — bytes as served, format kept
        img = Image.open(io.BytesIO(raw))
        img.load()
        ext = (img.format or "JPEG").lower().replace("jpeg", "jpg")
        filename = f"{slugify(subject)}.{ext}"
        abs_path = assets_dir / filename
        abs_path.write_bytes(raw)
        width, height = img.size
    else:
        # the ONE size discipline for vault images (imaging.py): long edge ≤ 1600, JPEG q85,
        # ~400KB best-effort cap, metadata dropped. Formerly an inline thumbnail at 1280/q82.
        shrunk = shrink_bytes(raw, force_format="JPEG")
        filename = f"{slugify(subject)}.jpg"
        abs_path = assets_dir / filename
        abs_path.write_bytes(shrunk.data)
        width, height = shrunk.width, shrunk.height

    rel_path = "/".join((*parts, filename))
    return ImageResult(
        subject=subject,
        source=candidate.source,
        source_url=candidate.source_url,
        attribution=candidate.attribution,
        rel_path=rel_path,
        abs_path=str(abs_path),
        width=width,
        height=height,
        size_bytes=abs_path.stat().st_size,
    )


def archive_photo(
    url: str, vault_root: Path, rel_dir: str, name: str,
    *, client: httpx.Client | None = None, timeout: float = 30.0,
) -> Path:
    """Download one photo URL into `travel/{rel_dir}/{name}.jpg` under the cap — the tool for the
    gallery-archive case that used to be a curl fallback (and produced a 28MB original)."""
    raw = _download_bytes(url, "", client, timeout)
    shrunk = shrink_bytes(raw, force_format="JPEG")
    out_dir = vault_root / "travel" / rel_dir.strip("/")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slugify(name)}.jpg"
    out.write_bytes(shrunk.data)
    return out


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


def fetch_destination_images(svc: TravelService, dest: str,
    subjects: list[str],
    provider_name: str = "wikimedia",
    vault_root: Path | None = None,
    delay_s: float = 1.0,
    keep_original: bool = False,
    errors: dict[str, str] | None = None,
) -> list[ImageResult]:
    """For each subject (hotel / attraction / view), find an image and store it locally under
    the doc's assets/ dir. `dest` is a bare destination slug (→ travel/destinations/{slug}/assets,
    back-compat) OR a vault path under travel/ (→ travel/{dest}/assets, like `viz --dest` — lets
    visits/ + trips/ folder-notes be illustrated). Returns embeddable ImageResults.

    Resilient + polite: a brief `delay_s` between subjects smooths bursts (Wikimedia throttles
    rapid runs), and a subject that finds nothing OR errors is skipped, not fatal — the rest still
    come back. A subject that ERRORED is recorded in `errors` (subject → reason, the HTTP status
    when there is one), so a blocked provider never reads as an empty search."""
    root = vault_root or get_settings().tracker_path
    provider = get_image_provider(provider_name)
    results: list[ImageResult] = []
    for i, subject in enumerate(subjects):
        if i:
            time.sleep(delay_s)  # politeness throttle — not a limiter; just spaces the burst
        try:
            candidates = provider.search_images(subject, limit=1)
            if not candidates:
                continue
            results.append(
                store_image(candidates[0], root, dest, subject, keep_original=keep_original)
            )
        except (ProviderError, RuntimeError) as e:
            if errors is not None:
                errors[subject] = str(e)  # skip this subject, keep the rest, say why
    return results


def make_contact_sheet(svc: TravelService, dest: str | None = None,
    paths: list[str] | None = None,
    *,
    name: str | None = None,
    cols: int = 4,
    vault_root: Path | None = None,
) -> Path:
    """Tile a destination's `assets/` (by slug/path) OR an explicit list of image paths into one
    labeled grid PNG — the eyeball aid for reviewing a fetched batch in a single look before
    embedding. Written to `~/.cache/harness/contact-sheets/` (NOT the vault). Returns its path."""
    if paths:
        imgs = [Path(p) for p in paths]
    elif dest:
        root = vault_root or get_settings().tracker_path
        assets = root.joinpath(*dest_dir_parts(dest, root), "assets")
        imgs = sorted(p for p in assets.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    else:
        raise ValueError("make_contact_sheet: pass dest or paths")
    if not imgs:
        raise ValueError("make_contact_sheet: no images found")
    out = cache_file("contact-sheets", f"{name or dest or 'sheet'}.png")  # sealable (paths.py)
    return build_contact_sheet(imgs, out, cols=cols)
