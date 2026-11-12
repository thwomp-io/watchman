"""Apply one broker fill to the portfolio ledger (portfolio.yaml): a targeted line editor.

The ledger is edited by hand as well as by this module, so it is never round-tripped through a YAML
library that could reflow it or drop its comments: exactly the lines a fill touches are rewritten
and every other byte is left alone.

One fill per call (buys; sells are out of scope):
- the holding's `shares` / `avg_cost` / `cost_basis` (computed; broker values, when passed, must agree
  within $0.02 or nothing is written);
- a symbol not held gets a new holding block (before the account's cash sweep);
- the matching `open_orders` entry is removed (a partial fill reduces its qty);
- with `pending`, the account's cash sweep (its `type: cash` holding) is netted by the fill's dollars
  and its `as_of` bumped.

The ledger holds the state after the fill and nothing else. What it was before, and the evidence for
the fill, go to the history store (`ledger_log`) as one row: `apply_fill` returns it and `write_fill`
adds it once the edited file has parsed. A comment on a line whose value changes described the old
value, so it moves into that row too.

`write_fill` parse-smokes the result through the same reader `positions` uses and restores the
original on any failure. Values this writes are numbers, a quoted date, or a JSON-quoted name, so no
value ever carries an unquoted ': '.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.finance.ledger_log import FillRow, Ledger

TOLERANCE = 0.02  # broker vs computed, in dollars

_TOP_KEY = re.compile(r"^[A-Za-z_][\w-]*:")
_ITEM = re.compile(r"^  - symbol: (\S+)\s*(?:#.*)?$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


class FillError(ValueError):
    """A refused fill — the message says why; nothing was written."""


@dataclass
class Fill:
    symbol: str
    qty: float
    price: float
    screen: str  # the screenshot / evidence id that proves it
    when: datetime  # local, aware: the time recorded for the fill
    account: str | None = None  # default: where the symbol is held (required for a new name, multi-account)
    broker_avg: float | None = None
    broker_basis: float | None = None
    pending: bool = False  # the debit hasn't settled: net it out of the account's cash sweep
    order_limit: float | None = None  # pick the open order by its limit (needed for a partial fill)
    no_order: bool = False  # the ledger never held an order for this fill (e.g. a market buy)
    name: str | None = None  # required to open a new position
    asset_type: str = "stock"


@dataclass
class FillResult:
    text: str
    diff: str
    changes: list[str] = field(default_factory=list)
    opened: bool = False  # the fill opened a new position (re-render the living visuals)
    record: FillRow | None = None  # the row for the history store


def _num(x: float) -> str:
    """Shares as the ledger writes them: 43, 265.439 (never 43.0)."""
    return f"{x:.6f}".rstrip("0").rstrip(".")


# ---- reading the ledger as lines -----------------------------------------------------------------

_KEY_LINE = re.compile(
    r"^(?P<lead>    [\w-]+:[ \t]*)(?P<val>\"[^\"]*\"|[^\s#]+)(?P<gap>[ \t]*)(?:#[ \t]?(?P<cmt>.*))?$"
)


@dataclass(frozen=True)
class _KeyLine:
    """One `    key: value  # comment` line, split so the value can be replaced and the key and its
    spacing kept byte for byte."""

    lead: str  # the indent, the key, the colon and the space after it
    value: str
    gap: str  # the whitespace between the value and its comment
    comment: str

    @classmethod
    def parse(cls, line: str) -> _KeyLine:
        m = _KEY_LINE.match(line)
        if not m:
            raise FillError(f"can't read the ledger line: {line.strip()[:80]}")
        return cls(m.group("lead"), m.group("val"), m.group("gap") or "  ", (m.group("cmt") or "").strip())

    def with_value(self, value: str) -> str:
        """The line carrying `value` and no comment: the old comment described the old value."""
        return f"{self.lead}{value}"


@dataclass(frozen=True)
class _Block:
    """The line range [first, last) of one `  - symbol:` item."""

    first: int
    last: int
    symbol: str


class _Ledger:
    """The ledger's lines, with just enough structure to find a holding or an order and its keys."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()

    def section(self, key: str) -> tuple[int, int]:
        """The line range [start, end) under a top-level `key:`."""
        start = next((i + 1 for i, ln in enumerate(self.lines) if ln.rstrip() == f"{key}:"), None)
        if start is None:
            raise FillError(f"the ledger has no `{key}:` section")
        end = next(
            (i for i in range(start, len(self.lines)) if _TOP_KEY.match(self.lines[i])), len(self.lines)
        )
        return start, end

    def blocks(self, key: str) -> list[_Block]:
        """Each item under `key:`. An item runs until the next line indented two spaces or less: the
        next item, a comment between items, or a top-level key."""
        start, end = self.section(key)
        out = []
        for i in range(start, end):
            m = _ITEM.match(self.lines[i])
            if not m:
                continue
            j = i + 1
            while j < end and not re.match(r"^ {0,2}\S", self.lines[j]):
                j += 1
            out.append(_Block(i, j, m.group(1)))
        return out

    def line_of(self, block: _Block, key: str) -> int | None:
        return next(
            (i for i in range(block.first, block.last) if re.match(rf"^    {key}:", self.lines[i])), None
        )

    def key_line(self, i: int) -> _KeyLine:
        return _KeyLine.parse(self.lines[i])

    def continuation(self, i: int) -> list[str]:
        """The text of the comment lines that continue line `i`'s trailing comment: the comment-only
        lines directly under it that are indented past the item's keys."""
        if not self.key_line(i).comment:
            return []
        out = []
        for line in self.lines[i + 1:]:
            m = re.match(r"^ {5,}#\s?(.*)$", line)
            if not m:
                break
            out.append(m.group(1).strip())
        return out

    def value(self, block: _Block, key: str) -> str | None:
        i = self.line_of(block, key)
        return self.key_line(i).value.strip('"') if i is not None else None


