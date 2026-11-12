"""Keyless destination facts: exchange rates, public holidays, country basics."""

from __future__ import annotations

import json

import typer
from rich.table import Table

from harness.travel.cli.common import console
from harness.travel.service import TravelService


def fx(
    symbols: list[str] = typer.Argument(None, help="Target currencies, e.g. EUR JPY MXN (default: all)"),
    base: str = typer.Option("USD", "--base", help="Base currency"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Exchange rates from a base currency (keyless, open.er-api.com). rate = units per 1 base."""
    fxr = TravelService().fx_rates(symbols or None, base=base)
    if as_json:
        console.print_json(json.dumps(fxr.model_dump()))
        return
    console.print(f"\n[bold]1 {fxr.base}[/bold] = …  [dim]{fxr.date}[/dim]")
    table = Table()
    for col in (f"per 1 {fxr.base}", "Rate", f"1 X = {fxr.base}"):
        table.add_column(col)
    for code, rate in sorted(fxr.rates.items()):
        inv = f"{1 / rate:,.4f}" if rate else "—"
        table.add_row(code, f"{rate:,.4f}", inv)
    console.print(table)


def holidays(
    country: str = typer.Argument(..., help="ISO-3166 alpha-2, e.g. US, JP, MX"),
    year: int = typer.Option(..., "--year", help="Year, e.g. 2026"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Public holidays for a country-year (keyless, date.nager.at) — crowds/closures timing."""
    hol = TravelService().public_holidays(country, year)
    if as_json:
        console.print_json(json.dumps(hol.model_dump()))
        return
    console.print(f"\n[bold]{hol.country} {hol.year}[/bold]  [dim]{len(hol.holidays)} public holidays[/dim]")
    table = Table()
    for col in ("Date", "Holiday", "Local name", "Nationwide"):
        table.add_column(col)
    for h in hol.holidays:
        table.add_row(h.date, h.name, h.local_name, "yes" if h.nationwide else "regional")
    console.print(table)


def country(
    name: str = typer.Argument(..., help="Country name, e.g. Japan, Mexico"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Trip-prep country facts (keyless, restcountries.com): currency, language, region, driving side."""
    cf = TravelService().country_facts(name)
    if as_json:
        console.print_json(json.dumps(cf.model_dump()))
        return
    console.print(f"\n[bold]{cf.name}[/bold]  [dim]{cf.official_name}[/dim]")
    cur = ", ".join(f"{code}: {desc}" for code, desc in cf.currencies.items()) or "—"
    console.print(f"  Region:     {cf.region}{' / ' + cf.subregion if cf.subregion else ''}")
    console.print(f"  Capital:    {', '.join(cf.capital) or '—'}")
    console.print(f"  Currency:   {cur}")
    console.print(f"  Languages:  {', '.join(cf.languages) or '—'}")
    console.print(f"  Drives on:  {cf.driving_side or '—'}")
    console.print(f"  Timezones:  {', '.join(cf.timezones) or '—'}")
