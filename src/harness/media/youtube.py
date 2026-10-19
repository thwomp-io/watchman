"""YouTube reader: metadata + transcript + keyframes, via yt-dlp (a pinned harness dependency).

The agent's vision is stills-only, and a YouTube page is JS-rendered (`wire read` gets a title and a
sponsor dek). This reads what a video SAYS (captions) and what it SHOWS (scene-change stills).

- Transcript: creator captions first, YouTube's auto-captions as the fallback, labeled which. The
  track is fetched as json3 (one event per caption line, no rolling duplicates like VTT has).
- Frames: the video-only stream at ≤720p (on-screen chart text stays legible), an ffmpeg
  scene-change pass on a downscaled copy picks the moments, full-size stills are cut at those
  times, and the video file is deleted.
- Read-only, no account, no cookies, no API key. yt-dlp keeps its own YouTube client headers (a
  custom User-Agent there breaks extraction); the caption fetch uses the harness UA.
- yt-dlp warns that YouTube extraction without a JavaScript runtime (deno) is deprecated. Captions
  and ≤720p streams work without one today (verified 2026-09-24); if extraction starts failing,
  installing deno is the fix yt-dlp names.

Only YouTube is wired. yt-dlp reads many sites; widening `video_id` + the error map is the
extension point.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.errors import ProviderError

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url: str) -> str:
    """The 11-character id from any YouTube URL form (watch, youtu.be, shorts, embed, live)."""
    u = urlparse(url.strip())
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    vid = ""
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                vid = parts[1]
    elif _ID.match(url.strip()):
        vid = url.strip()
    if not _ID.match(vid):
        raise ProviderError(f"not a YouTube video URL: {url} (only YouTube is wired)")
    return vid


@dataclass
class Cue:
    start: float  # seconds
    text: str


@dataclass
class Transcript:
    video_id: str
    title: str = ""
    channel: str = ""
    published: str = ""  # YYYY-MM-DD
    duration: int = 0  # seconds
    url: str = ""
    language: str = ""
    kind: str = ""  # creator | auto | none
    chapters: list[tuple[float, str]] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ————— pure pieces —————

_REASONS = [
    (r"private video", "the video is private"),
    (r"sign in to confirm your age|age-restricted|inappropriate for some users",
     "age-gated (needs a signed-in account; not supported)"),
    (r"not made this video available in your country|blocked it in your country|geo",
     "region-blocked"),
    (r"sign in to confirm you.re not a bot|confirm you are not a bot",
     "YouTube's bot wall refused this machine (try later)"),
    (r"members-only|join this channel", "members-only"),
    (r"has been removed|no longer available|video (is )?unavailable|does not exist",
     "the video is unavailable or removed"),
    (r"premieres in|live event will begin|is upcoming",
     "not published yet (an upcoming premiere or stream)"),
]


def reason(message: str) -> str:
    """yt-dlp's error text → a one-line reason (never a traceback)."""
    low = message.lower()
    for pattern, text in _REASONS:
        if re.search(pattern, low):
            return text
    line = re.sub(r"^error:\s*", "", message.strip().splitlines()[0] if message.strip() else "", flags=re.I)
    return re.sub(r"\[youtube\]\s*[\w-]+:\s*", "", line)[:200] or "extraction failed"


def pick_track(subtitles: dict[str, Any], auto: dict[str, Any]) -> tuple[str, str, str] | None:
    """(language, kind, json3 url) — creator English first, then auto (original English, English,
    any original-language track). None when the video has no captions at all."""

    def json3(tracks: Any) -> str | None:
        for t in tracks or []:
            if isinstance(t, dict) and t.get("ext") == "json3" and t.get("url"):
                return str(t["url"])
        return None

    for lang in sorted(subtitles, key=lambda k: (not k.startswith("en"), k != "en", k)):
        if lang.startswith("en") and (url := json3(subtitles[lang])):
            return lang, "creator", url
    for lang in ("en-orig", "en"):
        if lang in auto and (url := json3(auto[lang])):
            return lang, "auto", url
    for lang in sorted(auto):
        if lang.endswith("-orig") and (url := json3(auto[lang])):
            return lang, "auto", url
    for lang in sorted(subtitles):  # a creator track in another language beats nothing
        if url := json3(subtitles[lang]):
            return lang, "creator", url
    return None


