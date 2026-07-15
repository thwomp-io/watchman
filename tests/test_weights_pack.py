"""Weight-pack loader resolution — the additive/non-breaking invariant.

The pack loader must be purely additive: with no pack loaded, every lane resolves exactly as before.
With a pack loaded, a lane resolves its data from `<pack>/<lane>/…` only when the pack provides that
lane (else it falls back). These tests pin both directions.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from harness.career.config import role_hunt_root
from harness.career.service import CareerService
from harness.finance.config.settings import PORTFOLIO_PATH
from harness.finance.config.settings import Settings as FinanceSettings
from harness.packs import PackGroup
from harness.settings import BaseToolkitSettings
from harness.travel.config.settings import WEIGHTS_PATH
from harness.travel.config.settings import Settings as TravelSettings


@pytest.fixture(autouse=True)
def _clean_pack_env() -> object:
    """The `--pack` callback sets WEIGHTS_PACK in os.environ directly (not via monkeypatch); keep it
    out of every test's environment so a pack from one test never leaks into another's get_settings."""
    os.environ.pop("WEIGHTS_PACK", None)
    yield
    os.environ.pop("WEIGHTS_PACK", None)


def test_pack_file_is_none_without_a_pack() -> None:
    s = BaseToolkitSettings(weights_pack=None)
    assert s.pack_file("finance", "portfolio.yaml") is None


def test_pack_file_is_none_when_pack_lacks_the_lane(tmp_path: Path) -> None:
    # A pack dir with no `finance/` subdir does not provide the finance lane -> fall back.
    assert BaseToolkitSettings(weights_pack=tmp_path).pack_file("finance", "portfolio.yaml") is None


def test_pack_file_resolves_when_the_lane_is_present(tmp_path: Path) -> None:
    (tmp_path / "finance").mkdir()
    s = BaseToolkitSettings(weights_pack=tmp_path)
    assert s.pack_file("finance", "portfolio.yaml") == tmp_path / "finance" / "portfolio.yaml"


def test_blank_pack_env_is_treated_as_none() -> None:
    # A blank `WEIGHTS_PACK=` must mean "no pack", not Path('.').
    assert BaseToolkitSettings(weights_pack="").weights_pack is None


def test_finance_portfolio_path_falls_back_to_packaged_default(tmp_path: Path) -> None:
    # tracker_path pinned to an empty tmp dir: a scaffolded corpus at the real tracker_path must
    # not shadow the packaged-fallback branch under test.
    assert FinanceSettings(weights_pack=None, tracker_path=tmp_path).portfolio_path == PORTFOLIO_PATH


def test_finance_portfolio_path_uses_the_loaded_pack(tmp_path: Path) -> None:
    (tmp_path / "finance").mkdir()
    s = FinanceSettings(weights_pack=tmp_path)
    assert s.portfolio_path == tmp_path / "finance" / "portfolio.yaml"


# ----- travel lane -----


def test_travel_weights_path_falls_back_to_packaged_default() -> None:
    assert TravelSettings(weights_pack=None).weights_path == WEIGHTS_PATH


def test_travel_weights_path_uses_the_loaded_pack(tmp_path: Path) -> None:
    (tmp_path / "travel").mkdir()
    s = TravelSettings(weights_pack=tmp_path)
    assert s.weights_path == tmp_path / "travel" / "weights.yaml"


def test_travel_corpus_path_falls_back_to_tracker(tmp_path: Path) -> None:
    # No pack -> the legacy `<tracker>/travel` path, unchanged.
    s = TravelSettings(tracker_path=tmp_path, weights_pack=None)
    assert s.travel_corpus_path == tmp_path / "travel"


def test_travel_corpus_path_uses_the_loaded_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    (pack / "travel").mkdir(parents=True)
    s = TravelSettings(tracker_path=tmp_path, weights_pack=pack)
    assert s.travel_corpus_path == pack / "travel"


# ----- career lane -----


def test_role_hunt_root_falls_back_to_tracker(tmp_path: Path) -> None:
    # No pack -> the legacy `<tracker>/role-hunt` root, unchanged.
    s = BaseToolkitSettings(tracker_path=tmp_path, weights_pack=None)
    assert role_hunt_root(s) == tmp_path / "role-hunt"


def test_role_hunt_root_uses_the_loaded_pack(tmp_path: Path) -> None:
    # A pack's `career/` IS the role-hunt root (the `role-hunt/` infix is dropped).
    pack = tmp_path / "pack"
    (pack / "career").mkdir(parents=True)
    s = BaseToolkitSettings(tracker_path=tmp_path, weights_pack=pack)
    assert role_hunt_root(s) == pack / "career"


def test_career_service_role_hunt_defaults_to_tracker(tmp_path: Path) -> None:
    # Unchanged construction (the test/legacy path) -> `<tracker>/role-hunt`.
    assert CareerService(tmp_path).role_hunt == tmp_path / "role-hunt"


def test_career_service_role_hunt_honors_the_pack_root(tmp_path: Path) -> None:
    pack_career = tmp_path / "pack" / "career"
    svc = CareerService(tmp_path, role_hunt_root=pack_career)
    assert svc.role_hunt == pack_career


# ----- the trailing `--pack` CLI plumbing (PackGroup) -----


def _pack_app() -> typer.Typer:
    """A tiny root app with one PackGroup lane + one verb, mirroring the real mount."""
    lane = typer.Typer(cls=PackGroup)

    @lane.command()
    def hello() -> None:
        # echo whether a pack is active so the test can assert the env was set before the body ran.
        typer.echo(f"pack={os.environ.get('WEIGHTS_PACK', '')}")

    root = typer.Typer()
    root.add_typer(lane, name="lane")
    return root


def test_packgroup_injects_pack_into_every_verb(tmp_path: Path) -> None:
    # Assert `--pack` is INJECTED + accepted on the verb by invoking WITH it — rendering-independent.
    # (Parsing the Rich-rendered `--help` is brittle: on a narrow / no-TTY console like CI the
    # option-name column truncates and `--pack` won't appear as a literal substring.) If PackGroup
    # didn't inject the option, this would exit 2 with "No such option: --pack".
    (tmp_path / "finance").mkdir()  # a plausible pack (has a lane subdir)
    res = CliRunner().invoke(_pack_app(), ["lane", "hello", "--pack", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == f"pack={tmp_path}"  # the verb ran with WEIGHTS_PACK set


def test_packgroup_trailing_pack_sets_env_before_the_verb_runs(tmp_path: Path) -> None:
    (tmp_path / "finance").mkdir()  # a plausible pack (has a lane subdir)
    res = CliRunner().invoke(_pack_app(), ["lane", "hello", "--pack", str(tmp_path)])
    assert res.exit_code == 0
    assert f"pack={tmp_path.resolve()}" in res.output


def test_packgroup_no_pack_leaves_env_unset() -> None:
    res = CliRunner().invoke(_pack_app(), ["lane", "hello"])
    assert res.exit_code == 0
    assert "pack=\n" in res.output or res.output.strip().endswith("pack=")


def test_packgroup_bad_pack_path_errors_loudly(tmp_path: Path) -> None:
    res = CliRunner().invoke(_pack_app(), ["lane", "hello", "--pack", str(tmp_path / "nope")])
    assert res.exit_code != 0
    assert "weight pack not found" in res.output
