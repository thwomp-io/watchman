"""`hn calibration`: log a prediction, see what is due, resolve it, read the scores."""

from __future__ import annotations

import datetime as _dt
import json

import typer
from rich.console import Console
from rich.table import Table

from harness._cli import ReportingGroup
from harness.calibration.ledger import Calibration, Entry, EntryError, propose
from harness.errors import HarnessError, UsageError
from harness.settings import BaseToolkitSettings

app = typer.Typer(
    cls=ReportingGroup,
    add_completion=False,
    no_args_is_help=True,
    help="Scored predictions: a claim, a probability and a test logged before the outcome, resolved "
    "later, and scored (Brier and reliability) per owner.",
)
console = Console()


def _ledger() -> Calibration:
    return Calibration.for_corpus(BaseToolkitSettings().tracker_path)


def _line(e: Entry) -> str:
    if e.voided:
        state = "void"
    elif e.outcome is None:
        state = f"due {e.resolves_by}"
    else:
        state = f"{e.outcome} · brier {e.brier:.4f}"
    return f"{e.ref}  {e.owner:<5} p={e.p:.2f}  {state:<22} {e.claim}"


@app.command()
def add(
    claim: str = typer.Argument(..., help="What is predicted, in a sentence"),
    p: float = typer.Option(..., "--p", help="The probability it happens, 0 to 1"),
    resolves_by: str = typer.Option(..., "--by", help="The date it resolves, YYYY-MM-DD"),
    test: str = typer.Option(..., "--test", help="The mechanical test that settles it, and its fallback"),
    owner: str = typer.Option(..., "--owner", help="Whose call it is"),
    source: str = typer.Option("", "--source", help="Where the call was made (a note, a ticket)"),
) -> None:
    """Log a prediction before it resolves.

    The claim, the probability and the test are fixed once logged: a changed view is a new entry."""
    made = _dt.datetime.now().astimezone().isoformat(timespec="minutes")
    try:
        entry, warnings = _ledger().log(
            owner=owner, claim=claim, p=p, resolves_by=resolves_by, test=test, made=made, source=source
        )
    except EntryError as e:
        raise UsageError(str(e)) from e
    console.print(f"logged {_line(entry)}", highlight=False, markup=False)
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


@app.command()
def due(
    on: str = typer.Option(None, "--on", help="As of this date (default: today)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Open entries whose resolve-by date has arrived, soonest first."""
    try:
        today = _dt.date.fromisoformat(on) if on else _dt.date.today()
    except ValueError as e:
        raise UsageError("--on must look like 2026-11-30") from e
    entries = _ledger().due(today)
    if as_json:
        console.print_json(json.dumps([e.model_dump() for e in entries]))
        return
    if not entries:
        console.print("[dim]nothing due[/dim]")
    for entry in entries:
        console.print(_line(entry), highlight=False, markup=False)
        console.print(f"      test: {entry.test}", highlight=False, markup=False, style="dim")


@app.command()
def resolve(
    ref: str = typer.Argument(..., help="The entry, e.g. C012"),
    outcome: int = typer.Option(None, "--outcome", help="1 = it happened, 0 = it didn't"),
    void: str = typer.Option("", "--void", help="Void it: why the test can't be evaluated as written"),
    note: str = typer.Option("", "--note", help="The evidence, in a line"),
    suggest: bool = typer.Option(
        False, "--propose",
        help="For a test on the fund's NAV (a level, or a band for the day's move): read the last recorded "
        "grade and print the outcome it implies. Writes nothing; confirm with --outcome",
    ),
) -> None:
    """Record how an entry came out. An entry resolves once and is not edited afterwards."""
    ledger = _ledger()
    try:
        entry = ledger.get(ref)
        if suggest:
            _propose(entry)
            return
        entry = ledger.resolve(ref, outcome=outcome, on=_dt.date.today(), void=void, note=note)
    except EntryError as e:
        raise UsageError(str(e)) from e
    console.print(_line(entry), highlight=False, markup=False)


def _propose(entry: Entry) -> None:
    """Print the outcome the fund's last recorded NAV grade implies for `entry`'s test."""
    from harness.finance.config.settings import get_settings
    from harness.finance.ledger_log import GradeRow, Ledger

    grades = Ledger.for_portfolio(get_settings().portfolio_path).all(GradeRow)
    on_day = [g for g in grades if g.date == entry.resolves_by]
    if not on_day:
        raise HarnessError(
            f"no NAV grade recorded for {entry.resolves_by}",
            hint="Record the day with `hn finance ledger-grade`, then propose again.",
        )
    grade = on_day[-1]
    proposal = propose(entry.test, nav=grade.nav_after, pct=grade.pct)
    if proposal is None:
        raise HarnessError(f"{entry.ref}'s test isn't a NAV level or a day-move band: resolve it by hand")
    outcome, why = proposal
    console.print(f"{entry.ref}: {entry.test}", highlight=False, markup=False)
    console.print(f"  proposed outcome {outcome}: {why} ({grade.fund} {grade.date})", highlight=False)
    console.print(f"  to record it: hn calibration resolve {entry.ref} --outcome {outcome}", style="dim")


@app.command(name="list")
def list_entries(
    owner: str = typer.Option(None, "--owner"),
    open_only: bool = typer.Option(False, "--open", help="Only entries not yet resolved or voided"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Every entry, oldest first."""
    entries = [
        e for e in _ledger().entries()
        if (not owner or e.owner == owner) and (not open_only or e.is_open)
    ]
    if as_json:
        console.print_json(json.dumps([e.model_dump() for e in entries]))
        return
    for e in entries:
        console.print(_line(e), highlight=False, markup=False)


@app.command()
def score(as_json: bool = typer.Option(False, "--json")) -> None:
    """Mean Brier per owner and the reliability buckets. Voided and open entries are left out.

    Brier is (p − outcome)² averaged: 0 is perfect, 0.25 is a coin flip called at 0.5. In each bucket,
    a hit rate below the mean p is overconfidence; above it, timidity."""
    scores = _ledger().scores()
    if as_json:
        console.print_json(json.dumps([
            {"owner": s.owner, "n": s.n, "brier": s.brier, "buckets": [vars(b) for b in s.buckets]}
            for s in scores
        ]))
        return
    if not scores:
        console.print("[dim]no resolved entries yet[/dim]")
        return
    for s in scores:
        table = Table(title=f"{s.owner}: {s.n} resolved · mean Brier {s.brier:.4f}", title_justify="left")
        for column in ("p", "N", "mean p", "hit rate", "read"):
            table.add_column(column)
        for b in s.buckets:
            if b.mean_p is None or b.hit_rate is None:
                table.add_row(f"{b.low:.1f}–{b.high:.1f}", "0", "—", "—", "")
                continue
            gap = b.hit_rate - b.mean_p
            read = "calibrated" if abs(gap) < 0.1 else ("overconfident" if gap < 0 else "timid")
            table.add_row(f"{b.low:.1f}–{b.high:.1f}", str(b.n), f"{b.mean_p:.2f}", f"{b.hit_rate:.2f}", read)
        console.print(table)
