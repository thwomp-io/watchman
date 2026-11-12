"""The ledger-history verbs of `hn finance`: read what the ledger used to say, and add to it.

`log-fill` writes its own rows. These cover the rest: `ledger-log` reads, `ledger-note` records a
change made by hand against a broker screen, and `ledger-grade` records a day's check of the fund
proxy against the published NAV.

Commands are registered on the `finance` app in `harness.finance.cli`."""

from __future__ import annotations

import datetime as _dt
import json
from enum import StrEnum

import typer

from harness.errors import HarnessError, UsageError
from harness.finance.cli.common import console
from harness.finance.config.settings import get_settings
from harness.finance.corpus.reader import CorpusReader
from harness.finance.ledger_log import GradeRow, Ledger, SyncRow


class Kind(StrEnum):
    FILLS = "fills"
    SYNCS = "syncs"
    GRADES = "grades"


def _ledger() -> Ledger:
    return Ledger.for_portfolio(get_settings().portfolio_path)


def _day(value: str | None, option: str) -> _dt.date | None:
    if value is None:
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as e:
        raise UsageError(f"{option} must look like 2026-10-30") from e


def ledger_log(
    symbol: str = typer.Option(None, "--symbol", "-s", help="Rows about this symbol, or naming it"),
    since: str = typer.Option(None, "--since", help="Rows dated on or after YYYY-MM-DD"),
    kind: list[Kind] = typer.Option(
        [], "--kind", "-k", help="fills | syncs | grades (repeatable; default all)"
    ),
    grep: str = typer.Option(None, "--grep", help="Rows whose text contains this (case-insensitive)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Newest N rows (0 = all)"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild the database from the export first"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The ledger's history, newest first: fills, syncs against a broker screen, and fund-proxy grades.

    portfolio.yaml holds the current state only; this is where a prior basis, an old balance or a
    past grade is found. The database lives in the state directory and is reproduced from the
    committed export (`ledger-history/ledger.jsonl`), which `--rebuild` does on demand."""
    ledger = _ledger()
    if rebuild:
        if not ledger.export_path.exists():
            raise HarnessError(f"no export to rebuild from at {ledger.export_path}")
        console.print(f"[dim]rebuilt {ledger.rebuild()} rows from {ledger.export_path}[/dim]")
    rows = ledger.rows(
        kinds=tuple(k.value for k in kind) or None, symbol=symbol, since=_day(since, "--since"),
        contains=grep, limit=limit or None,
    )
    if as_json:
        # `kind` is the row's type in the singular (fill, sync, grade); `table` is where it is stored
        console.print_json(json.dumps([
            {"kind": r.table[:-1], "table": r.table, **r.model_dump()} for r in rows
        ]))
        return
    if not rows:
        console.print("[dim]no history rows match[/dim]")
        return
    for row in rows:
        about = getattr(row, "symbol", None) or getattr(row, "fund", None) or ""
        head = f"[bold]{row.date or 'undated'}[/bold] [dim]{row.table[:-1]}[/dim] {about}".rstrip()
        console.print(head, highlight=False)
        console.print(f"  {row.text}", markup=False, highlight=False, soft_wrap=True)


def ledger_note(
    text: str = typer.Argument(..., help="What changed, as you would have written the sync line"),
    symbol: str = typer.Option(None, "--symbol", "-s", help="The holding it concerns"),
    screen: str = typer.Option("", "--screen", help="The evidence id (the broker screenshot's name)"),
    path: str = typer.Option("", "--path", help="Where in the yaml it applies, e.g. holdings/ABC/balance"),
    at: str = typer.Option(None, "--at", help="YYYY-MM-DD HH:MM local (default: now)"),
) -> None:
    """Record a change made to the ledger by hand (a balance, a NAV, an order placed or cancelled).

    The yaml takes the new value; the sentence about it goes here, not into a comment."""
    try:
        when = (_dt.datetime.strptime(at, "%Y-%m-%d %H:%M") if at else _dt.datetime.now()).astimezone()
    except ValueError as e:
        raise UsageError("--at must look like 2026-10-30 08:02") from e
    row = SyncRow(
        ts=when.isoformat(timespec="minutes"), date=when.date().isoformat(), text=text.strip(),
        symbol=symbol.upper() if symbol else None, screen=screen, path=path,
    )
    _ledger().add(row)
    console.print(f"[dim]recorded · {row.date} sync{' ' + row.symbol if row.symbol else ''}[/dim]")


def ledger_grade(
    nav_before: float = typer.Option(..., "--nav-before", help="The prior published NAV"),
    nav_after: float = typer.Option(..., "--nav-after", help="The NAV being graded"),
    on: str = typer.Option(None, "--date", help="The NAV's date, YYYY-MM-DD (default: today)"),
    estimate: float = typer.Option(None, "--estimate", help="What the proxy said, in percent"),
    comparator: str = typer.Option("", "--comparator", help="Which estimate that was (e.g. 'filled proxy')"),
    day: int = typer.Option(None, "--day", help="The running day count (default: the last grade's + 1)"),
    fund: str = typer.Option(None, "--fund", help="Default: the portfolio's fund_proxy fund"),
    screen: str = typer.Option("", "--screen", help="The evidence id"),
    note: str = typer.Option("", "--note", help="The read of the day, in a sentence"),
) -> None:
    """Record one day's check of the fund proxy against the published NAV.

    The move and the miss are computed: pct from the two NAVs, and with `--estimate` the miss in
    percentage points and whether the direction was right."""
    ledger = _ledger()
    fund = (fund or CorpusReader(get_settings().portfolio_path).read_portfolio().proxy.fund).upper()
    if not fund:
        raise UsageError("no fund: pass --fund, or set fund_proxy.fund in portfolio.yaml")
    if nav_before <= 0:
        raise UsageError("--nav-before must be positive")
    when = _day(on, "--date") or _dt.date.today()
    if day is None:
        last = next((r for r in ledger.rows(kinds=("grades",), symbol=fund) if getattr(r, "day", None)), None)
        day = (getattr(last, "day", 0) or 0) + 1
    pct = round((nav_after - nav_before) / nav_before * 100, 2)
    miss = round(abs(estimate - pct), 2) if estimate is not None else None
    sentence = f"Day {day}: NAV {nav_before:.2f} → {nav_after:.2f} = {pct:+.2f}%"
    if estimate is not None:
        right = (estimate > 0) == (pct > 0) or estimate == pct
        sentence += (f" · {comparator or 'estimate'} {estimate:+.2f}%: direction "
                     f"{'right' if right else 'wrong'}, {miss:.2f}pp off")
    if note:
        sentence += f" · {note.strip()}"
    ledger.add(GradeRow(
        date=when.isoformat(), fund=fund, day=day, nav_before=nav_before, nav_after=nav_after, pct=pct,
        estimate_pct=estimate, comparator=comparator, miss_pp=miss, screen=screen, text=sentence,
    ))
    console.print(f"[dim]recorded ·[/dim] {fund} {sentence}", highlight=False)
