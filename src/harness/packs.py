"""Weight-pack CLI plumbing — a trailing `--pack` on every lane verb.

A weight pack is a portable, swappable scenario bundle a user maintains + an app loads; each lane it
provides drives its reads from `<pack>/<lane>/…` (the loader resolution lives in
`harness.settings.BaseToolkitSettings.pack_file` + each lane's settings). This module is the *CLI
face* of that: rather than a front-loaded global (`hn --pack X finance networth`), a lane group built
on `PackGroup` accepts the pack at the natural END of the command, keeping the noun-verb phrase
contiguous:

    hn finance networth --pack samples/packs/demo-investor

The mechanism: Click binds a trailing option to the *leaf* command, so `--pack` must be a parameter
of each verb. `PackGroup` injects it into every verb at command-resolution time — one definition, no
per-verb signature noise — and an eager callback sets `WEIGHTS_PACK` (the env var the lane Settings
read) before the verb body runs.

Note: the injected option is a `TyperOption` (Typer's *vendored*-Click parameter), not a vanilla
`click.Option`. Typer 0.26 vendors its own Click as `typer._click`; a real-`click` option mixed into
a Typer command resolves against a vendored-Click context that lacks the real-Click attributes its
processing expects (`_param_default_explicit`) → an AttributeError at parse time. Staying inside
Typer's vendored Click keeps the param + the context consistent.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import typer
import yaml
from typer.core import TyperGroup, TyperOption


def _activate_pack(ctx: Any, param: Any, value: Any) -> Any:
    """Eager option callback: point the lanes at the chosen pack for this invocation.

    Sets `WEIGHTS_PACK` (the env var pydantic-settings reads) so `--pack` is just the CLI face of
    that env var — one resolution path, not a parallel one. Each lane's `get_settings()` is
    lru_cached; a fresh `hn` process hasn't populated those yet (the verb body is the first call),
    but we clear them anyway so the flag is correct even if an import-time / in-process prior call
    already cached a no-pack Settings (tests, repeated invocations, the MCP server).

    A non-existent path is a loud error rather than a silent fall-back to the real corpus — a typo'd
    `--pack` should never quietly run against your default data.
    """
    if value is None:
        return value
    pack = Path(value).expanduser()
    if not pack.is_dir():
        raise typer.BadParameter(f"weight pack not found (not a directory): {pack}", param=param)
    os.environ["WEIGHTS_PACK"] = str(pack.resolve())
    # Lazy imports: these lanes' packages import this module, so importing them at module top would
    # risk an import cycle. At callback time (runtime) the cycle is long resolved.
    from harness.career.cli import get_settings as career_settings
    from harness.finance.config.settings import get_settings as finance_settings
    from harness.travel.config.settings import get_settings as travel_settings

    for getter in (finance_settings, travel_settings, career_settings):
        getter.cache_clear()
    return value


def _pack_option() -> TyperOption:
    """The injected `--pack` option. `expose_value=False` because the verb functions don't declare a
    `pack` parameter — the side effect (setting the env) is the whole job."""
    return TyperOption(
        param_decls=["--pack"],
        expose_value=False,
        is_eager=True,
        callback=_activate_pack,
        help="Load a weight pack (a portable scenario bundle): the lanes it provides drive their "
        "reads from <pack>/<lane>/…. Equivalent to setting WEIGHTS_PACK.",
    )


class PackGroup(TyperGroup):
    """A lane group whose every verb accepts a trailing `--pack <dir>`.

    Set as the `cls` of a lane's `typer.Typer(...)`. At command-resolution time it appends the
    `--pack` option to each leaf verb (idempotently — Click caches command instances), so the pack
    is selectable at the natural end of the command rather than as a front-loaded global.
    """

    def get_command(self, ctx: Any, name: str) -> Any:
        cmd = super().get_command(ctx, name)
        if cmd is not None and not any(p.name == "pack" for p in cmd.params):
            cmd.params.append(_pack_option())
        return cmd


# --- pack discovery (the CLI face of "what packs ship?") ------------------------------------------
# `--pack` above LOADS a pack (any dir); this half DISCOVERS the bundled samples. A user's own pack
# lives wherever they keep it and is loaded by path — the bundled samples are just the shipped
# starting points (the public clone-and-run scenarios).


@dataclass
class BundledPack:
    name: str  # dir name (what you pass to --pack)
    title: str  # human title from pack.yaml
    path: str  # absolute dir path
    lanes: list[str]  # lanes the pack provides data for
    default: bool  # the OOTB default a fresh install loads


def bundled_packs_dir() -> Path:
    """Where the repo's bundled sample packs live (`<repo>/samples/packs`). Resolved from this file so
    an editable install finds them regardless of CWD (the launch-context-resolution doctrine)."""
    return Path(__file__).resolve().parents[2] / "samples" / "packs"


def discover_bundled_packs() -> list[BundledPack]:
    """The bundled sample packs (dirs holding a `pack.yaml`), sorted by name. Reads each manifest for
    title/lanes/default — a malformed manifest is skipped, never crashes the listing."""
    root = bundled_packs_dir()
    out: list[BundledPack] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        manifest = d / "pack.yaml"
        if not manifest.is_file():
            continue
        try:
            m = yaml.safe_load(manifest.read_text()) or {}
        except yaml.YAMLError:
            continue
        out.append(BundledPack(
            name=d.name,
            title=str(m.get("title", d.name)),
            path=str(d),
            lanes=list(m.get("lanes") or []),
            default=bool(m.get("default", False)),
        ))
    return out


packs_app = typer.Typer(
    no_args_is_help=True,
    help="Weight packs — portable scenario bundles. `list` shows the bundled samples; load ANY pack "
    "dir with `--pack <dir>` on a lane verb (or set WEIGHTS_PACK). The watchman app loads them too.",
)


@packs_app.command("list")
def list_packs_cmd(
    json_out: bool = typer.Option(False, "--json", help="emit JSON instead of a table"),
) -> None:
    """List the bundled sample weight packs (the shipped scenarios)."""
    packs = discover_bundled_packs()
    if json_out:
        typer.echo(json.dumps([asdict(p) for p in packs], indent=2))
        return
    if not packs:
        typer.echo(f"no bundled packs found under {bundled_packs_dir()}")
        return
    for p in packs:
        tag = "  [default]" if p.default else ""
        typer.echo(f"{p.name}{tag} — {p.title}")
        typer.echo(f"    lanes: {', '.join(p.lanes) or '(none)'}")
        typer.echo(f"    load:  hn <lane> <verb> --pack {p.path}")
