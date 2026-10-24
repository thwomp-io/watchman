"""Size-capped rotation for the logs nothing else bounds — launchd's `StandardOutPath` files and the
standing agents' JSON run logs (shell redirects), which grow forever because no process owns them.

Copy-truncate, not rename: a long-running launchd service keeps writing to the inode it was handed,
so a rename would leave it filling the rotated copy until its next restart. launchd opens those
files O_APPEND, so after an in-place truncate the writer simply continues at offset 0 (no sparse
hole). The cost is the lines written between the copy and the truncate — milliseconds, accepted.

Past `cap_mb`, `<log>` is gzipped to `<log>.1.gz` (older copies shift to `.2.gz`…, beyond `keep`
deleted) and truncated. Paths are globs from the overlay (`bus.global_settings.log_rotation`); the
default is the harness state dir's run logs. The weekly `hn bus purge --policy default` runs it;
`hn bus logs` shows where every log stands.
"""

from __future__ import annotations

import glob
import gzip
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from harness.settings import overlay_get

DEFAULT_CAP_MB = 5.0
DEFAULT_KEEP = 5


@dataclass
class LogStatus:
    path: str
    bytes: int
    copies: int  # rotated .N.gz copies on disk
    over_cap: bool


@dataclass
class RotationPolicy:
    globs: list[str]
    cap_bytes: int
    keep: int


def policy() -> RotationPolicy:
    from harness.bus.store import harness_state_dir

    raw = overlay_get("bus", "log_rotation", default={})
    cfg = raw if isinstance(raw, dict) else {}
    globs = cfg.get("paths") or [str(harness_state_dir() / "*.log")]
    cap_mb = float(cfg.get("cap_mb") or DEFAULT_CAP_MB)
    keep = int(cfg.get("keep") or DEFAULT_KEEP)
    return RotationPolicy([str(g) for g in globs], int(cap_mb * 1_048_576), max(1, keep))


def matched(pol: RotationPolicy) -> list[Path]:
    """The live logs the globs name (rotated `.gz` copies and non-files excluded), de-duplicated."""
    seen: dict[str, Path] = {}
    for pattern in pol.globs:
        for hit in glob.glob(os.path.expanduser(pattern)):
            p = Path(hit)
            if p.is_file() and not p.name.endswith(".gz"):
                seen.setdefault(str(p.resolve()), p)
    return sorted(seen.values())


def _copy(p: Path, n: int) -> Path:
    return p.with_name(f"{p.name}.{n}.gz")


def copies(p: Path) -> int:
    n = 0
    while _copy(p, n + 1).exists():
        n += 1
    return n


def status(pol: RotationPolicy | None = None) -> list[LogStatus]:
    pol = pol or policy()
    out = []
    for p in matched(pol):
        size = p.stat().st_size
        out.append(LogStatus(str(p), size, copies(p), size > pol.cap_bytes))
    return out


def rotate_file(p: Path, keep: int) -> None:
    """Shift the gz chain, gzip the current contents to `.1.gz`, truncate in place."""
    _copy(p, keep).unlink(missing_ok=True)
    for n in range(keep - 1, 0, -1):
        if _copy(p, n).exists():
            _copy(p, n).replace(_copy(p, n + 1))
    tmp = p.with_name(f"{p.name}.1.gz.tmp")
    with p.open("rb") as src, gzip.open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(_copy(p, 1))
    os.truncate(p, 0)
    # a chain longer than `keep` (keep was lowered) is trimmed too
    n = keep + 1
    while _copy(p, n).exists():
        _copy(p, n).unlink()
        n += 1


def rotate(*, dry_run: bool = False, pol: RotationPolicy | None = None) -> list[str]:
    """Rotate every matched log past the cap. Returns the paths rotated (or that would be)."""
    pol = pol or policy()
    done = []
    for p in matched(pol):
        if p.stat().st_size > pol.cap_bytes:
            if not dry_run:
                rotate_file(p, pol.keep)
            done.append(str(p))
    return done