@dataclass
class _Edits:
    """What a fill changes, collected against the original line numbers and applied at the end."""

    rewrites: dict[int, str] = field(default_factory=dict)  # line number → its new text
    splices: list[tuple[int, int, list[str]]] = field(default_factory=list)  # [first, last) → lines
    changes: list[str] = field(default_factory=list)  # one line per change, for the caller to print
    # Comments on rewritten lines, by where they sat. They go to the history store with the fill.
    displaced: dict[str, str] = field(default_factory=dict)

    def rewrite(self, ledger: _Ledger, i: int, value: str, where: str) -> None:
        """Give line `i` a new value. Its comment, and the comment lines continuing it underneath,
        described the old value: they leave the file and are kept in `displaced`."""
        line = ledger.key_line(i)
        self.rewrites[i] = line.with_value(value)
        after = ledger.continuation(i)
        if after:
            self.splices.append((i + 1, i + 1 + len(after), []))
        comment = "\n".join([line.comment, *after]).strip()
        if comment:
            self.displaced[where] = comment

    def applied(self, lines: list[str]) -> list[str]:
        out = list(lines)
        for i, line in self.rewrites.items():
            out[i] = line
        # bottom-up, so an earlier splice's line numbers are still the original ones
        for first, last, replacement in sorted(self.splices, key=lambda s: s[0], reverse=True):
            out[first:last] = replacement
        return out


# ---- the steps of a fill -------------------------------------------------------------------------