def parse_json3(data: dict[str, Any]) -> list[Cue]:
    """YouTube's json3 caption format → cues (one per caption event with text)."""
    out: list[Cue] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs") or []
        text = "".join(str(s.get("utf8") or "") for s in segs)
        text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
        if text:
            out.append(Cue(start=float(ev.get("tStartMs") or 0) / 1000.0, text=text))
    return out


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def still_name(seconds: float) -> str:
    """'05m55s' / '1h02m07s' — zero-padded so a directory listing sorts in video order."""
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m:02d}m{sec:02d}s"


def paragraphs(cues: list[Cue], *, every: float = 30.0) -> list[tuple[float, str]]:
    """Cues joined into timestamped blocks of about `every` seconds (readable, still citable)."""
    out: list[tuple[float, str]] = []
    start, buf = None, []
    for c in cues:
        if start is None:
            start = c.start
        elif c.start - start >= every:
            out.append((start, " ".join(buf)))
            start, buf = c.start, []
        buf.append(c.text)
    if buf and start is not None:
        out.append((start, " ".join(buf)))
    return out


def render_markdown(t: Transcript) -> str:
    kind = {"creator": "creator captions", "auto": "YouTube auto-captions (machine speech-to-text: "
            "names and numbers can be misheard)", "none": "no captions"}[t.kind or "none"]
    lines = [
        f"# {t.title or t.video_id}", "",
        f"- Channel: {t.channel or '—'}",
        f"- Published: {t.published or '—'} · Length: {stamp(t.duration)}",
        f"- URL: {t.url}",
        f"- Transcript: {kind}" + (f" ({t.language})" if t.language else ""),
    ]
    lines += [f"- Note: {n}" for n in t.notes]
    if t.chapters:
        lines += ["", "## Chapters", ""] + [f"- [{stamp(s)}] {title}" for s, title in t.chapters]
    lines += ["", "## Transcript", ""]
    if not t.cues:
        lines.append("_This video has no captions (creator or automatic) — nothing to transcribe._")
    for start, text in paragraphs(t.cues):
        lines += [f"**[{stamp(start)}]** {text}", ""]
    return "\n".join(lines).rstrip() + "\n"


# ————— yt-dlp I/O —————


