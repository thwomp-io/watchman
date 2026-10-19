"""hn media: URL parsing, caption choice, json3 parse, frames selection, honest failures.

The json3 events mirror a real auto-caption track (9/24 specimen): one event per caption line,
`segs` of words with leading spaces, newline-only events between lines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from harness.errors import ProviderError
from harness.media import youtube as yt


@pytest.mark.parametrize("url", [
    "https://youtu.be/AbCdEfGhIjK", "https://youtu.be/AbCdEfGhIjK?si=abc&t=30",
    "https://www.youtube.com/watch?v=AbCdEfGhIjK&list=PL1", "https://m.youtube.com/watch?v=AbCdEfGhIjK",
    "https://www.youtube.com/shorts/AbCdEfGhIjK", "https://www.youtube.com/embed/AbCdEfGhIjK",
    "https://www.youtube.com/live/AbCdEfGhIjK?feature=share", "AbCdEfGhIjK",
])
def test_every_youtube_url_form_yields_the_id(url: str) -> None:
    assert yt.video_id(url) == "AbCdEfGhIjK"


@pytest.mark.parametrize("url", ["https://vimeo.com/123", "https://www.youtube.com/@ExampleChannel",
                                 "https://www.youtube.com/watch?v=short"])
def test_non_video_urls_are_refused_by_name(url: str) -> None:
    with pytest.raises(ProviderError, match="not a YouTube video URL"):
        yt.video_id(url)


@pytest.mark.parametrize("msg,want", [
    ("ERROR: [youtube] aaaaaaaaaaa: This video is unavailable", "unavailable or removed"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate", "age-gated"),
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "bot wall"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country", "region"),
    ("ERROR: [youtube] x: Something new", "Something new"),
])
def test_errors_become_one_line_reasons(msg: str, want: str) -> None:
    assert want in yt.reason(msg) and "\n" not in yt.reason(msg)


def _t(ext: str = "json3", url: str = "u") -> list[dict[str, str]]:
    return [{"ext": "vtt", "url": "v"}, {"ext": ext, "url": url}]


def test_creator_captions_beat_auto_and_english_beats_other_languages() -> None:
    assert yt.pick_track({"en-US": _t(url="c")}, {"en": _t(url="a")}) == ("en-US", "creator", "c")
    assert yt.pick_track({"de": _t(url="d")}, {"en-orig": _t(url="o"), "en": _t(url="a")}) == \
        ("en-orig", "auto", "o")
    assert yt.pick_track({}, {"fr-orig": _t(url="f"), "de": _t(url="x")}) == ("fr-orig", "auto", "f")
    assert yt.pick_track({"de": _t(url="d")}, {}) == ("de", "creator", "d")
    assert yt.pick_track({}, {}) is None


_JSON3 = {"events": [
    {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Last"}, {"utf8": " week,"}, {"utf8": " the"}]},
    {"tStartMs": 1200, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 2000, "segs": [{"utf8": "Federal"}, {"utf8": " Reserve"}]},
    {"tStartMs": 31000, "segs": [{"utf8": "So what changed?"}]},
    {"tStartMs": 33000},
]}


def test_json3_parses_to_cues_and_paragraphs() -> None:
    cues = yt.parse_json3(_JSON3)
    assert [(c.start, c.text) for c in cues] == [(0.0, "Last week, the"), (2.0, "Federal Reserve"),
                                                 (31.0, "So what changed?")]
    assert yt.paragraphs(cues) == [(0.0, "Last week, the Federal Reserve"), (31.0, "So what changed?")]


def test_markdown_labels_auto_captions_and_a_captionless_video() -> None:
    t = yt.Transcript(video_id="AbCdEfGhIjK", title="T", channel="C", published="2030-03-04", duration=828,
                      url="https://youtu.be/AbCdEfGhIjK", language="en-orig", kind="auto",
                      cues=yt.parse_json3(_JSON3), chapters=[(0.0, "Intro")])
    md = yt.render_markdown(t)
    assert "auto-captions" in md and "Length: 13:48" in md and "**[0:31]** So what changed?" in md
    assert "- [0:00] Intro" in md
    none = yt.render_markdown(yt.Transcript(video_id="x", kind="none"))
    assert "no captions" in none


def test_frame_times_collapse_transitions_subsample_and_fill() -> None:
    assert yt.choose_times([10.0, 10.5, 11.0, 20.0], 100.0, 4) == [10.0, 20.0]  # 2 ≥ 4//2: kept as-is
    many = [float(i) * 5 for i in range(100)]
    got = yt.choose_times(many, 500.0, 10)
    assert len(got) == 10 and got == sorted(got) and got[0] == 0.0 and got[-1] > 400
    talking_head = yt.choose_times([], 600.0, 6)  # no cuts: evenly spaced
    assert talking_head == [50.0, 150.0, 250.0, 350.0, 450.0, 550.0]


def test_still_names_sort_in_video_order() -> None:
    names = [yt.still_name(s) for s in (4, 100, 653, 3727)]
    assert names == ["00m04s", "01m40s", "10m53s", "1h02m07s"] and sorted(names[:3]) == names[:3]


# ————— the verbs, offline —————

def test_read_transcript_assembles_metadata_and_the_chosen_track(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    info = {"title": "Sample Talk", "channel": "Example Channel", "upload_date": "20300304",
            "duration": 828, "subtitles": {}, "automatic_captions": {"en": _t(url="https://cap")}}
    monkeypatch.setattr(yt, "_extract", lambda url, download_to=None: (info, ["n"]))
    seen: dict[str, Any] = {}

    def get(url: str, **kw: Any) -> httpx.Response:
        seen["url"], seen["ua"] = url, kw["headers"]["User-Agent"]
        return httpx.Response(200, json=_JSON3, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    t = yt.read_transcript("https://youtu.be/AbCdEfGhIjK?si=x")
    assert (t.title, t.published, t.kind, t.language, len(t.cues)) == \
        ("Sample Talk", "2030-03-04", "auto", "en", 3)
    assert seen["url"] == "https://cap" and seen["ua"].startswith("harness/") and t.notes == ["n"]


def test_extract_turns_any_ytdlp_failure_into_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_dlp  # type: ignore[import-untyped]

    class Boom:
        def __init__(self, opts: Any) -> None:
            pass

        def __enter__(self) -> Boom:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def extract_info(self, url: str, download: bool) -> Any:
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Private video. Sign in\nTraceback…")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(ProviderError) as e:
        yt._extract("https://www.youtube.com/watch?v=AbCdEfGhIjK")
    assert str(e.value) == "YouTube: the video is private"


def test_transcript_verb_writes_the_file_and_exits_2_without_captions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from harness.cli import app

    t = yt.Transcript(video_id="AbCdEfGhIjK", title="T", kind="auto", cues=yt.parse_json3(_JSON3))
    monkeypatch.setattr(yt, "read_transcript", lambda url: t)
    res = CliRunner().invoke(app, ["media", "transcript", "AbCdEfGhIjK", "--out", str(tmp_path)])
    assert res.exit_code == 0 and "So what changed?" in (tmp_path / "AbCdEfGhIjK-transcript.md").read_text()
    monkeypatch.setattr(yt, "read_transcript", lambda url: yt.Transcript(video_id="AbCdEfGhIjK", kind="none"))
    again = CliRunner().invoke(app, ["media", "transcript", "AbCdEfGhIjK", "--out", str(tmp_path)])
    assert again.exit_code == 2


def test_frames_deletes_the_video_even_when_extraction_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def fake_extract(url: str, download_to: Path | None = None) -> tuple[dict[str, Any], list[str]]:
        assert download_to is not None
        (download_to / "AbCdEfGhIjK.mp4").write_bytes(b"x")
        return {"title": "T", "duration": 60}, []

    def boom(video: Path, threshold: float = 0.3) -> list[float]:
        raise ProviderError("ffmpeg not found")

    monkeypatch.setattr(yt, "_extract", fake_extract)
    monkeypatch.setattr(yt, "scene_times", boom)
    with pytest.raises(ProviderError):
        yt.read_frames("AbCdEfGhIjK", tmp_path)
    assert not (tmp_path / "AbCdEfGhIjK" / "AbCdEfGhIjK.mp4").exists()
