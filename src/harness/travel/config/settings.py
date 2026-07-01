"""Runtime settings: API keys from env, tracker corpus path, packaged weights path.

`tracker_path` + the blank-falls-back behavior live in `harness.settings.BaseToolkitSettings`
(shared across lanes); this module adds the travel-specific keys + corpus path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from harness.settings import DEFAULT_TRACKER as _DEFAULT_TRACKER  # back-compat alias (tests import it)
from harness.settings import BaseToolkitSettings

# Packaged weights file lives next to this module.
WEIGHTS_PATH = Path(__file__).parent / "weights.yaml"

__all__ = ["WEIGHTS_PATH", "Settings", "get_settings", "_DEFAULT_TRACKER"]


class Settings(BaseToolkitSettings):
    """Environment-driven config. Each provider key is independently optional."""

    serpapi_key: str | None = None
    ticketmaster_key: str | None = None
    wsdot_api_key: str | None = None

    @property
    def weights_path(self) -> Path:
        """The travel weights seed: the active weight pack's `travel/weights.yaml` when a pack is
        loaded, else the packaged default. Unset pack -> the packaged default, unchanged."""
        return self.pack_file("travel", "weights.yaml") or WEIGHTS_PATH

    @property
    def travel_corpus_path(self) -> Path:
        """The travel corpus root: the active weight pack's `travel/` lane dir when a pack provides
        the travel lane (it holds the weights + destination/trip state), else `<tracker>/travel`.
        Unset pack -> `<tracker>/travel`, unchanged."""
        return self.pack_file("travel") or (self.tracker_path / "travel")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
