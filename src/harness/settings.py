"""Shared settings base across harness lanes.

Extracted at consolidation time: every lane's `config/settings.py` carried an
identical `tracker_path` field + the blank-falls-back validator + `_DEFAULT_TRACKER` + the `.env`
`model_config`. That common spine lives here now; each lane's `Settings` subclasses
`BaseToolkitSettings` and adds only its own provider keys + corpus-path properties.

`tracker_path` IS the corpus-root locate primitive — the one place every lane resolves the
corpus root (env-overridable `TRACKER_PATH`, else `~/projects/corpus`).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TRACKER = Path.home() / "projects" / "corpus"


def _env_files() -> tuple[str, ...]:
    """Where to find `.env`, so a globally-installed `hn` finds keys from ANY working directory
    (not just the repo — the global-CLI gap, cousin of the bus-app PATH fix). Priority order:
    `~/.config/harness/.env` (portable user-config home; multi-device-friendly) → the repo-root
    `.env` (resolved via the package location, so editable installs work from anywhere) → CWD `.env`
    (legacy / running from the repo). Missing files are skipped; OS env vars still override all."""
    candidates = (
        Path.home() / ".config" / "harness" / ".env",
        Path(__file__).resolve().parents[2] / ".env",  # repo root (editable install)
        Path(".env"),  # CWD
    )
    return tuple(str(p) for p in candidates)


class BaseToolkitSettings(BaseSettings):
    """Common env-driven config: the tracker corpus root + `.env` loading. Lanes subclass this."""

    model_config = SettingsConfigDict(
        env_file=_env_files(), env_file_encoding="utf-8", extra="ignore"
    )

    tracker_path: Path = Field(default_factory=lambda: DEFAULT_TRACKER)

    # An optional loaded "weight pack" — a portable bundle a user maintains + an app loads to drive a
    # scenario (the OSS-facing primitive). When set (`WEIGHTS_PACK`), lanes resolve their data from
    # `<pack>/<lane>/…` instead of their defaults. Unset (the default) → all lanes use their existing
    # paths, so this is purely additive: no pack changes nothing.
    weights_pack: Path | None = Field(default=None)

    @field_validator("tracker_path", mode="before")
    @classmethod
    def _empty_tracker_path_falls_back(cls, v: object) -> object:
        # A blank `TRACKER_PATH=` in .env would otherwise become a relative Path('.').
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_TRACKER
        return v

    @field_validator("weights_pack", mode="before")
    @classmethod
    def _empty_pack_is_none(cls, v: object) -> object:
        # A blank `WEIGHTS_PACK=` in .env means "no pack", not Path('.').
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    def pack_file(self, lane: str, *rel: str) -> Path | None:
        """Resolve a file inside the active weight pack's lane subdir, or None to fall back.

        Returns `<weights_pack>/<lane>/<rel…>` only when a pack is loaded AND it provides `lane`
        (a `<pack>/<lane>/` dir exists); otherwise None so the caller keeps its default path. The
        one place lane settings consult the pack — additive + non-breaking by construction."""
        if self.weights_pack is None:
            return None
        lane_dir = self.weights_pack / lane
        if not lane_dir.is_dir():
            return None
        return lane_dir.joinpath(*rel)
