"""harness — a personal agentic-harness toolkit, one CLI of domain submodules.

Root binary `harness` (shorthand `hn`) mounts domain noun-groups: `hn travel <verb>`,
`hn finance <verb>`, `hn career <verb>`. Each domain is a
Typer sub-app + FastMCP tool set over a shared core (HTTP, corpus reader, settings, provider base).
"""

from __future__ import annotations

__version__ = "0.9.0"