class _Log:
    """Collect yt-dlp warnings instead of printing them; keep the one that matters."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        pass


def _extract(url: str, *, download_to: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    import yt_dlp  # type: ignore[import-untyped]  # no stubs; the call surface is two methods

    log = _Log()
    opts: dict[str, Any] = {"quiet": True, "no_warnings": False, "logger": log, "noplaylist": True,
                            "skip_download": download_to is None}
    if download_to is not None:
        # video only, the smallest stream that keeps on-screen text legible
        opts["format"] = "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/b"
        opts["outtmpl"] = str(download_to / "%(id)s.%(ext)s")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=download_to is not None)
    except Exception as e:  # noqa: BLE001 — yt-dlp raises many types; all become one line
        raise ProviderError(f"YouTube: {reason(str(e))}") from None
    notes = []
    if any("JavaScript runtime" in w for w in log.warnings):
        notes.append("yt-dlp ran without a JS runtime (deprecated by YouTube; install deno if "
                     "extraction starts failing)")
    return dict(info or {}), notes


def read_transcript(url: str) -> Transcript:
    import httpx

    from harness._http import USER_AGENT

    vid = video_id(url)
    info, notes = _extract(f"https://www.youtube.com/watch?v={vid}")
    up = str(info.get("upload_date") or "")
    t = Transcript(
        video_id=vid, title=str(info.get("title") or ""),
        channel=str(info.get("channel") or info.get("uploader") or ""),
        published=f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else "",
        duration=int(info.get("duration") or 0), url=f"https://youtu.be/{vid}", notes=notes,
        chapters=[(float(c.get("start_time") or 0), str(c.get("title") or ""))
                  for c in info.get("chapters") or []],
    )
    track = pick_track(info.get("subtitles") or {}, info.get("automatic_captions") or {})
    if track is None:
        t.kind = "none"
        return t
    t.language, t.kind, cap_url = track
    try:
        resp = httpx.get(cap_url, headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        t.cues = parse_json3(resp.json())
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"YouTube captions: download failed ({type(e).__name__})") from None
    return t


# ————— frames —————


@dataclass
class Frames:
    video_id: str
    title: str
    out_dir: Path
    stills: list[Path] = field(default_factory=list)
    sheet: Path | None = None
    scene_changes: int = 0
    notes: list[str] = field(default_factory=list)


def choose_times(scenes: list[float], duration: float, max_n: int, *, min_gap: float = 4.0) -> list[float]:
    """Pure: scene-change times → at most `max_n` stills, spread across the video.

    Scene cuts closer than `min_gap` seconds collapse (a transition fires several). Too many → an
    even subsample; too few (a talking head with no cuts) → evenly spaced times fill the gap."""
    picked: list[float] = []
    for s in sorted(scenes):
        if not picked or s - picked[-1] >= min_gap:
            picked.append(s)
    if len(picked) > max_n:
        step = len(picked) / max_n
        picked = [picked[int(i * step)] for i in range(max_n)]
    if len(picked) < max_n // 2 and duration > 0:
        n = max_n - len(picked)
        even = [duration * (i + 0.5) / n for i in range(n)]
        picked = sorted(set(round(x, 1) for x in picked + even))[:max_n]
    return picked


_PTS = re.compile(r"pts_time:([0-9.]+)")


def scene_times(video: Path, *, threshold: float = 0.3) -> list[float]:
    """ffmpeg scene-change pass on a downscaled copy (fast); the cut times in seconds."""
    from harness.paths import which_tool

    ffmpeg = which_tool("ffmpeg")
    if not ffmpeg:
        raise ProviderError("ffmpeg not found (brew install ffmpeg)")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video), "-an",
         "-vf", f"scale=320:-1,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    return [float(m.group(1)) for m in _PTS.finditer(proc.stderr)]


def cut_still(video: Path, at: float, out: Path) -> bool:
    from harness.paths import which_tool

    ffmpeg = which_tool("ffmpeg") or "ffmpeg"
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", str(out)],
        capture_output=True, check=False,
    )
    return proc.returncode == 0 and out.exists()


def read_frames(url: str, out_root: Path, *, max_n: int = 16) -> Frames:
    from harness.travel.media import build_contact_sheet

    vid = video_id(url)
    out_dir = out_root / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    info, notes = _extract(f"https://www.youtube.com/watch?v={vid}", download_to=out_dir)
    video = next((p for p in out_dir.glob(f"{vid}.*") if p.suffix in (".mp4", ".webm", ".mkv")), None)
    if video is None:
        raise ProviderError("YouTube: the download produced no video file")
    fr = Frames(video_id=vid, title=str(info.get("title") or ""), out_dir=out_dir, notes=notes)
    try:
        scenes = scene_times(video)
        fr.scene_changes = len(scenes)
        for old in out_dir.glob("*.png"):
            old.unlink()  # a re-run replaces the set
        for at in choose_times(scenes, float(info.get("duration") or 0), max_n):
            still = out_dir / f"{still_name(at)}.png"
            if cut_still(video, at, still):
                fr.stills.append(still)
    finally:
        video.unlink(missing_ok=True)
    if fr.stills:
        fr.sheet = build_contact_sheet(fr.stills, out_root / f"{vid}-sheet.png", cols=4, thumb_w=360)
    return fr
