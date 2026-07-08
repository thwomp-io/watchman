"""Shared-core primitives — direct contract coverage.

These were extracted from the travel lane to `harness.*` for cross-lane reuse; the lane suites cover
them transitively, but the shared contract deserves its own focused tests.
"""

from __future__ import annotations

from pathlib import Path

from harness.corpus import section, split_sections
from harness.errors import ProviderError
from harness.settings import DEFAULT_TRACKER, BaseToolkitSettings


def test_split_sections_preamble_and_h2() -> None:
    body = "intro line\n\n## Window\n- 3/10 → 3/12\n\n## Lodging anchors\n- **Hotel X**"
    secs = split_sections(body)
    assert secs["_preamble"] == "intro line"
    assert "3/10" in secs["Window"]
    assert "Hotel X" in secs["Lodging anchors"]


def test_section_prefix_match_is_case_insensitive() -> None:
    secs = {"_preamble": "", "Trip-shape": "beach + walkable", "Window": "x"}
    assert section(secs, "trip-shape") == "beach + walkable"
    assert section(secs, "WINDOW") == "x"
    assert section(secs, "nonexistent") == ""


def test_base_settings_blank_tracker_path_falls_back() -> None:
    assert BaseToolkitSettings(tracker_path="").tracker_path == DEFAULT_TRACKER  # type: ignore[arg-type]
    assert BaseToolkitSettings(tracker_path="   ").tracker_path == DEFAULT_TRACKER  # type: ignore[arg-type]
    assert BaseToolkitSettings(tracker_path="/tmp/t").tracker_path == Path("/tmp/t")  # type: ignore[arg-type]


def test_provider_error_is_runtime_error() -> None:
    assert issubclass(ProviderError, RuntimeError)


def test_lane_provider_errors_are_the_shared_one() -> None:
    # The re-export shims must point at the single shared class (so `except ProviderError` is
    # interchangeable across lanes).
    from harness.finance.providers.base import ProviderError as FinErr
    from harness.travel.providers.base import ProviderError as TravErr

    assert FinErr is ProviderError
    assert TravErr is ProviderError