def apply_fill(text: str, fill: Fill) -> FillResult:
    """The ledger text with `fill` applied (pure; raises FillError, writing nothing, on any doubt)."""
    sym = _checked_symbol(fill)
    ledger = _Ledger(text)
    day = fill.when.date().isoformat()
    amount = round(fill.qty * fill.price, 2)
    edits = _Edits()

    # The order first: the holding's comment quotes its limit, and a partial fill leaves a remainder.
    order = _settle_order(ledger, fill, sym, edits)
    account = _account_for(ledger, fill, sym)
    position = _add_to_holding(ledger, fill, sym, account, amount, edits)
    cash = _net_pending_cash(ledger, fill, sym, account, amount, day, edits) if fill.pending else None
    sweep_note = (
        f" · {cash['symbol']} {cash['balance'][0]:,.2f} − {amount:,.2f} = {cash['balance'][1]:,.2f} net"
        if cash else ""
    )

    summary = (
        f"FILL ({fill.screen}; log-fill): {sym} {_num(fill.qty)} @ ${fill.price:.2f} "
        f"(${amount:,.2f}{order.limit_note}) → {_num(position.shares)} sh / avg {position.avg:.2f} / "
        f"basis {position.basis:,.2f}"
        + (" · NEW POSITION" if position.opened else "")
        + (f" · partial: {_num(order.remaining)} remain on the order" if order.remaining else "")
        + sweep_note + "."
    )
    record = FillRow(
        ts=fill.when.isoformat(timespec="minutes"), date=day, symbol=sym, account=account,
        qty=fill.qty, price=fill.price, amount=amount, screen=fill.screen, text=summary,
        shares_before=position.old_shares, shares_after=position.shares,
        avg_before=position.old_avg, avg_after=position.avg,
        basis_before=position.old_basis, basis_after=position.basis,
        data={
            "opened": position.opened,
            "avg_source": "broker" if fill.broker_avg is not None else "computed",
            "order": order.record, "cash": cash, "displaced_comments": edits.displaced,
        },
    )
    out = edits.applied(ledger.lines)
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile="portfolio.yaml", tofile="portfolio.yaml (after)", n=1,
    ))
    return FillResult(
        text=new_text, diff=diff, changes=edits.changes, opened=position.opened, record=record
    )


def _checked_symbol(fill: Fill) -> str:
    sym = fill.symbol.upper()
    if not _SYMBOL.match(sym):
        raise FillError(f"not a ticker: {fill.symbol!r}")
    if fill.qty <= 0 or fill.price <= 0:
        raise FillError("qty and price must be positive")
    # Each of these is written into a one-line comment or value.
    for label, value in (("screen", fill.screen), ("name", fill.name or ""), ("account", fill.account or "")):
        if "\n" in value:
            raise FillError(f"--{label} can't contain a newline")
    return sym


@dataclass(frozen=True)
class _SettledOrder:
    limit_note: str = ""  # "; limit 280.00", for the fill's summary
    remaining: float = 0.0  # what a partial fill leaves resting
    record: dict[str, float] | None = None  # the order as it was: qty, limit, remaining


def _settle_order(ledger: _Ledger, fill: Fill, sym: str, edits: _Edits) -> _SettledOrder:
    """Find the open order this fill came from and retire it: a full fill removes the entry, a
    partial fill reduces its qty."""
    orders = [b for b in ledger.blocks("open_orders") if b.symbol == sym]
    if fill.no_order:
        return _SettledOrder()

    def qty_and_limit(order: _Block) -> tuple[float, float]:
        qty, limit = ledger.value(order, "qty"), ledger.value(order, "limit")
        if qty is None or limit is None:
            raise FillError(f"open order for {sym} at line {order.first + 1} has no qty/limit")
        return float(qty), float(limit)

    if fill.order_limit is not None:
        matches = [o for o in orders if abs(qty_and_limit(o)[1] - fill.order_limit) < 1e-9]
    else:
        matches = [o for o in orders if abs(qty_and_limit(o)[0] - fill.qty) < 1e-9]
    if len(matches) != 1:
        how = f"limit {fill.order_limit:.2f}" if fill.order_limit is not None else f"qty {_num(fill.qty)}"
        raise FillError(
            f"{len(matches)} open orders match {sym} {how} ({len(orders)} open for {sym}): "
            "pass --order-limit to pick one (needed for a partial fill), or --no-order when the "
            "ledger never had the order"
        )
    order = matches[0]
    order_qty, limit = qty_and_limit(order)
    side = ledger.value(order, "side") or "buy"
    if side != "buy":
        raise FillError(f"the matched {sym} order is a {side}: log-fill handles buys only")
    if fill.price > limit + 0.005:
        raise FillError(f"a buy can't fill above its limit: ${fill.price:.2f} > ${limit:.2f} — "
                        "the wrong order matched, or the price is mistyped")
    if fill.qty > order_qty + 1e-9:
        raise FillError(f"fill qty {_num(fill.qty)} exceeds the order's {_num(order_qty)}")

    limit_note = f"; limit {limit:.2f}"
    if abs(fill.qty - order_qty) < 1e-9:
        edits.splices.append((order.first, order.last, []))
        edits.changes.append(f"open order {sym} {_num(order_qty)} @ {limit:.2f} → filled, removed")
        return _SettledOrder(limit_note, record={"qty": order_qty, "limit": limit, "remaining": 0.0})

    remaining = order_qty - fill.qty
    qty_i = ledger.line_of(order, "qty")
    assert qty_i is not None  # qty_and_limit read it
    edits.rewrite(ledger, qty_i, _num(remaining), f"open_orders/{sym}/qty")
    edits.changes.append(
        f"open order {sym} @ {limit:.2f}: qty {_num(order_qty)} → {_num(remaining)} (partial)"
    )
    return _SettledOrder(
        limit_note, remaining, record={"qty": order_qty, "limit": limit, "remaining": remaining}
    )


