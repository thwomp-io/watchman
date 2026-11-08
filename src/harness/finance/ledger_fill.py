"""Apply one broker fill to the portfolio ledger (portfolio.yaml) — a targeted line editor.

The ledger's inline comments are load-bearing provenance (every prior value, the screenshot that proved
it), so this never round-trips the YAML through a library that could reflow or drop them: it rewrites
exactly the lines a fill touches and leaves every other byte alone.

One fill per call (buys; sells are out of scope):
- the holding's `shares` / `avg_cost` / `cost_basis` (computed; broker values, when passed, must agree
  within $0.02 or nothing is written), each prior value demoted into that line's comment ("Prior …");
- a symbol not held gets a new holding block (before the account's cash sweep);
- the matching `open_orders` entry becomes a one-line FILLED comment (a partial fill reduces its qty);
- with `pending`, the account's cash sweep (its `type: cash` holding) is netted by the fill's dollars
  and its `as_of` bumped;
- a dated `# Sync …` line goes on top of the file's sync log.

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

from harness.finance.sessions import tz_label

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
    when: datetime  # local, aware: dates the comments and the sync line
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


def _num(x: float) -> str:
    """Shares as the ledger writes them: 43, 265.439 (never 43.0)."""
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _section(lines: list[str], key: str) -> tuple[int, int]:
    """[start, end) line range of a top-level `key:` section (start = the line after the key)."""
    start = next((i + 1 for i, ln in enumerate(lines) if ln.rstrip() == f"{key}:"), None)
    if start is None:
        raise FillError(f"the ledger has no `{key}:` section")
    end = next((i for i in range(start, len(lines)) if _TOP_KEY.match(lines[i])), len(lines))
    return start, end


def _items(lines: list[str], start: int, end: int) -> list[tuple[int, int, str]]:
    """(first, last-exclusive, symbol) of each `  - symbol:` item in [start, end). An item runs until
    the next line indented two spaces or less (the next item, a between-items comment, a top-level key)."""
    out = []
    for i in range(start, end):
        m = _ITEM.match(lines[i])
        if not m:
            continue
        j = i + 1
        while j < end and not re.match(r"^ {0,2}\S", lines[j]):
            j += 1
        out.append((i, j, m.group(1)))
    return out


def _key_line(lines: list[str], first: int, last: int, key: str) -> int | None:
    return next((i for i in range(first, last) if re.match(rf"^    {key}:", lines[i])), None)


_KEY = re.compile(
    r"^(?P<lead>    [\w-]+:[ \t]*)(?P<val>\"[^\"]*\"|[^\s#]+)(?P<gap>[ \t]*)(?:#[ \t]?(?P<cmt>.*))?$"
)


def _parse(line: str) -> tuple[str, str, str, str]:
    m = _KEY.match(line)
    if not m:
        raise FillError(f"can't read the ledger line: {line.strip()[:80]}")
    return m.group("lead"), m.group("val"), m.group("gap") or "  ", (m.group("cmt") or "").strip()


def _rewrite(line: str, value: str, comment: str) -> str:
    lead, _, gap, _ = _parse(line)
    return f"{lead}{value}{gap}# {comment}"


def _block_value(lines: list[str], first: int, last: int, key: str) -> str | None:
    i = _key_line(lines, first, last, key)
    return _parse(lines[i])[1].strip('"') if i is not None else None


def apply_fill(text: str, fill: Fill) -> FillResult:
    """The ledger text with `fill` applied (pure; raises FillError, writing nothing, on any doubt)."""
    sym = fill.symbol.upper()
    if not _SYMBOL.match(sym):
        raise FillError(f"not a ticker: {fill.symbol!r}")
    if fill.qty <= 0 or fill.price <= 0:
        raise FillError("qty and price must be positive")
    for label, value in (("screen", fill.screen), ("name", fill.name or ""), ("account", fill.account or "")):
        if "\n" in value:
            raise FillError(f"--{label} can't contain a newline")
    lines = text.splitlines()
    trailing_nl = text.endswith("\n")
    amt = round(fill.qty * fill.price, 2)
    date_iso = fill.when.date().isoformat()
    md = f"{fill.when.month}/{fill.when.day}"
    stamp = f"{date_iso} {fill.when:%H:%M} {tz_label(fill.when)}"
    changes: list[str] = []

    # ---- the open order (resolved first: a partial fill and the header's limit both depend on it) ----
    o_start, o_end = _section(lines, "open_orders")
    orders = [it for it in _items(lines, o_start, o_end) if it[2] == sym]
    order_edit: tuple[int, int, list[str]] | None = None
    limit_txt = ""
    remaining = 0.0
    if not fill.no_order:
        def _order(it: tuple[int, int, str]) -> tuple[float, float]:
            q, lim = _block_value(lines, it[0], it[1], "qty"), _block_value(lines, it[0], it[1], "limit")
            if q is None or lim is None:
                raise FillError(f"open order for {sym} at line {it[0] + 1} has no qty/limit")
            return float(q), float(lim)

        if fill.order_limit is not None:
            matches = [it for it in orders if abs(_order(it)[1] - fill.order_limit) < 1e-9]
        else:
            matches = [it for it in orders if abs(_order(it)[0] - fill.qty) < 1e-9]
        if len(matches) != 1:
            how = f"limit {fill.order_limit:.2f}" if fill.order_limit is not None else f"qty {_num(fill.qty)}"
            raise FillError(
                f"{len(matches)} open orders match {sym} {how} ({len(orders)} open for {sym}): "
                "pass --order-limit to pick one (needed for a partial fill), or --no-order when the "
                "ledger never had the order"
            )
        first, last, _ = matches[0]
        oqty, olim = _order(matches[0])
        side = _block_value(lines, first, last, "side") or "buy"
        if side != "buy":
            raise FillError(f"the matched {sym} order is a {side}: log-fill handles buys only")
        if fill.price > olim + 0.005:
            raise FillError(f"a buy can't fill above its limit: ${fill.price:.2f} > ${olim:.2f} — "
                            "the wrong order matched, or the price is mistyped")
        limit_txt = f"; limit {olim:.2f}"
        if fill.qty > oqty + 1e-9:
            raise FillError(f"fill qty {_num(fill.qty)} exceeds the order's {_num(oqty)}")
        if abs(fill.qty - oqty) < 1e-9:
            order_edit = (first, last, [f"  # {sym} {_num(oqty)} @ ${olim:.2f} GTC — FILLED {date_iso} @ "
                                        f"${fill.price:.2f} ({fill.screen})."])
            changes.append(f"open order {sym} {_num(oqty)} @ {olim:.2f} → FILLED comment")
        else:
            remaining = oqty - fill.qty
            qi = _key_line(lines, first, last, "qty")
            assert qi is not None
            _, _, _, old_c = _parse(lines[qi])
            note = (f"was {_num(oqty)}; PARTIAL FILL {date_iso}: {_num(fill.qty)} @ ${fill.price:.2f} "
                    f"({fill.screen})") + (f". Prior: {old_c}" if old_c else "")
            new_block = lines[first:last]
            new_block[qi - first] = _rewrite(lines[qi], _num(remaining), note)
            order_edit = (first, last, new_block)
            changes.append(f"open order {sym} @ {olim:.2f}: qty {_num(oqty)} → {_num(remaining)} (partial)")

    # ---- the holding ----
    h_start, h_end = _section(lines, "holdings")
    items = _items(lines, h_start, h_end)

    def _acct(it: tuple[int, int, str]) -> str:
        return _block_value(lines, it[0], it[1], "account") or ""

    account = fill.account
    if account is None:
        where = sorted({_acct(it) for it in items if it[2] == sym})
        every = sorted({_acct(it) for it in items})
        if len(where) == 1:
            account = where[0]
        elif not where and len(every) == 1:
            account = every[0]
        else:
            raise FillError(f"pass --account ({sym} is held in {', '.join(where) or 'no account'}; "
                            f"the ledger has {', '.join(every)})")

    def _sweep() -> list[tuple[int, int, str]]:
        return [it for it in items if _acct(it) == account
                and (_block_value(lines, it[0], it[1], "type") or "") == "cash"]

    held = [it for it in items if it[2] == sym and _acct(it) == account]
    if len(held) > 1:
        raise FillError(f"{sym} is held more than once in {account}: edit by hand")
    holding_edits: dict[int, str] = {}
    insert_at: int | None = None
    new_block_lines: list[str] = []
    opened = not held
    if held:
        first, last, _ = held[0]
        si, ai, bi = (_key_line(lines, first, last, k) for k in ("shares", "avg_cost", "cost_basis"))
        if si is None or ai is None or bi is None:
            raise FillError(f"{sym} has no shares/avg_cost/cost_basis line to update")
        _, s_val, _, s_c = _parse(lines[si])
        _, a_val, _, a_c = _parse(lines[ai])
        _, b_val, _, b_c = _parse(lines[bi])
        old_shares, old_avg, old_basis = float(s_val), float(a_val), float(b_val)
    else:
        if not fill.name:
            raise FillError(f"{sym} isn't held in {account}: pass --name to open the position")
        old_shares = old_avg = old_basis = 0.0
        s_c = a_c = b_c = ""
    shares = old_shares + fill.qty
    basis = round(old_basis + amt, 2)
    if fill.broker_basis is not None:
        if abs(fill.broker_basis - basis) > TOLERANCE:
            raise FillError(f"broker basis {fill.broker_basis:,.2f} vs computed {basis:,.2f} "
                            f"({old_basis:,.2f} + {amt:,.2f}): off by {fill.broker_basis - basis:+.2f}; "
                            "nothing written")
        basis = round(fill.broker_basis, 2)
    avg = round(basis / shares, 2)
    if fill.broker_avg is not None:
        if abs(fill.broker_avg - avg) > TOLERANCE:
            raise FillError(f"broker avg {fill.broker_avg:.2f} vs computed {avg:.2f} ({basis:,.2f} / "
                            f"{_num(shares)}): off by {fill.broker_avg - avg:+.2f}; nothing written")
        avg = round(fill.broker_avg, 2)
    src = (f"broker truth {fill.screen}" if fill.broker_avg is not None else "computed")
    filled = (f"FILLED {date_iso} @ ${fill.price:.2f} ({_num(fill.qty)} sh, ${amt:,.2f}{limit_txt}; "
              f"{fill.screen}).")
    if held:
        assert si is not None and ai is not None and bi is not None
        holding_edits[si] = _rewrite(
            lines[si], _num(shares),
            f"{_num(old_shares)} -> {_num(shares)}: {filled}" + (f" Prior {s_c}" if s_c else ""))
        holding_edits[ai] = _rewrite(
            lines[ai], f"{avg:.2f}",
            f"{src} ({basis:,.2f} / {_num(shares)}). Prior {old_avg:.2f}" + (f" — {a_c}" if a_c else ""))
        holding_edits[bi] = _rewrite(
            lines[bi], f"{basis:.2f}",
            f"{old_basis:.2f} + {amt:.2f} ({_num(fill.qty)} @ ${fill.price:.2f}, {md})."
            + (f" Prior: {b_c}" if b_c else ""))
        changes.append(f"{sym}: {_num(old_shares)} → {_num(shares)} sh · avg {old_avg:.2f} → {avg:.2f} · "
                       f"basis {old_basis:,.2f} → {basis:,.2f}")
    else:
        sweep = _sweep()
        insert_at = sweep[0][0] if sweep else h_end
        new_block_lines = [
            f"  - symbol: {sym}",
            f"    name: {json.dumps(fill.name)}",
            f"    account: {account}",
            f"    shares: {_num(shares)}  # NEW POSITION: {filled}",
            f"    avg_cost: {avg:.2f}  # {src} ({basis:,.2f} / {_num(shares)})",
            f"    cost_basis: {basis:.2f}  # {_num(fill.qty)} @ ${fill.price:.2f} ({md})",
            f"    type: {fill.asset_type}",
        ]
        changes.append(f"{sym}: NEW POSITION {_num(shares)} sh · avg {avg:.2f} · basis {basis:,.2f}")

    # ---- the cash sweep, when the debit is still pending ----
    sweep_note = ""
    if fill.pending:
        sweep = _sweep()
        if len(sweep) != 1:
            raise FillError(f"--pending needs exactly one cash sweep (type: cash) in {account} "
                            f"(found {len(sweep)})")
        first, last, cash = sweep[0]
        bal_i, asof_i = _key_line(lines, first, last, "balance"), _key_line(lines, first, last, "as_of")
        if bal_i is None or asof_i is None:
            raise FillError(f"the {cash} sweep has no balance/as_of line")
        _, bal_v, _, bal_c = _parse(lines[bal_i])
        _, asof_v, _, asof_c = _parse(lines[asof_i])
        old_bal = float(bal_v)
        new_bal = round(old_bal - amt, 2)
        holding_edits[bal_i] = _rewrite(
            lines[bal_i], f"{new_bal:.2f}",
            f"{stamp} ({fill.screen}): {old_bal:,.2f} − Pending {amt:,.2f} ({sym} {_num(fill.qty)} @ "
            f"{fill.price:.2f}, T+1) = {new_bal:,.2f} net. Was {old_bal:.2f}"
            + (f" — {bal_c}" if bal_c else "."))
        holding_edits[asof_i] = _rewrite(
            lines[asof_i], f'"{date_iso}"',
            f"{stamp} net of the {sym} fill ({fill.screen}). Prior: "
            + (asof_c if asof_c else f"as_of {asof_v.strip(chr(34))}"))
        sweep_note = f" · {cash} {old_bal:,.2f} − {amt:,.2f} = {new_bal:,.2f} net"
        changes.append(f"{cash}: {old_bal:,.2f} → {new_bal:,.2f} (pending {amt:,.2f}); as_of → {date_iso}")

    header = (f"# Sync {stamp} — FILL ({fill.screen}; log-fill): {sym} {_num(fill.qty)} @ ${fill.price:.2f} "
              f"(${amt:,.2f}{limit_txt}) → {_num(shares)} sh / avg {avg:.2f} / basis {basis:,.2f}"
              + (" · NEW POSITION" if opened else "")
              + (f" · partial: {_num(remaining)} remain on the order" if remaining else "")
              + sweep_note + ".")

    # ---- assemble: in-place line rewrites, then the block swaps/inserts bottom-up ----
    out = list(lines)
    for i, ln in holding_edits.items():
        out[i] = ln
    splices: list[tuple[int, int, list[str]]] = []
    if order_edit:
        splices.append(order_edit)
    if insert_at is not None:
        splices.append((insert_at, insert_at, new_block_lines))
    for first, last, repl in sorted(splices, key=lambda s: s[0], reverse=True):
        out[first:last] = repl
    head = next((i for i, ln in enumerate(out) if ln.startswith("# Sync ")),
                next((i for i, ln in enumerate(out) if not ln.startswith("#")), 0))
    out.insert(head, header)
    new_text = "\n".join(out) + ("\n" if trailing_nl else "")
    diff = "".join(difflib.unified_diff(text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        fromfile="portfolio.yaml", tofile="portfolio.yaml (after)", n=1))
    return FillResult(text=new_text, diff=diff, changes=changes, opened=opened)


def write_fill(path: Path, fill: Fill, *, dry_run: bool = False) -> FillResult:
    """Apply `fill` to the ledger file. Writes atomically, then parse-smokes through the reader
    `positions` uses; on any failure the original bytes are put back and FillError says why."""
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
    return res
