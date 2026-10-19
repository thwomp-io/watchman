"""The strict-boolean YAML loader: ticker words stay strings (the `ON` → True data-loss bug)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.yamlio import load_yaml


@pytest.mark.parametrize("word", ["ON", "on", "NO", "no", "YES", "Off", "Y", "n"])
def test_yaml11_boolean_words_stay_strings(word: str) -> None:
    assert load_yaml(f"- symbol: {word}\n") == [{"symbol": word}]


@pytest.mark.parametrize(("text", "want"), [("true", True), ("False", False), ("TRUE", True)])
def test_true_false_are_still_booleans(text: str, want: bool) -> None:
    assert load_yaml(f"flag: {text}\n") == {"flag": want}


def test_everything_else_resolves_as_before() -> None:
    doc = load_yaml("a: 1\nb: 2.5\nc: 2026-09-22\nd: null\ne: [x, y]\n")
    assert doc["a"] == 1 and doc["b"] == 2.5 and str(doc["c"]) == "2026-09-22" and doc["d"] is None


def test_no_config_loader_bypasses_it() -> None:
    """Every config load in src/ goes through load_yaml — a raw safe_load reintroduces the bug."""
    src = Path(__file__).resolve().parents[1] / "src" / "harness"
    offenders = [
        f"{p.relative_to(src)}:{i}"
        for p in src.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"\byaml\.(safe_)?load\(", line) and p.name != "yamlio.py"
    ]
    assert offenders == []