def _account_for(ledger: _Ledger, fill: Fill, sym: str) -> str:
    """The account the fill lands in: the one named, else the only one holding the symbol, else the
    ledger's only account."""
    if fill.account is not None:
        return fill.account
    holdings = ledger.blocks("holdings")
    holding_sym = sorted({ledger.value(b, "account") or "" for b in holdings if b.symbol == sym})
    every = sorted({ledger.value(b, "account") or "" for b in holdings})
    if len(holding_sym) == 1:
        return holding_sym[0]
    if not holding_sym and len(every) == 1:
        return every[0]
    raise FillError(f"pass --account ({sym} is held in {', '.join(holding_sym) or 'no account'}; "
                    f"the ledger has {', '.join(every)})")


def _cash_sweeps(ledger: _Ledger, account: str) -> list[_Block]:
    return [
        b for b in ledger.blocks("holdings")
        if (ledger.value(b, "account") or "") == account and (ledger.value(b, "type") or "") == "cash"
    ]


@dataclass(frozen=True)
class _Position:
    shares: float
    avg: float
    basis: float
    opened: bool  # the fill opened it
    old_shares: float = 0.0
    old_avg: float = 0.0
    old_basis: float = 0.0


def _add_to_holding(
    ledger: _Ledger,
    fill: Fill,
    sym: str,
    account: str,
    amount: float,
    edits: _Edits,
) -> _Position:
    """Add the fill to the symbol's holding in `account`, or open one. Shares, average cost and basis
    are computed; a broker figure, when given, must agree within TOLERANCE and is then the one written."""
    held = [
        b for b in ledger.blocks("holdings")
        if b.symbol == sym and (ledger.value(b, "account") or "") == account
    ]
    if len(held) > 1:
        raise FillError(f"{sym} is held more than once in {account}: edit by hand")
    if held:
        indexes = [ledger.line_of(held[0], key) for key in ("shares", "avg_cost", "cost_basis")]
        if any(i is None for i in indexes):
            raise FillError(f"{sym} has no shares/avg_cost/cost_basis line to update")
        shares_line, avg_line, basis_line = (ledger.key_line(i) for i in indexes if i is not None)
        old_shares, old_avg, old_basis = (
            float(shares_line.value), float(avg_line.value), float(basis_line.value)
        )
    else:
        if not fill.name:
            raise FillError(f"{sym} isn't held in {account}: pass --name to open the position")
        old_shares = old_avg = old_basis = 0.0

    shares = old_shares + fill.qty
    basis = round(old_basis + amount, 2)
    if fill.broker_basis is not None:
        if abs(fill.broker_basis - basis) > TOLERANCE:
            raise FillError(f"broker basis {fill.broker_basis:,.2f} vs computed {basis:,.2f} "
                            f"({old_basis:,.2f} + {amount:,.2f}): off by {fill.broker_basis - basis:+.2f}; "
                            "nothing written")
        basis = round(fill.broker_basis, 2)
    avg = round(basis / shares, 2)
    if fill.broker_avg is not None:
        if abs(fill.broker_avg - avg) > TOLERANCE:
            raise FillError(f"broker avg {fill.broker_avg:.2f} vs computed {avg:.2f} ({basis:,.2f} / "
                            f"{_num(shares)}): off by {fill.broker_avg - avg:+.2f}; nothing written")
        avg = round(fill.broker_avg, 2)

    if held:
        shares_i, avg_i, basis_i = (i for i in indexes if i is not None)
        edits.rewrite(ledger, shares_i, _num(shares), f"holdings/{sym}/shares")
        edits.rewrite(ledger, avg_i, f"{avg:.2f}", f"holdings/{sym}/avg_cost")
        edits.rewrite(ledger, basis_i, f"{basis:.2f}", f"holdings/{sym}/cost_basis")
        edits.changes.append(
            f"{sym}: {_num(old_shares)} → {_num(shares)} sh · avg {old_avg:.2f} → {avg:.2f} · "
            f"basis {old_basis:,.2f} → {basis:,.2f}"
        )
    else:
        # A new holding goes above the account's cash sweep, which the ledger keeps last.
        sweeps = _cash_sweeps(ledger, account)
        at = sweeps[0].first if sweeps else ledger.section("holdings")[1]
        edits.splices.append((at, at, [
            f"  - symbol: {sym}",
            f"    name: {json.dumps(fill.name)}",
            f"    account: {account}",
            f"    shares: {_num(shares)}",
            f"    avg_cost: {avg:.2f}",
            f"    cost_basis: {basis:.2f}",
            f"    type: {fill.asset_type}",
        ]))
        edits.changes.append(f"{sym}: NEW POSITION {_num(shares)} sh · avg {avg:.2f} · basis {basis:,.2f}")
    return _Position(shares, avg, basis, not held, old_shares, old_avg, old_basis)


