"""Pocket-agent configuration — the overlay's `pocket.global_settings` block over neutral defaults.

Everything personal (the working directory, the lane's actor name, the boot prompt, which read verbs
the agent may run) lives in the operator's overlay; the packaged defaults describe a generic
read-only assistant in the corpus root, so a stranger's install gets a sealed, useful agent without
editing anything. The SEAL (what can never be granted) is not configurable here: seal.py adds the
mandatory denies on top of whatever the overlay allows.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from harness.settings import BaseToolkitSettings, overlay_get

# A neutral read-only allowlist: issue-tracker (beads) reads + filing/commenting, git history
# reads. The overlay replaces it wholesale (a lane names its own read verbs); seal.py's denies
# still apply on top.
DEFAULT_ALLOW = [
    "Bash(bd list *)",
    "Bash(bd show *)",
    "Bash(bd ready *)",
    "Bash(bd search *)",
    "Bash(bd prime)",
    "Bash(bd create *)",
    "Bash(bd comments *)",
    "Bash(git log *)",
    "Bash(git status *)",
    "Bash(git show *)",
    "Bash(git diff *)",
]

# Live data reaches the agent through the harness's own MCP server, which runs OUTSIDE the OS sandbox
# and reads its provider keys itself — so no secret ever enters the sandboxed shell. Only bounded,
# side-effect-free read tools: anything that writes state (bus_ack/bus_publish, finance_watch marks
# headlines seen, image fetches), spends paid quota (flights, hotels, food), runs a full live fan-out
# (career_openings) or fetches an arbitrary URL (finance_wire_read — an exfiltration channel) is left
# out; seal.DENY_MCP denies the worst of them by name regardless.
DEFAULT_MCP_TOOLS = [
    "finance_quote", "finance_market", "finance_fed", "finance_history", "finance_positions",
    "finance_networth", "finance_fund_proxy", "finance_daygl", "finance_fundamentals",
    "finance_multiples", "finance_compare", "finance_news", "finance_wire", "finance_gauges",
    "finance_projections", "finance_holdings", "finance_screen", "finance_correlate",
    "career_roles", "career_campaign", "career_comp", "career_pool",
    "travel_upcoming", "travel_calendar", "travel_list_trips", "travel_get_weather",
    "travel_bank", "travel_banks", "travel_schedule", "travel_active_decision",
    "travel_read_preferences",
    "finance_global", "finance_calendar", "finance_wire_read_match", "finance_watch_peek",
    "bus_list", "bus_stats",
]

DEFAULT_BOOT_PROMPT = (
    "You are the pocket agent: the operator is reading on a phone. You are READ-ONLY — you may read "
    "files and run the allowlisted read commands, and nothing else. Lead with the answer in one or two "
    "lines; keep replies short and phone-sized. If an answer needs a file change, say what should "
    "change instead of attempting it."
)


class PocketConfig(BaseModel):
    """The resolved runner configuration. Paths are absolute after `load()`."""

    cwd: Path
    actor: str = "pocket"
    model: str | None = None
    effort: str | None = None
    boot_prompt: str = DEFAULT_BOOT_PROMPT
    allow: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOW))
    # the harness MCP read tools the agent may call (bare names; seal.py prefixes mcp__harness__)
    mcp_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_MCP_TOOLS))
    # sanctioned WRITE tools the agent may call — only names in seal.SANCTIONED_WRITES are accepted
    # (today: finance_take_write, the create-only take writer); empty = a strictly read-only agent
    mcp_writes: list[str] = Field(default_factory=list)
    # extra read roots beyond cwd (skills dirs, a memory dir) — file tools may read them
    add_dirs: list[Path] = Field(default_factory=list)
    # extra paths no tool may read (on top of seal.MANDATORY_DENY_READ)
    deny_read: list[Path] = Field(default_factory=list)
    # hosts sandboxed commands may reach (the providers the allowlisted read verbs call). Commands
    # stay sandboxed — `excludedCommands` is never used, since an excluded command also loses the
    # filesystem denyRead.
    network_domains: list[str] = Field(default_factory=list)
    max_prompt_chars: int = 8000
    selftest_on_start: bool = True
    # the model the live seal probe uses — the cheapest one; it only has to attempt tool calls
    selftest_model: str = "haiku"


def _paths(raw: object) -> list[Path]:
    if not isinstance(raw, list):
        return []
    return [Path(str(p)).expanduser() for p in raw if str(p).strip()]


def _strings(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    return [str(x) for x in raw if str(x).strip()]


def load(settings: BaseToolkitSettings | None = None) -> PocketConfig:
    """Resolve the config from the overlay. Missing keys fall back to the neutral defaults."""
    s = settings or BaseToolkitSettings()

    def get(key: str) -> object:
        return overlay_get("pocket", key, settings=s)

    cwd_raw = get("cwd")
    cwd = Path(str(cwd_raw)).expanduser() if cwd_raw else s.tracker_path.expanduser()
    cfg = PocketConfig(cwd=cwd.resolve())
    if isinstance(actor := get("actor"), str) and actor.strip():
        cfg.actor = actor.strip()
    for key in ("model", "effort"):
        if isinstance(val := get(key), str) and val.strip():
            setattr(cfg, key, val.strip())
    if isinstance(boot := get("boot_prompt"), str) and boot.strip():
        cfg.boot_prompt = boot.strip()
    if (allow := _strings(get("allow"))) is not None:
        cfg.allow = allow
    if (tools := _strings(get("mcp_tools"))) is not None:
        cfg.mcp_tools = tools
    if (writes := _strings(get("mcp_writes"))) is not None:
        cfg.mcp_writes = writes
    if (net := _strings(get("network_domains"))) is not None:
        cfg.network_domains = net
    cfg.add_dirs = [p.resolve() for p in _paths(get("add_dirs"))]
    cfg.deny_read = [p.resolve() for p in _paths(get("deny_read"))]
    if isinstance(cap := get("max_prompt_chars"), int) and cap > 0:
        cfg.max_prompt_chars = cap
    if isinstance(st := get("selftest_on_start"), bool):
        cfg.selftest_on_start = st
    if isinstance(sm := get("selftest_model"), str) and sm.strip():
        cfg.selftest_model = sm.strip()
    return cfg
