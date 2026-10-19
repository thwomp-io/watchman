"""The backup lane — age-encrypted offsite snapshots of the operator's corpus + service state.

Why a lane, not a script: the corpus's authoritative copy lives on one machine (repos, local
databases, service state, the harness config/state), and that machine may go dark on a schedule.
An offsite copy has to be produced by a standing job, and a standing job is a toolkit verb —
deterministic, overlay-driven, testable, bus-reporting. Everything the job reads comes from the
`backup:` overlay block; no personal path lives in this module.

What one snapshot holds (a single `backup-<stamp>.tar.age` in the sync folder + two sidecars):
  repos/<slug>.bundle      `git bundle --all` — full history; gitignored dropzones stay out by construction
  sqlite/<slug>.sqlite     consistent copies via sqlite3's online-backup API (WAL-safe, no lock held)
  dirs/<slug>.tar          plain tars of config/state/service dirs, minus the `exclude` patterns
  MANIFEST.txt / .json     what's inside, sizes, repo heads, host + time
The work tree is tarred once and encrypted with `age -r <recipient>` BEFORE it touches the sync
folder — the sync provider's own encryption is the second layer, never the only one. The private
identity is never on this machine; the job holds only the public recipient.

Restore needs no harness: `age -d` + `tar -x` + `git clone <bundle>` (the operator runbook's
restore script). The only subprocesses here are `git` and `age`.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from harness.bus.store import harness_state_dir
from harness.paths import TOOL_DIRS, which_tool
from harness.settings import BaseToolkitSettings, overlay_get

LANE = "backup"
PRODUCER = "backup.snapshot"
SNAPSHOT_SUFFIX = ".tar.age"
SNAPSHOT_GLOB = f"backup-*{SNAPSHOT_SUFFIX}"
AGE_HEADER = b"age-encryption.org/v1"
RUN_LOG = "backup-json.log"


class BackupConfigError(ValueError):
    """The overlay is missing a value the job cannot proceed without."""


class BackupConfig(BaseModel):
    """The `backup.global_settings` overlay block, resolved. `sync_dir` + `recipient` are the two
    required values; everything else has a neutral default."""

    sync_dir: Path
    recipient: str
    subdir: str = "backup"
    keep: int = 8
    max_age_days: int = 9
    repos: list[Path] = Field(default_factory=list)
    dirs: list[Path] = Field(default_factory=list)
    sqlite: list[Path] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    sync_process: str | None = None

    @property
    def snapshot_dir(self) -> Path:
        return self.sync_dir / self.subdir


def _paths(value: object) -> list[Path]:
    if not isinstance(value, list):
        return []
    return [Path(str(p)).expanduser() for p in value]


def _strs(value: object) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def load_config(settings: BaseToolkitSettings | None = None) -> BackupConfig:
    """Read the `backup:` overlay block. Raises BackupConfigError with the exact keys to set."""

    def get(*keys: str, default: object = None) -> object:
        return overlay_get(LANE, *keys, default=default, settings=settings)

    sync_dir, recipient = get("sync_dir"), get("recipient")
    missing = [k for k, v in (("sync_dir", sync_dir), ("recipient", recipient)) if not v]
    if missing:
        raise BackupConfigError(
            f"backup overlay incomplete — set {', '.join(missing)} under `backup.global_settings` "
            "in config/harness.yaml (the tracker-resident copy)"
        )
    if not str(recipient).startswith("age1"):
        raise BackupConfigError(
            "backup.recipient must be an age PUBLIC key (age1…) — the private identity never belongs here"
        )
    proc = get("sync_process")
    return BackupConfig(
        sync_dir=Path(str(sync_dir)).expanduser(),
        recipient=str(recipient),
        subdir=str(get("subdir", default="backup")),
        keep=int(str(get("keep", default=8))),
        max_age_days=int(str(get("max_age_days", default=9))),
        repos=_paths(get("repos", default=[])),
        dirs=_paths(get("dirs", default=[])),
        sqlite=_paths(get("sqlite", default=[])),
        exclude=_strs(get("exclude", default=[])),
        sync_process=str(proc) if proc else None,
    )


# ————— building the work tree —————


def slug_for(path: Path) -> str:
    """A filesystem-safe, human-readable name for a source path (`/a/b c` → `a__b_c`). Absolute,
    so two sources with the same basename never collide inside one snapshot."""
    text = str(path).strip("/")
    return text.replace("/", "__").replace(" ", "_").replace("@", "_at_") or "root"


def excluded(rel: str, patterns: list[str]) -> bool:
    """True when any pattern matches the relative path, its basename, or any leading sub-path —
    so `.beads/backup` skips a whole subtree and `*.log` skips by name anywhere."""
    parts = rel.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return any(
        fnmatch.fnmatch(rel, pat)
        or fnmatch.fnmatch(parts[-1], pat)
        or any(fnmatch.fnmatch(p, pat) for p in prefixes)
        for pat in patterns
    )


class ManifestEntry(BaseModel):
    kind: str  # repo | sqlite | dir
    source: str
    artifact: str
    bytes: int
    note: str = ""


class Manifest(BaseModel):
    created: str
    host: str
    entries: list[ManifestEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.bytes for e in self.entries)

    def text(self) -> str:
        lines = [f"backup manifest · {self.created} · {self.host}", ""]
        for e in self.entries:
            note = f"  ({e.note})" if e.note else ""
            lines.append(f"{e.kind:<6} {e.bytes:>12,}  {e.artifact}  ← {e.source}{note}")
        lines += ["", f"total {self.total_bytes:,} bytes across {len(self.entries)} artifacts"]
        if self.warnings:
            lines += ["", "warnings:"] + [f"  - {w}" for w in self.warnings]
        return "\n".join(lines) + "\n"


def bundle_repo(repo: Path, dest: Path) -> str:
    """`git bundle create --all` + verify; returns the short HEAD for the manifest."""
    run = [which_tool("git") or "git", "-C", str(repo)]
    subprocess.run([*run, "bundle", "create", str(dest), "--all"], check=True, capture_output=True, text=True)
    # verify checks prerequisites against the CURRENT repo, so it must run inside one (a
    # scheduler's cwd is not a checkout)
    subprocess.run([*run, "bundle", "verify", str(dest)], check=True, capture_output=True, text=True)
    head = subprocess.run([*run, "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True)
    return head.stdout.strip()


def snapshot_sqlite(src: Path, dest: Path) -> None:
    """A consistent copy through the online-backup API — safe against a writer mid-transaction
    and WAL files, unlike a plain file copy."""
    with (
        contextlib.closing(sqlite3.connect(f"file:{src}?mode=ro", uri=True)) as s,
        contextlib.closing(sqlite3.connect(dest)) as d,
    ):
        s.backup(d)


def tar_dir(src: Path, dest: Path, exclude: list[str]) -> tuple[int, list[str]]:
    """Tar a directory minus excludes; symlinks + sockets + unreadable entries are skipped
    (e.g. symlinked dirs, a daemon's socket). Returns (files added, warnings)."""
    count, warnings = 0, []
    with tarfile.open(dest, "w") as tf:
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src).as_posix()
            if excluded(rel, exclude) or p.is_symlink() or not (p.is_file() or p.is_dir()):
                continue
            try:
                tf.add(p, arcname=f"{src.name}/{rel}", recursive=False)
            except OSError as exc:  # unreadable entry: note it, keep going
                warnings.append(f"{src}/{rel}: {exc.strerror or exc}")
                continue
            if p.is_file():
                count += 1
    return count, warnings


