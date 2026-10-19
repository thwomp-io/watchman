"""Photo galleries for a visit's bank — several photos per outing/restaurant tile, not one.

For each bank item that is a real place this resolves the Google Maps place, pulls its photo
gallery (one quota search), and stores gallery-sized copies under the visit folder:

    travel/visits/<slug>/assets/gallery/<item-id>/01.jpg, 02.jpg, …

`hn travel bank` discovers these folders at read time, so curating a gallery is deleting a file;
the agent-authored bank JSONs are never rewritten. Resolved places (id, title, address, coords)
land in a sidecar `places.json` beside them, which later enrichment and the map reuse.

Resolution order, cheapest first: the sidecar (a place resolved before) → any earlier google_maps
search in the provider cache (free) → one `lookup_place` search for "label, area" around the home
locale. Items with `"gallery": false` are not places (a cook-in, a TV game) and are skipped.

The deterministic filters drop what never makes a good tile — videos and thumbnails at the
provider, then after download anything smaller than `MIN_EDGE`, any 360° panorama (aspect past
`MAX_ASPECT`), and near-duplicates (an 8×8 average hash within `DUP_BITS`). Junk a rule can't see
(menus, receipts, a blurry selfie) is the eyeball pass on the contact sheet.
"""

from __future__ import annotations

import io
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image

from harness.travel.bank import GALLERY_DIR, BankItem, BankKind, VisitBank, gallery_files
from harness.travel.imaging import shrink_bytes

SIDECAR = "places.json"
EDGE = 1200  # long edge of a stored gallery photo (a tile is ~200px, the popup ~640px)
MAX_BYTES = 180_000
MIN_EDGE = 480  # smaller than this after download = a thumbnail, not a photo
MAX_ASPECT = 2.2  # wider than this = a 360° / panorama strip
DUP_BITS = 6  # average-hash Hamming distance at or under which two photos are the same shot
PER_ITEM = 8


@dataclass
class ItemResult:
    id: str
    label: str
    place: str = ""  # the resolved Google title — compare it with the label on review
    source: str = ""  # sidecar | cache | search | skipped
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    note: str = ""


@dataclass
class GalleryRun:
    visit: str
    searches_planned: int = 0
    searches_spent: int = 0
    items: list[ItemResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "visit": self.visit,
            "searches_planned": self.searches_planned,
            "searches_spent": self.searches_spent,
            "items": [vars(i) for i in self.items],
        }


def read_sidecar(folder: Path) -> dict[str, Any]:
    p = folder / SIDECAR
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_sidecar(folder: Path, data: dict[str, Any]) -> None:
    (folder / SIDECAR).write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def place_query(item: BankItem) -> str:
    """The Maps query for an item: the `query=` of its authored Google Maps link when it has one
    (the bank author already named the exact place — "Springfield History Museum" for a
    "Springfield museums" tile), else its label (decoration stripped) plus its area."""
    maps = item.links.get("maps", "")
    q = parse_qs(urlparse(maps).query).get("query", [""])[0].strip() if maps else ""
    if q:
        return " ".join(q.split())
    label = "".join(ch for ch in item.label if ch.isalnum() or ch in " &'+-.,()").strip()
    return f"{label}, {item.area}" if item.area else label


def wants_gallery(item: BankItem) -> bool:
    return item.gallery


def ahash(img: Image.Image) -> int:
    small = img.convert("L").resize((8, 8))
    px = list(small.tobytes())
    avg = sum(px) / len(px)
    return sum(1 << i for i, v in enumerate(px) if v >= avg)


def _near(h: int, seen: list[int]) -> bool:
    return any(bin(h ^ s).count("1") <= DUP_BITS for s in seen)