def _net_pending_cash(
    ledger: _Ledger, fill: Fill, sym: str, account: str, amount: float, day: str, edits: _Edits
) -> dict[str, Any]:
    """The fill's debit hasn't settled, so the broker's cash figure still includes it: net it out of
    the account's cash sweep and date the balance. Returns the sweep's before and after, for the log."""
    sweeps = _cash_sweeps(ledger, account)
    if len(sweeps) != 1:
        raise FillError(f"--pending needs exactly one cash sweep (type: cash) in {account} "
                        f"(found {len(sweeps)})")
    sweep = sweeps[0]
    balance_i, as_of_i = ledger.line_of(sweep, "balance"), ledger.line_of(sweep, "as_of")
    if balance_i is None or as_of_i is None:
        raise FillError(f"the {sweep.symbol} sweep has no balance/as_of line")
    balance_line, as_of_line = ledger.key_line(balance_i), ledger.key_line(as_of_i)
    old_balance = float(balance_line.value)
    new_balance = round(old_balance - amount, 2)
    edits.rewrite(ledger, balance_i, f"{new_balance:.2f}", f"holdings/{sweep.symbol}/balance")
    edits.rewrite(ledger, as_of_i, f'"{day}"', f"holdings/{sweep.symbol}/as_of")
    edits.changes.append(
        f"{sweep.symbol}: {old_balance:,.2f} → {new_balance:,.2f} (pending {amount:,.2f}); "
        f"as_of → {day}"
    )
    return {
        "symbol": sweep.symbol,
        "balance": [old_balance, new_balance],
        "as_of": [as_of_line.value.strip('"'), day],
    }


def write_fill(path: Path, fill: Fill, *, dry_run: bool = False) -> FillResult:
    """Apply `fill` to the ledger file. Writes atomically, then parse-smokes through the reader
    `positions` uses; on any failure the original bytes are put back and FillError says why. Once
    the file is good, the fill's row is added to the history store."""
    from harness.finance.corpus.reader import CorpusReader

    original = path.read_text()
    res = apply_fill(original, fill)
    if dry_run:
        return res
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".portfolio-", suffix=".yaml")
    with os.fdopen(fd, "w") as fh:
        fh.write(res.text)
    os.replace(tmp, path)
    try:
        CorpusReader(path).read_portfolio()
    except Exception as exc:  # noqa: BLE001 — any parse failure restores the original
        path.write_text(original)
        raise FillError(f"the edited ledger failed its parse-smoke, original restored: {exc}") from exc
    if res.record is not None:
        Ledger.for_portfolio(path).add(res.record)
    return res
