"""The seal — the exact `claude -p` invocation, its settings file, and the checks that prove them.

Why the seal is built here and not left to the model's instructions: the read-only line has to hold
even when the model is wrong, confused, or steered by text it read. Each layer was verified against
the live CLI before it was relied on:

- `--restricted` — without it the working directory's own `.claude/settings.json` merges in, and a
  repo that grants its desktop sessions broad Bash/Write rules would grant them to the phone too.
- `--tools Read,Grep,Glob,Bash` — the only built-ins that exist in the session; Write/Edit are not
  denied, they are absent.
- `--strict-mcp-config` — `--tools` limits built-ins only; account-level MCP connectors still load
  without this flag.
- `--permission-mode dontAsk` + `--permission-prompts none` — anything not allowed by a rule is
  refused on the spot (nobody is there to approve). Compound commands, redirects and `$(…)` are
  checked per subcommand, so an allowed prefix can't carry a write.
- deny rules for secret paths, absolute paths written `//abs/**` (a single leading `/` means
  "relative to the settings file" and silently misses), mirrored in the OS sandbox's `denyRead` so a
  shell `cat` is refused by the kernel, not only by the rule matcher.
- the environment: `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are stripped — `-p` bills an API key
  whenever one is present — and the lane's actor name is set for the bead tracker.

Invalid settings are ignored silently in `-p` mode, so nothing here is trusted until `verify_static`
(and, at runner start, the live probe in runner.py) has checked it.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.paths import augmented_path, harness_config_dir, which_tool
from harness.pocket.config import PocketConfig

TOOLS = ["Read", "Grep", "Glob", "Bash"]

# Tools that must never exist in the session, denied by bare name (a bare-name deny removes the tool
# from the model's context entirely) — belt-and-braces behind `--tools`.
DENY_TOOLS = ["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]

# Write verbs denied even if an overlay's allowlist is broader than it should be (deny beats allow).
DENY_BASH = [
    "Bash(bd update *)", "Bash(bd close *)", "Bash(bd delete *)", "Bash(bd edit *)",
    "Bash(bd reopen *)", "Bash(bd dep *)", "Bash(bd label *)", "Bash(bd remember *)",
    "Bash(bd forget *)",
    "Bash(git push *)", "Bash(git commit *)", "Bash(git reset *)", "Bash(git checkout *)",
    "Bash(git rebase *)", "Bash(git merge *)", "Bash(git stash *)", "Bash(git tag *)",
    "Bash(rm *)", "Bash(mv *)", "Bash(cp *)", "Bash(curl *)", "Bash(wget *)",
]

MCP_SERVER = "harness"

# MCP tools denied by name whatever the overlay allows: bus writes, the seen-cache writer, image/asset
# writers, and the arbitrary-URL fetcher (a model steered by text it read could carry data out in a
# query string).
DENY_MCP = [
    "bus_ack", "bus_publish", "finance_watch", "finance_wire_read", "finance_research",
    "travel_fetch_destination_images", "travel_make_contact_sheet",
]

# The write tools an operator may grant through `mcp_writes`, each narrow by construction: the take
# writer creates one new file under finance/market/takes/ from a body alone (no path, no overwrite).
SANCTIONED_WRITES = {"finance_take_write"}


def mcp_rule(tool: str) -> str:
    return f"mcp__{MCP_SERVER}__{tool}"


# Auth keys that would silently switch `-p` from the subscription to API billing.
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# `claude auth status` methods that bill the operator's subscription.
SUBSCRIPTION_AUTH = {"claude.ai", "oauth_token"}


def mandatory_deny_read() -> list[Path]:
    """Secret-bearing paths no tool may read, whatever the overlay says. Resolved at call time so a
    sealed HARNESS_CONFIG_DIR is honored."""
    home = Path.home()
    return [
        harness_config_dir(),               # bus token, push keys, .env, the browser profile
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        home / ".config" / "gh",
        home / ".netrc",
        home / ".git-credentials",
        home / ".claude.json",
        home / ".claude" / ".credentials.json",
        home / "Library" / "Keychains",
    ]


def _abs_rules(tool: str, path: Path) -> list[str]:
    """Both the path itself and everything under it — whether it is a file or a dir is not known
    ahead of time (a secret file may not exist yet). `//` = absolute in Claude Code's permission
    grammar; a single `/` would be settings-relative and silently match nothing."""
    p = str(path).rstrip("/")
    return [f"{tool}(/{p})", f"{tool}(/{p}/**)"]


def deny_paths(cfg: PocketConfig) -> list[Path]:
    seen: list[Path] = []
    for p in [*mandatory_deny_read(), *cfg.deny_read]:
        if p not in seen:
            seen.append(p)
    return seen


def settings_doc(cfg: PocketConfig) -> dict[str, Any]:
    """The runner's settings file — the only settings source the session loads."""
    paths = deny_paths(cfg)
    deny = [*DENY_TOOLS, *DENY_BASH,
            *(r for p in paths for t in ("Read", "Grep", "Glob") for r in _abs_rules(t, p)),
            "Read(**/.env)", "Read(**/.env.*)"]
    deny += [mcp_rule(t) for t in DENY_MCP]
    allow = ["Read", "Grep", "Glob", *cfg.allow,
             *(mcp_rule(t) for t in cfg.mcp_tools if t not in DENY_MCP and t not in SANCTIONED_WRITES),
             *(mcp_rule(t) for t in cfg.mcp_writes if t in SANCTIONED_WRITES)]
    sandbox: dict[str, Any] = {
        "enabled": True,
        # never run unsandboxed when the OS sandbox can't start — the session refuses instead
        "failIfUnavailable": True,
        # a sandboxed command still has to match an allow rule — the sandbox is a floor, not a pass
        "autoAllowBashIfSandboxed": False,
        "allowUnsandboxedCommands": False,
        "filesystem": {"denyRead": [str(p) for p in paths]},
        "network": {"allowedDomains": list(cfg.network_domains), "strictAllowlist": True},
    }
    return {"permissions": {"allow": allow, "deny": deny}, "sandbox": sandbox}


