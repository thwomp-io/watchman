"""`hn init` scaffolds a complete, valid-empty corpus — the clone-and-run onboarding guarantee.

Pins (1) the expected structure ships, (2) the copy is non-destructive (never clobbers user content),
and (3) the config templates parse as valid-EMPTY (not null) so a fresh corpus reads gracefully instead
of crashing — the bug caught building this (bare `holdings:` → null → traceback).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.finance.config.settings import PORTFOLIO_PATH, Settings
from harness.scaffold import scaffold_corpus

KEY_FILES = [
    "README.md",
    "user_background.md",
    "finance/config/portfolio.yaml",
    "finance/networth-history.json",
    "role-hunt/watchlist.yml",
    "role-hunt/applications.yaml",
    "travel/config/weights.yaml",
]
KEY_DIRS = ["finance/research", "finance/market/takes", "role-hunt/discoveries", "travel/trips",
            "reports/sessions", "narratives"]


def test_scaffold_lays_down_the_expected_structure(tmp_path: Path) -> None:
    created, skipped = scaffold_corpus(tmp_path)
    assert skipped == []
    assert created  # something was written
    for f in KEY_FILES:
        assert (tmp_path / f).is_file(), f"scaffold missing {f}"
    for d in KEY_DIRS:
        assert (tmp_path / d).is_dir(), f"scaffold missing dir {d}"


def test_scaffold_is_non_destructive(tmp_path: Path) -> None:
    scaffold_corpus(tmp_path)
    real = tmp_path / "user_background.md"
    real.write_text("MY REAL CONTENT")  # simulate a filled-in corpus
    created, skipped = scaffold_corpus(tmp_path)  # re-run
    assert created == [], "re-run must not re-create existing files"
    assert "user_background.md" in skipped
    assert real.read_text() == "MY REAL CONTENT", "user content must be preserved"


def test_scaffold_configs_are_valid_empty_not_null(tmp_path: Path) -> None:
    # the bug guard: bare `holdings:` / `companies:` parse as null and crash the readers; they must be [].
    scaffold_corpus(tmp_path)
    pf = yaml.safe_load((tmp_path / "finance" / "config" / "portfolio.yaml").read_text())
    assert pf["holdings"] == [], "portfolio holdings must be a valid empty list, not null"
    wl = yaml.safe_load((tmp_path / "role-hunt" / "watchlist.yml").read_text())
    assert wl["companies"] == [], "watchlist companies must be a valid empty list, not null"


def test_finance_portfolio_prefers_tracker_resident(tmp_path: Path) -> None:
    # the additive finance-tracker-resident change: a scaffolded tracker portfolio shadows the packaged
    # default, but only when it exists (back-compat for an install with no tracker file).
    assert Settings(tracker_path=tmp_path, weights_pack=None).portfolio_path == PORTFOLIO_PATH
    cfg = tmp_path / "finance" / "config"
    cfg.mkdir(parents=True)
    (cfg / "portfolio.yaml").write_text("holdings: []\n")
    assert Settings(tracker_path=tmp_path, weights_pack=None).portfolio_path == cfg / "portfolio.yaml"