def build_snapshot(cfg: BackupConfig, work: Path, now: datetime | None = None) -> Manifest:
    """Populate `work` with repos/ sqlite/ dirs/ + the manifest. Missing sources are warnings, not
    failures — one absent optional dir must never cost the whole snapshot."""
    when = now or datetime.now().astimezone()
    manifest = Manifest(created=when.isoformat(timespec="seconds"), host=platform.node())
    for sub in ("repos", "sqlite", "dirs"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    for repo in cfg.repos:
        if not (repo / ".git").exists():
            manifest.warnings.append(f"repo missing or not a git checkout: {repo}")
            continue
        dest = work / "repos" / f"{slug_for(repo)}.bundle"
        head = bundle_repo(repo, dest)
        manifest.entries.append(
            ManifestEntry(
                kind="repo",
                source=str(repo),
                artifact=f"repos/{dest.name}",
                bytes=dest.stat().st_size,
                note=f"HEAD {head}",
            )
        )

    for db in cfg.sqlite:
        if not db.is_file():
            manifest.warnings.append(f"sqlite missing: {db}")
            continue
        dest = work / "sqlite" / f"{slug_for(db.with_suffix(''))}.sqlite"
        snapshot_sqlite(db, dest)
        manifest.entries.append(
            ManifestEntry(
                kind="sqlite", source=str(db), artifact=f"sqlite/{dest.name}", bytes=dest.stat().st_size
            )
        )

    for d in cfg.dirs:
        if not d.is_dir():
            manifest.warnings.append(f"dir missing: {d}")
            continue
        dest = work / "dirs" / f"{slug_for(d)}.tar"
        files, warns = tar_dir(d, dest, cfg.exclude)
        manifest.warnings.extend(warns)
        manifest.entries.append(
            ManifestEntry(
                kind="dir",
                source=str(d),
                artifact=f"dirs/{dest.name}",
                bytes=dest.stat().st_size,
                note=f"{files} files",
            )
        )

    (work / "MANIFEST.txt").write_text(manifest.text(), encoding="utf-8")
    (work / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


# ————— sealing + landing + retention —————


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp_for(now: datetime) -> str:
    return now.strftime("%Y-%m-%d-%H%M")


def seal_and_land(work: Path, cfg: BackupConfig, stamp: str, age_bin: str = "age") -> Path:
    """Tar the work tree, encrypt it to the recipient, then MOVE the sealed file into the sync
    folder (plaintext never enters the synced tree). Writes the `.sha256` + `.manifest.txt`
    sidecars beside it. Returns the sealed snapshot path."""
    cfg.snapshot_dir.mkdir(parents=True, exist_ok=True)
    scratch = work.parent
    tar_path = scratch / f"backup-{stamp}.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(work, arcname=".")
    sealed_tmp = scratch / f"backup-{stamp}{SNAPSHOT_SUFFIX}"
    subprocess.run(
        [age_bin, "-r", cfg.recipient, "-o", str(sealed_tmp), str(tar_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    tar_path.unlink()
    final = cfg.snapshot_dir / sealed_tmp.name
    shutil.move(str(sealed_tmp), str(final))
    (cfg.snapshot_dir / f"backup-{stamp}.sha256").write_text(
        f"{sha256_of(final)}  {final.name}\n", encoding="utf-8"
    )
    shutil.copyfile(work / "MANIFEST.txt", cfg.snapshot_dir / f"backup-{stamp}.manifest.txt")
    return final


def sidecars(snapshot: Path) -> list[Path]:
    stem = snapshot.name[: -len(SNAPSHOT_SUFFIX)]
    return [snapshot.parent / f"{stem}.sha256", snapshot.parent / f"{stem}.manifest.txt"]


def prune(snapshot_dir: Path, keep: int) -> list[Path]:
    """Drop the oldest snapshots (and their sidecars) past `keep`; stamps sort lexically = by time."""
    removed: list[Path] = []
    if keep <= 0:
        return removed
    for snap in sorted(snapshot_dir.glob(SNAPSHOT_GLOB))[:-keep]:
        for p in (snap, *sidecars(snap)):
            if p.exists():
                p.unlink()
                removed.append(p)
    return removed


# ————— the run + its record —————


class BackupResult(BaseModel):
    snapshot: str
    bytes: int
    seconds: float
    entries: int
    warnings: list[str] = Field(default_factory=list)
    pruned: list[str] = Field(default_factory=list)


def run_log_path() -> Path:
    return harness_state_dir() / RUN_LOG


def _append_run_log(record: dict[str, object]) -> None:
    path = run_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def last_run() -> dict[str, object] | None:
    path = run_log_path()
    if not path.is_file():
        return None
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        loaded = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _publish(ok: bool, title: str, body: str, stamp: str) -> None:
    from harness.bus.models import EventDraft
    from harness.bus.service import BusService

    BusService().publish(
        EventDraft(
            producer=PRODUCER,
            lane="core",
            kind="backup_snapshot",
            subject="backup",
            title=title,
            body=body,
            severity="info" if ok else "alert",
            idempotency_key=f"{PRODUCER}:{'ok' if ok else 'fail'}:{stamp}",
        )
    )


def plan(cfg: BackupConfig) -> list[tuple[str, str, bool]]:
    """What a run would read, with an existence check — the `--dry-run` surface."""
    rows: list[tuple[str, str, bool]] = [("repo", str(r), (r / ".git").exists()) for r in cfg.repos]
    rows += [("sqlite", str(d), d.is_file()) for d in cfg.sqlite]
    rows += [("dir", str(d), d.is_dir()) for d in cfg.dirs]
    rows.append(("dest", str(cfg.snapshot_dir), cfg.sync_dir.is_dir()))
    return rows


def run_backup(
    cfg: BackupConfig, now: datetime | None = None, notify: bool = False, age_bin: str = "age"
) -> BackupResult:
    """The whole job: build → seal → land → prune → log (+ bus). A failure is logged + published
    as an ALERT (when notify) and re-raised, so launchd's exit code and the Inbox both say so."""
    when = now or datetime.now().astimezone()
    stamp = stamp_for(when)
    t0 = time.monotonic()
    try:
        # Preflight INSIDE the try: a missing tool must reach the run log + the ALERT like any
        # other failure, or an unattended run fails with no log line and no Inbox event.
        resolved_age = which_tool(age_bin)
        if resolved_age is None:
            raise BackupConfigError(f"`{age_bin}` not found (PATH + {', '.join(TOOL_DIRS)}) — "
                                    "install it (e.g. `brew install age`)")
        age_bin = resolved_age
        with tempfile.TemporaryDirectory(prefix="harness-backup-") as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            manifest = build_snapshot(cfg, work, when)
            sealed = seal_and_land(work, cfg, stamp, age_bin=age_bin)
        pruned = prune(cfg.snapshot_dir, cfg.keep)
        result = BackupResult(
            snapshot=str(sealed),
            bytes=sealed.stat().st_size,
            seconds=round(time.monotonic() - t0, 1),
            entries=len(manifest.entries),
            warnings=manifest.warnings,
            pruned=[p.name for p in pruned],
        )
    except Exception as exc:
        _append_run_log({"ts": when.isoformat(timespec="seconds"), "status": "fail", "error": str(exc)})
        if notify:
            _publish(False, "backup snapshot FAILED", str(exc)[:400], stamp)
        raise
    _append_run_log(
        {
            "ts": when.isoformat(timespec="seconds"),
            "status": "ok",
            "snapshot": sealed.name,
            "bytes": result.bytes,
            "seconds": result.seconds,
            "entries": result.entries,
            "warnings": len(result.warnings),
        }
    )
    if notify:
        mb = result.bytes / 1_048_576
        _publish(
            True,
            f"backup snapshot landed · {mb:,.0f} MB",
            f"{sealed.name} · {result.entries} artifacts · {result.seconds}s",
            stamp,
        )
    return result


# ————— status (the deterministic health read) —————


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


class SnapshotInfo(BaseModel):
    name: str
    bytes: int
    age_days: float
    sha_ok: bool | None  # None = no sidecar to check against
    header_ok: bool


class BackupStatus(BaseModel):
    ok: bool
    checks: list[Check]
    newest: SnapshotInfo | None
    count: int
    total_bytes: int
    last_run: dict[str, object] | None
    snapshot_dir: str


def _process_running(name: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0
    except OSError:
        return False


def inspect_snapshot(snap: Path, now: datetime) -> SnapshotInfo:
    mtime = datetime.fromtimestamp(snap.stat().st_mtime).astimezone()
    sha_side = sidecars(snap)[0]
    sha_ok: bool | None = None
    if sha_side.is_file():
        recorded = sha_side.read_text(encoding="utf-8").split()[0]
        sha_ok = recorded == sha256_of(snap)
    with snap.open("rb") as f:
        header_ok = f.read(len(AGE_HEADER)) == AGE_HEADER
    return SnapshotInfo(
        name=snap.name,
        bytes=snap.stat().st_size,
        age_days=round((now - mtime).total_seconds() / 86400, 2),
        sha_ok=sha_ok,
        header_ok=header_ok,
    )


def status(cfg: BackupConfig, now: datetime | None = None, age_bin: str = "age") -> BackupStatus:
    when = now or datetime.now().astimezone()
    checks: list[Check] = []
    checks.append(Check(name="age installed", ok=which_tool(age_bin) is not None, fix="brew install age"))
    checks.append(
        Check(
            name="recipient is a public key",
            ok=cfg.recipient.startswith("age1"),
            detail=cfg.recipient[:16] + "…",
        )
    )
    checks.append(
        Check(
            name="sync folder present",
            ok=cfg.sync_dir.is_dir(),
            detail=str(cfg.sync_dir),
            fix="open the sync app and sign in",
        )
    )
    if cfg.sync_process:
        checks.append(
            Check(
                name=f"sync app running ({cfg.sync_process})",
                ok=_process_running(cfg.sync_process),
                fix=f"open -a '{cfg.sync_process}' — and make it a login item",
            )
        )
    snaps = sorted(cfg.snapshot_dir.glob(SNAPSHOT_GLOB)) if cfg.snapshot_dir.is_dir() else []
    newest = inspect_snapshot(snaps[-1], when) if snaps else None
    if newest is None:
        checks.append(Check(name="a snapshot exists", ok=False, fix="hn backup snapshot"))
    else:
        checks.append(
            Check(
                name=f"newest snapshot fresh (< {cfg.max_age_days}d)",
                ok=newest.age_days < cfg.max_age_days,
                detail=f"{newest.name} · {newest.age_days}d",
                fix="hn backup snapshot; check the run log",
            )
        )
        checks.append(Check(name="sha256 sidecar matches", ok=bool(newest.sha_ok), fix="hn backup snapshot"))
        checks.append(
            Check(
                name="age header present", ok=newest.header_ok, fix="the file is not an age archive — re-run"
            )
        )
    last = last_run()
    if last is not None:
        checks.append(
            Check(
                name="last run succeeded",
                ok=last.get("status") == "ok",
                detail=str(last.get("ts", "")),
                fix=str(last.get("error", ""))[:120],
            )
        )
    total = sum(s.stat().st_size for s in snaps)
    return BackupStatus(
        ok=all(c.ok for c in checks),
        checks=checks,
        newest=newest,
        count=len(snaps),
        total_bytes=total,
        last_run=last,
        snapshot_dir=str(cfg.snapshot_dir),
    )
