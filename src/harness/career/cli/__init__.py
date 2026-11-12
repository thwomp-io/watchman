"""`hn career`: the role-hunt lane's CLI. Each module holds one noun's verbs; this file builds the
group and fixes the order `hn career --help` lists them in.

Read-rich, execute-gated: every verb observes (scan boards, surface comp, capture forms, render
artifacts, assemble packets). The one execute-side act is `apply --submit --confirm`, the user's
deliberate escalation: one packet per run, a receipt recorded, the pipeline row advanced. The tool
assembles; the human greenlights."""

from __future__ import annotations

import typer

from harness.career.cli import comp, corpus, levels, pipeline, render, scan
from harness.packs import PackGroup

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn career shortlist --pack …)
    add_completion=False,
    help="Career / role-hunt lane — keyless ATS board scan, posting + form capture, local shortlist "
    "+ application pipeline, résumé renders, packet assembly, D3 diagrams into role-hunt/. "
    "Execute-gated: the only act is `apply --submit --confirm`, one packet per run.",
)


# Registration order is the order `--help` lists the verbs in.
app.command()(scan.openings)
app.command()(scan.roles)
app.command()(pipeline.campaign)
app.command()(scan.shortlist)
app.command()(pipeline.applications)
app.command()(pipeline.posting)
app.command()(pipeline.schools)
app.command()(pipeline.apply)
app.command()(corpus.company_profiles)
app.command()(render.render)
app.command()(corpus.viz)
app.command()(comp.comp)
app.command("levels-index")(levels.levels_index)
app.command()(levels.families)
app.command()(levels.slugs)
app.command()(levels.industries)
app.command()(comp.pool)
