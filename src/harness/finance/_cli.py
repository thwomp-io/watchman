"""Shared helpers for the `hn finance` CLI modules (the console, the service, the formatters)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from harness.finance.service import FinanceService

if TYPE_CHECKING:
    pass

console = Console()


def _svc(feed: str = "iex") -> FinanceService:
    return FinanceService(feed=feed)


def _signed(v: float | None, money: bool = False) -> str:
    """Signed value, SIGN-COLORED (green +, red −, dim flat). Threaded through quote / positions /
    networth / watch / research / bars — one helper, consistent valence everywhere. Rich auto-strips
    color on non-TTY/piped output, so `--json` and the bus-app's subprocess reads stay clean."""
    if v is None:
        return "—"
    body = f"${abs(v):,.2f}" if money else f"{abs(v):.2f}%"
    text = f"{'+' if v >= 0 else '-'}{body}"
    style = "green" if v > 0 else "red" if v < 0 else "dim"
    return f"[{style}]{text}[/{style}]"


def _money_short(v: float) -> str:
    """Compact USD for big reported figures: 1.23B / 456.7M / 12,345 (signed)."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:,.0f}"


def _trend_money(value: float | None, prior: float | None) -> str:
    """`_money_short` colored by trend vs the prior same-type period (green=grew, red=shrank, dim=flat
    / no prior). For income-statement concepts (revenue/income/operating income) growth reads as good —
    lets the eye scan a column for the trajectory before reading a single number."""
    if value is None:
        return "—"
    cell = _money_short(value)
    if prior is None or value == prior:
        return cell
    style = "green" if value > prior else "red"
    return f"[{style}]{cell}[/{style}]"


