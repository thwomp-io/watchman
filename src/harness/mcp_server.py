"""Root MCP server — the single Claude-native surface for the whole harness.

Composes each domain's FastMCP tools into one server, namespaced by domain (`travel_*`, `finance_*`)
so they coexist without collision. Each domain still defines its tools on its own `mcp` instance
(and keeps a standalone `main()` for solo use); this root introspects those instances and
re-registers every tool under the domain prefix. New domain tools flow in automatically — no
per-tool wiring here.

The prefix guard is idempotent: finance tools are already `finance_*` (left as-is); travel's bare
names (`rank_destinations`, …) gain a `travel_` prefix. Renaming travel's MCP tool names is safe —
nothing is wired to the MCP surface today (the CLI via `uv run` is the live consumer).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from harness.bus.mcp_server import mcp as bus_mcp
from harness.career.mcp_server import mcp as career_mcp
from harness.finance.mcp_server import mcp as finance_mcp
from harness.travel.mcp_server import mcp as travel_mcp

mcp = FastMCP("harness")


def _mount(domain_mcp: FastMCP, prefix: str) -> None:
    """Re-register every tool from a domain server onto the root, under `prefix` (idempotent)."""
    for tool in domain_mcp._tool_manager.list_tools():
        name = tool.name if tool.name.startswith(prefix) else f"{prefix}{tool.name}"
        mcp.add_tool(tool.fn, name=name, description=tool.description)


_mount(travel_mcp, "travel_")
_mount(finance_mcp, "finance_")
_mount(career_mcp, "career_")
_mount(bus_mcp, "bus_")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
