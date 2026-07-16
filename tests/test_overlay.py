"""The user overlay (harness.yaml): precedence + accessor + graceful degradation."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.settings import _PACKAGED_OVERLAY, BaseToolkitSettings, overlay_get, overlay_path, user_overlay


def _settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pack: Path | None = None
) -> BaseToolkitSettings:
    monkeypatch.setenv("TRACKER_PATH", str(tmp_path / "tracker"))
    if pack is not None:
        monkeypatch.setenv("WEIGHTS_PACK", str(pack))
    else:
        monkeypatch.delenv("WEIGHTS_PACK", raising=False)
    return BaseToolkitSettings()


def test_falls_back_to_the_packaged_neutral_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(tmp_path, monkeypatch)
    assert overlay_path(s) == _PACKAGED_OVERLAY
    # the shipped template is all nulls — every accessor answers the default
    assert overlay_get("finance", "brokerage", default="unset", settings=s) == "unset"


def test_tracker_resident_overlay_wins_over_packaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(tmp_path, monkeypatch)
    cfg = tmp_path / "tracker" / "config"
    cfg.mkdir(parents=True)
    (cfg / "harness.yaml").write_text(
        "finance:\n  global_settings:\n    brokerage: Example Broker\n"
        "    fund_holdings:\n      query: Acme Fund\n      cik: '0001234567'\n"
    )
    assert overlay_path(s) == cfg / "harness.yaml"
    assert overlay_get("finance", "brokerage", settings=s) == "Example Broker"
    assert overlay_get("finance", "fund_holdings", "cik", settings=s) == "0001234567"
    # a lane/key the file doesn't carry degrades to the default
    assert overlay_get("travel", "home_city", default=None, settings=s) is None


def test_pack_overlay_wins_over_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker_cfg = tmp_path / "tracker" / "config"
    tracker_cfg.mkdir(parents=True)
    (tracker_cfg / "harness.yaml").write_text("finance:\n  global_settings:\n    brokerage: RealCo\n")
    pack = tmp_path / "pack"
    (pack / "config").mkdir(parents=True)
    (pack / "config" / "harness.yaml").write_text("finance:\n  global_settings:\n    brokerage: DemoCo\n")
    s = _settings(tmp_path, monkeypatch, pack=pack)
    assert overlay_get("finance", "brokerage", settings=s) == "DemoCo"


def test_invalid_yaml_degrades_to_empty_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(tmp_path, monkeypatch)
    cfg = tmp_path / "tracker" / "config"
    cfg.mkdir(parents=True)
    (cfg / "harness.yaml").write_text("{: not yaml ::")
    assert user_overlay(s) == {}
    assert overlay_get("finance", "brokerage", default="fallback", settings=s) == "fallback"
