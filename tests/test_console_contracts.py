"""The console's TypeScript contracts are generated from the engine's models: the committed
schema must match a fresh export (run `npm run gen:types` in bus-app after a model change)."""

from __future__ import annotations

import json
from pathlib import Path

from harness.console.contracts import export_schema

SCHEMA = Path(__file__).resolve().parents[1] / "bus-app/src/generated/contracts.schema.json"


def test_committed_schema_matches_the_models() -> None:
    assert json.loads(SCHEMA.read_text()) == export_schema()


def test_every_emitted_field_is_required_and_titles_never_become_types() -> None:
    for name, d in export_schema()["$defs"].items():
        props = d.get("properties", {})
        assert d.get("required") == sorted(props), name
        assert not any("title" in p for p in props.values()), name