def write_settings(cfg: PocketConfig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings_doc(cfg), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def mcp_doc(cfg: PocketConfig) -> dict[str, Any]:
    """The `--mcp-config` file: the harness's own MCP server, nothing else (`--strict-mcp-config`
    keeps account-level connectors out). The server's env names the lane as the author of anything
    the sanctioned writers create — set here by the host, never by the model."""
    exe = which_tool("harness-mcp") or "harness-mcp"
    return {"mcpServers": {MCP_SERVER: {"command": exe, "args": [],
                                         "env": {"HARNESS_TAKE_AUTHOR": cfg.actor}}}}


def write_mcp(cfg: PocketConfig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mcp_doc(cfg), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def claude_bin() -> str | None:
    return which_tool("claude")


def argv(cfg: PocketConfig, settings_path: Path, *, mcp_path: Path | None = None, resume: str | None = None,
         session_id: str | None = None, model: str | None = None,
         system_prompt: str | None = None) -> list[str]:
    """The full invocation. The prompt goes on stdin, never argv (no length or quoting limits, and
    it never shows in `ps`)."""
    exe = claude_bin() or "claude"
    out = [
        exe, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--restricted",
        "--tools", ",".join(TOOLS),
        "--settings", str(settings_path),
        "--strict-mcp-config",
        *(["--mcp-config", str(mcp_path)] if mcp_path else []),
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--append-system-prompt", system_prompt if system_prompt is not None else cfg.boot_prompt,
    ]
    if model or cfg.model:
        out += ["--model", str(model or cfg.model)]
    if cfg.effort and not model:
        out += ["--effort", cfg.effort]
    for d in cfg.add_dirs:
        out += ["--add-dir", str(d)]
    if resume:
        out += ["--resume", resume]
    elif session_id:
        out += ["--session-id", session_id]
    return out


def env(cfg: PocketConfig, base: dict[str, str] | None = None) -> dict[str, str]:
    """The child environment: the parent's, minus API-billing keys, plus the lane's actor and a PATH
    that finds the operator's tools under launchd's minimal environment."""
    src = dict(os.environ if base is None else base)
    for k in STRIP_ENV:
        src.pop(k, None)
    src["BEADS_ACTOR"] = cfg.actor
    src["PATH"] = augmented_path(src.get("PATH", ""))
    src.pop("CLAUDECODE", None)  # a runner started from inside a session must not look nested
    return src


@dataclass
class SealReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.ok = False

    def failures(self) -> list[str]:
        return [f"{c['check']}: {c['detail']}" for c in self.checks if not c["ok"]]


def auth_method(child_env: dict[str, str]) -> str:
    """`claude auth status` → authMethod ('' when the CLI is missing or the output is unreadable)."""
    exe = claude_bin()
    if not exe:
        return ""
    try:
        res = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30,
                             env=child_env, check=False)
        data = json.loads(res.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return ""
    return str(data.get("authMethod", "")) if isinstance(data, dict) else ""


def verify_static(cfg: PocketConfig, cmd: list[str], doc: dict[str, Any], child_env: dict[str, str],
                  *, auth: str | None = None) -> SealReport:
    """Check the invocation, settings and environment without calling a model."""
    rep = SealReport(ok=True)
    rep.add("claude on PATH", claude_bin() is not None, claude_bin() or "not found")
    rep.add("harness-mcp on PATH", which_tool("harness-mcp") is not None,
            which_tool("harness-mcp") or "not found")
    for flag in ("--restricted", "--strict-mcp-config"):
        rep.add(f"flag {flag}", flag in cmd)
    rep.add("permission mode dontAsk", _after(cmd, "--permission-mode") == "dontAsk")
    rep.add("permission prompts none", _after(cmd, "--permission-prompts") == "none")
    tools = set((_after(cmd, "--tools") or "").split(","))
    rep.add("tools are read+shell only", tools == set(TOOLS), ",".join(sorted(tools)))
    deny = set(doc.get("permissions", {}).get("deny", []))
    missing_tools = [t for t in DENY_TOOLS if t not in deny]
    missing_tools += [mcp_rule(t) for t in DENY_MCP if mcp_rule(t) not in deny]
    rep.add("write tools denied by name", not missing_tools, ",".join(missing_tools))
    missing = [str(p) for p in deny_paths(cfg) if not set(_abs_rules("Read", p)) <= deny]
    rep.add("secret paths denied (Read, //abs)", not missing, ", ".join(missing))
    sandbox = doc.get("sandbox", {})
    rep.add("sandbox enabled", sandbox.get("enabled") is True)
    rep.add("sandbox fails closed", sandbox.get("failIfUnavailable") is True)
    rep.add("no unsandboxed commands", not sandbox.get("excludedCommands")
            and sandbox.get("allowUnsandboxedCommands") is False)
    sb_missing = [str(p) for p in deny_paths(cfg)
                  if str(p) not in sandbox.get("filesystem", {}).get("denyRead", [])]
    rep.add("secret paths denied (sandbox)", not sb_missing, ", ".join(sb_missing))
    unsanctioned = [t for t in cfg.mcp_writes if t not in SANCTIONED_WRITES]
    rep.add("only sanctioned write tools", not unsanctioned, ", ".join(unsanctioned))
    allowed = set(doc.get("permissions", {}).get("allow", []))
    write_rules = {mcp_rule(t) for t in [*DENY_MCP, *SANCTIONED_WRITES]} & allowed
    stray = sorted(write_rules - {mcp_rule(t) for t in cfg.mcp_writes if t in SANCTIONED_WRITES})
    rep.add("no write tool allowed outside mcp_writes", not stray, ", ".join(stray))
    broad = [r for r in cfg.allow if r.strip() in ("Bash", "Bash(*)", "Bash(**)")]
    rep.add("no blanket Bash allow", not broad, ", ".join(broad))
    leaked = [k for k in STRIP_ENV if k in child_env]
    rep.add("no API-billing key in env", not leaked, ", ".join(leaked))
    rep.add("actor set", child_env.get("BEADS_ACTOR") == cfg.actor, child_env.get("BEADS_ACTOR", ""))
    method = auth_method(child_env) if auth is None else auth
    rep.add("subscription auth", method in SUBSCRIPTION_AUTH, method or "unknown")
    rep.add("cwd exists", cfg.cwd.is_dir(), str(cfg.cwd))
    return rep


def _after(cmd: list[str], flag: str) -> str | None:
    try:
        return cmd[cmd.index(flag) + 1]
    except (ValueError, IndexError):
        return None
