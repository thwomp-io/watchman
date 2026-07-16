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


# ————— the user overlay (harness.yaml) — one home for bespoke-install settings ————
# Per-lane `global_settings` blocks the engine consults so personal nouns never enter code or
# shipped defaults. Precedence mirrors the portfolio seed: pack > tracker-resident > packaged
# neutral template. Missing/invalid files degrade to {} — the overlay is always optional.

_PACKAGED_OVERLAY = Path(__file__).parent / "config" / "harness.yaml"


def overlay_path(settings: BaseToolkitSettings | None = None) -> Path:
    """The overlay file the current environment resolves to (pack > tracker > packaged)."""
    s = settings or BaseToolkitSettings()
    if s.weights_pack is not None:
        pack_copy = s.weights_pack / "config" / "harness.yaml"
        if pack_copy.is_file():
            return pack_copy
    tracker_copy = s.tracker_path.expanduser() / "config" / "harness.yaml"
    if tracker_copy.is_file():
        return tracker_copy
    return _PACKAGED_OVERLAY


def user_overlay(settings: BaseToolkitSettings | None = None) -> dict[str, object]:
    """The parsed overlay (whole document). {} on missing/unparseable — never raises."""
    import yaml

    path = overlay_path(settings)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def overlay_get(lane: str, *keys: str, default: object = None,
                settings: BaseToolkitSettings | None = None) -> object:
    """Walk `<lane>.global_settings.<keys…>`; None/missing at any hop → default.

    The one accessor lanes use — e.g. `overlay_get("finance", "fund_holdings", "query")`."""
    node: object = user_overlay(settings).get(lane, {})
    if isinstance(node, dict):
        node = node.get("global_settings", {})
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k)
    return default if node is None else node
