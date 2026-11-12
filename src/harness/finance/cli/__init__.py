"""`hn finance`: the finance lane's CLI. Each module holds one area's verbs (market data, the
ledger and its history, research, the standing agents); this file builds the group and fixes the order
`hn finance --help` lists them in. Thin over `FinanceService`; observation only, no trading."""

from __future__ import annotations

import typer

from harness.finance.cli import history, ledger, market, ops, research
from harness.packs import PackGroup

app = typer.Typer(
    cls=PackGroup,  # every verb accepts a trailing `--pack <dir>` (hn finance networth --pack …)
    add_completion=False,
    help="Read-only market-data hands for the harness: live quotes + corpus-aware portfolio "
    "observation. Observation only — no trading.",
)

# Registration order is the order `--help` lists the verbs in.
app.command()(market.bars)
app.command()(market.session)
app.command()(market.correlate)
app.command()(market.gauges)
app.command(name="gates")(ops.gates)
app.command(name="trap-map")(ops.trap_map)
app.command()(ops.orders)
app.command(name="unwind")(ledger.unwind)
app.command()(market.quote)
app.command()(market.market)
app.command()(market.fed)
app.command()(market.history)
app.command()(market.moves)
app.command()(market.crashdays)
app.command()(ledger.positions)
app.command()(ledger.networth)
app.command()(ledger.daygl)
app.command("log-fill")(ledger.log_fill)
app.command("ledger-log")(history.ledger_log)
app.command("ledger-note")(history.ledger_note)
app.command("ledger-grade")(history.ledger_grade)
app.command()(ledger.concentration)
app.command()(ledger.allocation)
app.command(name="fund-proxy")(ledger.fund_proxy)
app.command(name="fund-holdings")(ledger.fund_holdings)
app.command()(research.resolve)
app.command()(research.fundamentals)
app.command()(research.multiples)
app.command()(research.filing)
app.command()(research.compare)
app.command()(ops.pulse)
app.command()(market.ratings)
app.command()(research.screen)
app.command()(ledger.symbols)
app.command()(ops.mcp)
app.command()(research.news)
app.add_typer(research.wire_app, name="wire")
app.command(name="take-write")(research.take_write)
app.command()(market.calendar)
app.command()(ops.watch)
app.command()(research.research)
app.command()(research.scorecards)
app.command()(research.projections)
app.command()(ledger.holdings)
app.command()(ops.viz)
app.command(name="global")(market.global_markets)

