"""One YAML loader for every human-authored harness config — booleans are `true`/`false` only.

PyYAML implements YAML 1.1, where `on`/`off`/`yes`/`no`/`y`/`n` (any case) are booleans. In a
config full of tickers that is a silent data-loss bug: `- symbol: ON` (ON Semiconductor) loaded as
`True`, the entry never matched a symbol, and the projections bank lost a name with no error.
This loader keeps YAML 1.1 everywhere else but resolves booleans the YAML 1.2 way (`true`/`false`
in any capitalization), so those words stay strings. Every config `yaml.safe_load` in `src/`
goes through `load_yaml`; a config that genuinely wants a boolean writes `true`/`false`.
"""

from __future__ import annotations

import re
from typing import Any

import yaml


class _StrictBoolLoader(yaml.SafeLoader):
    """SafeLoader with the YAML 1.2 boolean set."""


# Drop the inherited 1.1 bool resolver (keyed by first character), then register the 1.2 one.
_StrictBoolLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictBoolLoader.add_implicit_resolver(  # type: ignore[no-untyped-call]  # untyped PyYAML API
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load_yaml(text: str) -> Any:
    """`yaml.safe_load` with YAML 1.2 booleans (see module docstring)."""
    return yaml.load(text, Loader=_StrictBoolLoader)  # noqa: S506 — a SafeLoader subclass
