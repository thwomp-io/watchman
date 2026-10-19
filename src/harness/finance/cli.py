"""Typer CLI adapter (Bash-callable / cron-able). Thin wrapper over FinanceService.

`app` is a domain-noun command group (`finance <verb>`) — standalone at its own root today, a
zero-refactor `add_typer()` mount under a future unified harness CLI (the noun-group sub-commands
pattern).
"""

from __future__ import annotations

import typer

from harness.finance import cli_ledger, cli_market, cli_ops, cli_research
from harness.packs import PackGroup

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn finance networth --pack …)
    add_completion=False,
    help="Read-only market-data hands for the harness: live quotes + corpus-aware portfolio "
    "observation. Observation only — no trading.",
)

# Registration order is the order `hn finance --help` lists the verbs in (kept from before the split).
app.command()(cli_market.bars)
app.command()(cli_market.correlate)
app.command()(cli_market.gauges)
app.command(name='gates')(cli_ops.gates)
app.command(name='trap-map')(cli_ops.trap_map)
app.command(name='unwind')(cli_ledger.unwind)
app.command()(cli_market.quote)
app.command()(cli_market.market)
app.command()(cli_market.fed)
app.command()(cli_market.history)
app.command()(cli_ledger.positions)
app.command()(cli_ledger.networth)
app.command()(cli_ledger.daygl)
app.command()(cli_ledger.concentration)
app.command()(cli_ledger.allocation)
app.command(name='fund-proxy')(cli_ledger.fund_proxy)
app.command(name='fund-holdings')(cli_ledger.fund_holdings)
app.command()(cli_research.resolve)
app.command()(cli_research.fundamentals)
app.command()(cli_research.multiples)
app.command()(cli_research.filing)
app.command()(cli_research.compare)
app.command()(cli_ops.pulse)
app.command()(cli_market.ratings)
app.command()(cli_research.screen)
app.command()(cli_ledger.symbols)
app.command()(cli_ops.mcp)
app.command()(cli_research.news)
app.add_typer(cli_research.wire_app, name='wire')
app.command(name='take-write')(cli_research.take_write)
app.command()(cli_market.calendar)
app.command()(cli_ops.watch)
app.command()(cli_research.research)
app.command()(cli_research.scorecards)
app.command()(cli_research.projections)
app.command()(cli_ledger.holdings)
app.command()(cli_ops.viz)
app.command(name='global')(cli_market.global_markets)


if __name__ == "__main__":
    app()