def store_gallery(
    urls: list[str],
    out_dir: Path,
    fetch: Callable[[str], bytes],
    *,
    limit: int = PER_ITEM,
    seen: list[int] | None = None,
    pause_s: float = 0.0,
) -> tuple[int, dict[str, int]]:
    """Download → filter → write `01.jpg …` into `out_dir` until `limit` are kept. Returns
    (kept, dropped-by-reason). `seen` carries hashes already present (the cover photo), so a
    gallery copy of the cover is dropped as a duplicate."""
    hashes = list(seen or [])
    dropped: dict[str, int] = {}
    kept = 0

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for url in urls:
        if kept >= limit:
            break
        try:
            raw = fetch(url)
            with Image.open(io.BytesIO(raw)) as probe:
                probe.load()
                w, h = probe.size
                hsh = ahash(probe)
        except Exception:  # noqa: BLE001 — a dead or undecodable URL is one dropped photo
            drop("unreadable")
            continue
        if min(w, h) < MIN_EDGE:
            drop("small")
            continue
        if max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
            drop("panorama")
            continue
        if _near(hsh, hashes):
            drop("duplicate")
            continue
        shrunk = shrink_bytes(raw, max_edge=EDGE, max_bytes=MAX_BYTES, force_format="JPEG")
        out_dir.mkdir(parents=True, exist_ok=True)
        kept += 1
        (out_dir / f"{kept:02d}.jpg").write_bytes(shrunk.data)
        hashes.append(hsh)
        if pause_s:
            time.sleep(pause_s)
    return kept, dropped


def cover_hashes(vault_root: Path, item: BankItem) -> list[int]:
    if not item.image:
        return []
    p = vault_root / item.image
    try:
        with Image.open(p) as im:
            return [ahash(im)]
    except (OSError, ValueError):
        return []


def plan_and_fetch(
    bank: VisitBank,
    vault_root: Path,
    provider: Any,
    home: tuple[float, float],
    fetch: Callable[[str], bytes],
    *,
    kind: BankKind | None = None,
    ids: set[str] | None = None,
    per_item: int = PER_ITEM,
    refresh: bool = False,
    dry_run: bool = False,
    pause_s: float = 0.25,
) -> GalleryRun:
    """Resolve + fetch galleries for the bank's items. An item that already has a gallery folder
    is left alone unless `refresh`. `dry_run` resolves only from the sidecar and cache and counts
    the searches a real run would spend (two per unresolved item, one per resolved)."""
    from harness.travel.providers.food_provider import normalize_name

    folder = vault_root / Path(bank.doc).parent
    sidecar = read_sidecar(folder)
    known = provider.known_places()
    run = GalleryRun(visit=bank.visit)
    for item in bank.items:
        if (kind and item.kind != kind) or (ids and item.id not in ids):
            continue
        res = ItemResult(id=item.id, label=item.label)
        run.items.append(res)
        if not wants_gallery(item):
            res.source, res.note = "skipped", "gallery: false"
            continue
        if gallery_files(folder, item.id) and not refresh:
            res.source, res.note = "skipped", "gallery exists"
            res.kept = len(gallery_files(folder, item.id))
            continue
        place = sidecar.get(item.id)
        res.source = "sidecar"
        if not place:
            place = known.get(normalize_name(item.label))
            res.source = "cache"
        if not place:
            res.source = "search"
            run.searches_planned += 1
            if dry_run:
                run.searches_planned += 1  # + the gallery
                continue
            before = provider.search_count
            place = provider.lookup_place(place_query(item), home[0], home[1], refresh=refresh)
            run.searches_spent += provider.search_count - before
            if place is None:
                res.note = "no Maps match"
                continue
        res.place = str(place.get("title") or "")
        run.searches_planned += 1  # the gallery
        if dry_run:
            continue
        sidecar[item.id] = {**place, "query": place_query(item), "resolved": date.today().isoformat()}
        write_sidecar(folder, sidecar)
        before = provider.search_count
        urls = provider.place_gallery(str(place["data_id"]), refresh=refresh)
        run.searches_spent += provider.search_count - before
        out_dir = folder / GALLERY_DIR / item.id
        if refresh and out_dir.is_dir():
            for old in gallery_files(folder, item.id):
                old.unlink()
        res.kept, res.dropped = store_gallery(
            urls, out_dir, fetch, limit=per_item, seen=cover_hashes(vault_root, item), pause_s=pause_s,
        )
        if not urls:
            res.note = "gallery empty"
    return run
