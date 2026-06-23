"""Bundled sample packs load OOTB — the clone-and-run guarantee.

These pin the promise that a fresh clone runs on the bundled sample packs with no configuration: each
pack's manifest is well-formed, every lane it declares has a data dir, and each lane's primary read
loads its fictional data. A broken sample pack fails here rather than in front of a reviewer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

PACKS = Path(__file__).resolve().parents[1] / "samples" / "packs"


def _all_packs() -> list[Path]:
    return sorted(p for p in PACKS.iterdir() if (p / "pack.yaml").exists())


@pytest.mark.parametrize("pack", _all_packs(), ids=lambda p: p.name)
def test_pack_manifest_lanes_have_data_dirs(pack: Path) -> None:
    manifest = yaml.safe_load((pack / "pack.yaml").read_text())
    lanes = manifest.get("lanes") or []
    assert lanes, f"{pack.name}: no lanes declared"
    for lane in lanes:
        assert (pack / lane).is_dir(), f"{pack.name}: declares lane '{lane}' but {lane}/ is missing"
    assert isinstance(manifest.get("default", False), bool)


def test_exactly_one_bundled_default() -> None:
    # A fresh clone loads exactly one default pack OOTB; more than one is ambiguous, none breaks OOTB.
    defaults = [
        p.name
        for p in _all_packs()
        if yaml.safe_load((p / "pack.yaml").read_text()).get("default")
    ]
    assert defaults == ["demo-investor"]


@pytest.mark.parametrize("pack", _all_packs(), ids=lambda p: p.name)
def test_bundled_packs_are_complete_personas(pack: Path) -> None:
    # The persona principle: a bundled pack ships EVERY lane so loading it swaps the whole console
    # (an unprovided lane would fall back to the user's real corpus and leak it).
    lanes = set(yaml.safe_load((pack / "pack.yaml").read_text()).get("lanes") or [])
    assert lanes == {"finance", "career", "travel"}, f"{pack.name}: not a complete persona"


@pytest.mark.parametrize("name", ["demo-investor", "demo-growth", "early-retiree", "college-grad"])
def test_finance_pack_portfolio_loads(name: str) -> None:
    from harness.finance.corpus.reader import CorpusReader

    seed = CorpusReader(portfolio_path=PACKS / name / "finance" / "portfolio.yaml").read_portfolio()
    assert len(seed.holdings) >= 3
    # the net-worth trend reads this logged file — ship one per persona so the trend isn't real data
    assert (PACKS / name / "finance" / "networth-history.json").is_file()


@pytest.mark.parametrize("name", ["demo-investor", "demo-growth", "early-retiree", "college-grad"])
def test_finance_pack_ships_offline_quotes(name: str) -> None:
    # the keyless clone-and-run path renders finance from a static-quote fixture (no Alpaca keys), so
    # every finance pack ships quotes.json covering its share-priced holdings + the whole-market basket
    # (so both the Core finance tab AND the Market tab render offline). Regen: scripts/gen_demo_quotes.py.
    fin = PACKS / name / "finance"
    quotes = json.loads((fin / "quotes.json").read_text())
    pf = yaml.safe_load((fin / "portfolio.yaml").read_text())
    for h in pf.get("holdings") or []:
        if h.get("shares") is not None:
            assert h["symbol"].upper() in quotes, f"{name}: holding {h['symbol']} not in quotes.json"
    assert {"SPY", "XLK", "NVDA"} <= set(quotes), f"{name}: market basket missing from quotes.json"


@pytest.mark.parametrize("name", ["demo-investor", "demo-growth", "early-retiree", "college-grad"])
def test_career_pack_watchlist_pipeline_and_scan_load(name: str) -> None:
    from harness.career.applications import load_applications
    from harness.career.watchlist import load_watchlist

    career = PACKS / name / "career"
    wl = load_watchlist(Path("/unused"), root=career)
    assert len(wl.companies) >= 1
    pipe = load_applications(Path("/unused"), root=career)
    assert len(pipe.applications) >= 1
    # a .md discoveries report (for the openings-scan doc_series) AND its JSON twin (for shortlist)
    disc = career / "discoveries"
    assert any(disc.glob("*-openings.md")) and any(disc.glob("*-openings.json"))


@pytest.mark.parametrize("name,home", [
    ("demo-investor", "Minneapolis, MN"),
    ("demo-growth", "Denver, CO"),
    ("early-retiree", "Scottsdale, AZ"),
    ("college-grad", "Austin, TX"),
])
def test_travel_pack_weights_and_trips_load(name: str, home: str) -> None:
    from harness.travel.config.settings import Settings as TravelSettings
    from harness.travel.corpus.reader import CorpusReader
    from harness.travel.ranking.weights import load_weights

    pack = PACKS / name
    weights = load_weights(pack / "travel" / "weights.yaml")
    assert weights.conditions.home == home
    # explicit settings + weights -> no dependency on the global WEIGHTS_PACK env
    reader = CorpusReader(settings=TravelSettings(weights_pack=pack), weights=weights)
    trips = reader.scan_trips()
    assert len(trips) >= 2


# Every compiled-default dashboard lane (dash.rs `compiled_defaults`). A loaded pack FULLY overrides
# the console, so it must ship the WHOLE tab-set — a missing tab would inherit the compiled
# default's symbols. Regenerated by the `emit_pack_dashboards` tool (bus-app/src-tauri dash.rs).
CONSOLE_LANES = ("finance", "travel", "unwind", "market", "tickets", "compare", "career")


@pytest.mark.parametrize("pack", _all_packs(), ids=lambda p: p.name)
def test_pack_ships_the_full_console(pack: Path) -> None:
    # v2 (pack-described dashboards): a pack ships its own dashboards/<lane>.json so loading it FULLY
    # overrides the console tab-set. Without it, the pack inherits the compiled default's chart
    # symbols and drops sub-tabs. Every persona ships ALL tabs.
    for lane in CONSOLE_LANES:
        f = pack / "dashboards" / f"{lane}.json"
        assert f.is_file(), f"{pack.name}: missing dashboards/{lane}.json (would inherit real defaults)"
        d = json.loads(f.read_text())
        assert d.get("lane") and d.get("widgets"), f"{pack.name}: dashboards/{lane}.json malformed"


@pytest.mark.parametrize("pack", _all_packs(), ids=lambda p: p.name)
def test_pack_provides_every_dashboard_read_source(pack: Path) -> None:
    # The completeness contract: every File/command source the curated dashboards read must resolve to
    # pack data, else the panel goes empty under that pack (the company-profiles + key-dates gaps caught
    # 2026-06-20). Pin the categories the dashboards consume so a missing stub fails here, not in a demo.
    assert (pack / "finance" / "networth-history.json").is_file()  # net-worth trend (File source)
    # the Market tab's take panel (doc_series) — at least one market take so it isn't an empty "No takes"
    assert any((pack / "finance" / "market" / "takes").glob("*.md")), \
        f"{pack.name}: no finance/market/takes/*.md — the Market-tab take panel would be empty"
    # the Tickets-tab ticket panel (doc_series over finance/execution/)
    assert any((pack / "finance" / "execution").glob("*.md")), \
        f"{pack.name}: no finance/execution/*.md — the Tickets-tab ticket panel would be empty"
    # the Compare-tab narrative panel (doc_series over finance/research/compares/)
    assert any((pack / "finance" / "research" / "compares").glob("*.md")), \
        f"{pack.name}: no finance/research/compares/*.md — the Compare-tab panel would be empty"
    # the Hiring-Map deep-links jump to per-company profiles
    assert any((pack / "career" / "companies").glob("*.md")), \
        f"{pack.name}: no career/companies/*.md — Hiring-Map deep-links would be dead"
    # the key-dates panel reads the travel reference almanac
    assert any((pack / "travel" / "reference").glob("*.md")), \
        f"{pack.name}: no travel/reference/*.md — the key-dates panel would be empty"
