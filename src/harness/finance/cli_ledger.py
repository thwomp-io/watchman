"""The book verbs of `hn finance`: positions, net worth, day G/L, concentration, allocation, the
fund proxy + fund holdings, the unwind, holdings, the quotable symbols.

Commands are registered on the `finance` app in `harness.finance.cli` (that file fixes the
order `hn finance --help` lists them in)."""

from __future__ import annotations

import datetime as _dt
import json
from datetime import date
from typing import TYPE_CHECKING, cast

import typer
from rich.markup import escape
from rich.table import Table

from harness.finance.config.settings import get_settings
from harness.finance.providers.base import ProviderError

if TYPE_CHECKING:
    pass
from harness._cli import fail as _fail
from harness.finance._cli import _money_short, _signed, _svc, console
from harness.viz import VizError, render_diagram


def positions(
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Portfolio holdings (from the corpus seed) joined to live quotes — read-only observation."""
    try:
        snap = _svc(feed).positions()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(snap.model_dump_json())
        return
    table = Table(title="Positions — across brokerages")
    for col in ("Acct", "Symbol", "Type", "Shares", "Price", "Mkt value", "Unreal G/L", "%", "Day %"):
        table.add_column(col)
    positions = sorted(snap.positions, key=lambda p: (p.account, p.symbol))
    for p in positions:
        table.add_row(
            p.account,
            p.symbol,
            p.asset_type,
            f"{p.shares:g}",
            f"${p.price:,.2f}" if p.price is not None else "—",
            f"${p.market_value:,.2f}" if p.market_value is not None else "—",
            _signed(p.unrealized_gl, money=True),
            _signed(p.unrealized_gl_pct),
            _signed(p.day_change_pct),
        )
    console.print(table)

    # Per-brokerage quoted subtotals (the cross-brokerage view) + the all-brokerage total.
    accounts = sorted({p.account for p in positions})
    if len(accounts) > 1:
        for acct in accounts:
            acct_pos = [p for p in positions if p.account == acct]
            mv = sum(p.market_value or 0.0 for p in acct_pos)
            gl = sum(p.unrealized_gl or 0.0 for p in acct_pos)
            unreal = _signed(gl, money=True)
            console.print(f"[dim]  {acct}: value ${mv:,.2f}   unrealized {unreal}[/dim]")
    console.print(
        f"\n[bold]Net worth ${snap.net_worth:,.2f}[/bold]  "
        f"[dim](live ${snap.live_value:,.2f} + last-known ${snap.last_known_value:,.2f} + "
        f"static ${snap.static_value:,.2f})[/dim]   day {_signed(snap.quoted_day_gl, money=True)} (live)"
    )
    for p in positions:
        if p.note:
            console.print(f"[dim]  {p.symbol}: {p.note}[/dim]")
    for n in snap.notes:
        console.print(f"[dim]  • {n}[/dim]")


def networth(
    as_json: bool = typer.Option(False, "--json"),
    log: bool = typer.Option(False, "--log", help="Upsert today's total into finance/networth-history.json"),
) -> None:
    """Full-picture net worth across institutions (brokerage + retirement + cash)."""
    try:
        nw = _svc().networth()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(nw.model_dump_json())
        return
    table = Table(title="Net worth — full picture")
    for col in ("Account", "Value", "Basis", "As of"):
        table.add_column(col)
    for g in nw.groups:
        table.add_row(g.account, f"${g.value:,.2f}", g.valuation, g.as_of or "live")
    console.print(table)
    console.print(
        f"\n[bold]Net worth ${nw.total:,.2f}[/bold]  "
        f"[dim](live ${nw.live_value:,.2f} + last-known ${nw.last_known_value:,.2f} + "
        f"static ${nw.static_value:,.2f})[/dim]"
    )
    for n in nw.notes:
        console.print(f"[dim]  • {n}[/dim]")
    if log:
        _log_networth(nw.total)


def daygl(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Full-book intraday day G/L: live quotes exact + fund NAV proxy-ESTIMATED + statics flat."""
    try:
        d = _svc().daygl()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(d.model_dump_json())
        return
    table = Table(title="Day G/L — full book (est)")
    for col in ("Sleeve", "Value", "Day $", "Day %", "Basis"):
        table.add_column(col)
    for r in d.rows:
        table.add_row(
            r.label,
            f"${r.value:,.2f}",
            _signed(r.day_gl, money=True) if r.day_gl is not None else "[dim]flat[/dim]",
            _signed(r.day_pct) if r.day_pct is not None else "[dim]—[/dim]",
            f"{r.kind}" + (f" · {r.detail}" if r.detail else ""),
        )
    console.print(table)
    if d.total_day_gl is not None:
        console.print(
            f"\n[bold]Day G/L {_signed(d.total_day_gl, money=True)} "
            f"({_signed(d.total_day_pct) if d.total_day_pct is not None else '—'}) est[/bold]  "
            f"[dim]of net worth ${d.net_worth:,.2f}[/dim]"
        )
        if d.accounts:
            split = " · ".join(f"{a} {_signed(v, money=True)}" for a, v in sorted(d.accounts.items()))
            console.print(f"[dim]  exact sleeve by account: {split}[/dim]")
    else:
        console.print("[yellow]Nothing priced this run (no live quotes, no proxy estimate).[/yellow]")
    for n in d.notes:
        console.print(f"[dim]  • {n}[/dim]")


def _render_derived(diagram: str, data: dict[str, object], dest: str, name: str) -> None:
    """Render a service-derived viz data dict to {dest}/visuals/{name}.svg (composes the viz engine).
    The `--write` path for the derived net-worth-portrait verbs — same render call the `viz` verb uses,
    so a derived treemap/pie lands exactly where the hand-authored one did (the plan-doc embeds keep
    working, now backed by always-current data)."""
    tracker = get_settings().tracker_path
    out = tracker / dest / "visuals" / f"{name}.svg"
    try:
        written = render_diagram(diagram, data, out)
    except VizError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    rel = written.resolve().relative_to(tracker.resolve())
    console.print(f"wrote {written}")
    console.print(f"![[{rel.as_posix()}|640]]", markup=False)


def concentration(
    as_json: bool = typer.Option(False, "--json"),
    write: bool = typer.Option(False, "--write", help="Render the treemap SVG to {dest}/visuals/{name}.svg"),
    dest: str = typer.Option(
        "finance/plans/concentration-unwind", "--dest", help="Vault path under the corpus for --write"
    ),
    name: str = typer.Option("concentration", "--name", help="Output SVG stem for --write"),
) -> None:
    """LIVE net-worth concentration treemap — DERIVED from portfolio.yaml + live quotes.

    Replaces the hand-authored concentration.json: holdings grouped by the semantic risk taxonomy
    (concentrated single-stock + single-theme fund isolated, then diversified-brokerage / retirement /
    cash). Never stale — a fresh portfolio.yaml sync + this command reproduces it current. `--json`
    emits the `treemap` contract; `--write` renders the SVG. Read-only observation.
    """
    try:
        data = _svc().concentration_treemap()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if write:
        _render_derived("treemap", data, dest, name)
        return
    if as_json:
        console.print_json(json.dumps(data))
        return
    nodes = cast("list[dict[str, object]]", data["nodes"])
    table = Table(title="Concentration — LIVE (treemap data)")
    for col in ("Holding", "Value", "Group"):
        table.add_column(col)
    for n in nodes:
        table.add_row(str(n["label"]), f"${float(cast('float', n['value'])):,.2f}", str(n["group"]))
    console.print(table)


def allocation(
    as_json: bool = typer.Option(False, "--json"),
    write: bool = typer.Option(False, "--write", help="Render the pie SVG to {dest}/visuals/{name}.svg"),
    dest: str = typer.Option(
        "finance/plans/concentration-unwind", "--dest", help="Vault path under the corpus for --write"
    ),
    name: str = typer.Option("allocation", "--name", help="Output SVG stem for --write"),
) -> None:
    """Current → target allocation pies — Current DERIVED live, Target from config.

    Replaces the hand-authored allocation.json: the Current pie is the liquid pool (every non-retirement
    holding, each its own slice) computed live from portfolio.yaml; the Target pie is the ratified
    strategic plan read from `allocation_target:` (config-stored judgment, synced when the strategy
    changes — not per-fill). `--json` emits the `pie` contract; `--write` renders the SVG.
    """
    try:
        data = _svc().allocation_pie()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e
    if write:
        _render_derived("pie", data, dest, name)
        return
    if as_json:
        console.print_json(json.dumps(data))
        return
    for pie in cast("list[dict[str, object]]", data["pies"]):
        slices = cast("list[dict[str, object]]", pie["slices"])
        total = sum(float(cast("float", s["value"])) for s in slices) or 1.0
        table = Table(title=f"{pie['label']} — {pie.get('caption', '')}")
        for col in ("Slice", "Value", "%"):
            table.add_column(col)
        for s in sorted(slices, key=lambda x: -float(cast("float", x["value"]))):
            v = float(cast("float", s["value"]))
            table.add_row(str(s["label"]), f"${v:,.0f}", f"{v / total * 100:.1f}%")
        console.print(table)


def fund_proxy(
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Estimate a mutual fund's EOD direction from a live proxy basket (it doesn't price intraday).
    The fund + its proxies come from the `fund_proxy:` block in portfolio.yaml."""
    try:
        est = _svc(feed).fund_proxy()
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if as_json:
        console.print_json(est.model_dump_json())
        return
    table = Table(title=f"{est.fund} proxy basket")
    cols = ["Symbol", "Fund wt %", "Price", "Prev close", "Move %", "Status"] if est.weighted else [
        "Symbol", "Price", "Prev close", "Move %", "Status"
    ]
    for col in cols:
        table.add_column(col)
    for c in est.components:
        row = [
            c.symbol,
            f"${c.price:,.2f}" if c.price is not None else "—",
            f"${c.prev_close:,.2f}" if c.prev_close is not None else "—",
            _signed(c.move_pct),
            "ok" if c.available else "unavailable",
        ]
        if est.weighted:
            row.insert(1, f"{c.weight:.2f}" if c.weight is not None else "—")
        table.add_row(*row)
    console.print(table)
    headline = _signed(est.estimate_pct) if est.estimate_pct is not None else "n/a"
    if est.session_state and est.session_date:
        sd = _dt.date.fromisoformat(est.session_date)
        tag = "live" if est.session_state == "live" else est.session_state
        headline += f"  [bold]·[/bold] {sd:%a %m-%d} US session ({tag})"
    if est.weighted:
        console.print(
            f"\n[bold]Rough EOD estimate[/bold]: {headline}  "
            f"[dim](cap-weighted over {est.available_count} live names; basket = "
            f"{est.coverage_pct}% of the fund, {est.live_coverage_pct}% live)[/dim]"
        )
    else:
        console.print(
            f"\n[bold]Rough EOD estimate[/bold]: {headline}  "
            f"[dim](equal-weight mean of {est.available_count} available names)[/dim]"
        )
    for i, n in enumerate(est.notes):
        style = "yellow" if i == 0 and est.session_state not in (None, "live") else "dim"
        console.print(f"[{style}]  • {escape(n)}[/{style}]")


def fund_holdings(
    # A fund's name/CIK is per-user data, not tool config — so the defaults come from the USER
    # OVERLAY (harness.yaml finance.global_settings.fund_holdings), never from code. Flags still
    # override; with neither, the command errors naming both homes.
    query: str = typer.Option(
        "", "--query", help='The fund\'s exact series name for EDGAR full-text search ("Acme Growth '
        'Fund"). Default: the user overlay\'s finance.fund_holdings.query.'
    ),
    cik: str = typer.Option(
        "", "--cik", help="The FILER trust's CIK (not the fund's — it's on the fund's EDGAR page). "
        "Default: the user overlay's finance.fund_holdings.cik."
    ),
    top: int = typer.Option(15, "--top", help="Rows to print in the table"),
    write_yaml: str = typer.Option(
        "", "--write-yaml", help="Regenerate this reference yaml (merges existing sector/us/note enrichment)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """FULL mutual-fund holdings roster via SEC Form N-PORT (keyless discovery; the archive fetch
    needs HARNESS_SEC_CONTACT in .env — SEC fair-access UA). The fund page's top-10 is marketing;
    the filing is law (~1 quarter stale by design). With --write-yaml, regenerates the corpus
    reference file preserving enrichment and prints the roster DIFF vs the prior filing — the
    manager's rotation, itemized. Run quarterly to refresh the fund roster."""
    from datetime import date as _date

    from harness.finance import nport
    from harness.settings import overlay_get

    query = query.strip() or str(overlay_get("finance", "fund_holdings", "query", default="") or "")
    cik = cik.strip() or str(overlay_get("finance", "fund_holdings", "cik", default="") or "")
    if not query or not cik:
        _fail(
            "no fund configured — pass --query + --cik, or set finance.global_settings."
            "fund_holdings.{query,cik} in your user overlay (<tracker>/config/harness.yaml)"
        )
        raise typer.Exit(code=1)

    try:
        accession, filed = nport.latest_filing_ref(query, cik)
        filing = nport.fetch_filing(cik, accession, filed)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    diff = None
    stats = None
    if write_yaml:
        from pathlib import Path

        path = Path(write_yaml).expanduser()
        existing = path.read_text() if path.exists() else ""
        if existing:
            old_rows = [
                (r.get("name", ""), float(r.get("pct", 0) or 0)) for r in nport.read_reference_rows(existing)
            ]
            diff = nport.diff_rosters(old_rows, [(h.name, h.pct) for h in filing.holdings])
        new_text, stats = nport.merge_reference_yaml(existing, filing, fetched=_date.today().isoformat())
        path.write_text(new_text)

    if as_json:
        payload = filing.model_dump()
        if diff is not None:
            payload["diff"] = diff.model_dump()
        if stats is not None:
            payload["merge_stats"] = stats
        console.print_json(data=payload)
        return

    title = (
        f"{filing.series} — N-PORT {filing.period} "
        f"(filed {filing.filed}, {len(filing.holdings)} positions)"
    )
    table = Table(title=title)
    for col in ("#", "Name", "% fund", "Value", "Country"):
        table.add_column(col)
    for i, h in enumerate(filing.holdings[:top], 1):
        table.add_row(str(i), h.name[:44], f"{h.pct:.2f}", f"${h.val_usd/1e6:,.1f}M", h.country or "—")
    console.print(table)
    if len(filing.holdings) > top:
        console.print(f"[dim]  … +{len(filing.holdings) - top} more (full roster in --json / the yaml)[/dim]")
    for n in filing.notes:
        console.print(f"[dim]  • {n}[/dim]")
    if diff is not None:
        console.print("\n[bold]Roster diff vs the prior reference file[/bold] (the manager's hand):")
        for line in diff.entries[:8]:
            console.print(f"  [green]+ {line}[/green]")
        for line in diff.exits[:8]:
            console.print(f"  [red]- {line}[/red]")
        for line in diff.shifts[:8]:
            console.print(f"  [yellow]~ {line}[/yellow]")
        if not (diff.entries or diff.exits or diff.shifts):
            console.print("  [dim](no entries/exits/shifts ≥0.25pp — a quiet quarter)[/dim]")
    if stats is not None:
        console.print(
            f"[dim]  yaml regenerated: {stats['kept']} enriched rows kept · {stats['new']} NEW rows "
            f"(sector: unknown — enrich + research) · {stats['gone']} prior names dropped. "
            "Re-run the treemap derive + re-render next.[/dim]"
        )


def unwind(
    symbol: str | None = typer.Option(
        None, "--symbol", help="Concentrated holding to analyze (needs `lots:` in portfolio.yaml; "
        "defaults to the lotted holding)"
    ),
    days: int = typer.Option(120, "--days", help="Price-history lookback for bars + support levels"),
    feed: str = typer.Option("iex", "--feed"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the full unwind contract — the dashboard source (one source, "
        "two renderers: static SVG + the live visx widget cluster)"
    ),
    section: str = typer.Option(
        "", "--section", help="Emit ONE top-level contract section (e.g. `vest_timeline` — the "
        "live-viz source for the sell-planning calendar; implies --json)"
    ),
) -> None:
    """Concentration-unwind sell-planning contract: per-lot LIVE gain/loss + wash-sale
    harvestability, the vest calendar, wash windows, the holding's price + support levels.

    The lot is the atom — gains never wash (sellable anytime), losses are the TLH inventory
    (wash-gated). Read-only observation; order placement is always the user's.
    """
    try:
        report = _svc(feed).unwind(symbol=symbol, days=days)
    except ProviderError as e:
        _fail(str(e))
        raise typer.Exit(code=1) from e

    if section:
        payload = report.model_dump(mode="json")
        if section not in payload:
            raise typer.BadParameter(f"unknown section '{section}' — one of: {', '.join(payload)}")
        console.print_json(data=payload[section])
        return
    if as_json:
        console.print_json(report.model_dump_json())
        return

    p = report.position
    console.print(
        f"\n[bold]{report.symbol}[/bold] @ ${report.price:,.2f}  "
        f"{_signed(report.day_change_pct)}  ·  position {p.shares:,.0f} sh  "
        f"mkt {_money_short(p.market_value)}  unreal {_signed(p.unrealized_gl, money=True)} "
        f"({_signed(p.unrealized_gl_pct)})\n"
    )
    if report.progress:
        pr = report.progress
        liquid_bit = ""
        if pr.pct_of_liquid is not None:
            liquid_bit = f"  ·  {pr.pct_of_liquid:.1f}% of liquid pool"
            if pr.pct_of_liquid_at_start is not None and pr.target_pct_liquid is not None:
                liquid_bit += (
                    f" (start ~{pr.pct_of_liquid_at_start:.0f}% → "
                    f"target ~{pr.target_pct_liquid:.0f}%)"
                )
        console.print(
            f"  [bold cyan]UNWIND {pr.pct_unwound:.1f}% COMPLETE[/bold cyan] — "
            f"{pr.shares_sold:,.0f} of {pr.shares_ever:,.0f} ever-held sh sold "
            f"({pr.shares_current:,.0f} remain){liquid_bit}\n"
            f"  [dim]denominator grows at each vest — a post-vest dip = more pie appeared, "
            f"not lost progress[/dim]\n"
        )

    lt = Table(title="Tax lots — live gain/loss at the current price (the atom)")
    for col in ("Acquired", "Qty", "Unit cost", "Mkt value", "Unreal G/L", "%", "Class", "Harvest"):
        lt.add_column(col)
    for lot in report.lots:
        cls = "[green]gain[/green]" if lot.klass == "gain" else "[red]loss[/red]"
        harvest = (
            "[yellow]✓ now[/yellow]"
            if lot.harvestable_now
            else ("wash-gated" if lot.klass == "loss" else "—")
        )
        lt.add_row(
            lot.acquired, f"{lot.qty:,.0f}", f"${lot.unit_cost:,.3f}",
            _money_short(lot.market_value), _signed(lot.unrealized_gl, money=True),
            _signed(lot.unrealized_gl_pct), cls, harvest,
        )
    console.print(lt)

    t = report.tlh
    console.print(
        f"\n  [bold]TLH split[/bold] @ ${report.price:,.2f} — "
        f"[red]loss inventory {_money_short(t.harvestable_loss)} across {t.harvestable_shares:,.0f} sh[/red] "
        f"(wash-gated) · [green]gain lots {_money_short(t.gain_lot_value)} / "
        f"{t.gain_lot_shares:,.0f} sh[/green] (sellable anytime)"
    )
    w = report.wash_sale
    if w.today_poisoned:
        console.print(
            f"  [yellow]wash: POISONED today ({w.reason}); next clean window "
            f"{w.next_clean_start} → {w.next_clean_end or '—'}[/yellow]"
        )
    else:
        console.print(f"  [green]wash: clean today[/green] ({w.reason})")

    vt = Table(title="RSU vest calendar — timeline markers (units × price)")
    for col in ("Date", "Units", "Est $", "Days away", "Status"):
        vt.add_column(col)
    for v in report.vests:
        vt.add_row(
            v.date, f"{v.units:,}", _money_short(v.est_value),
            f"{v.days_away:+d}", "upcoming" if v.future else "vested",
        )
    console.print(vt)
    console.print(
        "[dim]  • Read-only: lot-level tax state, not advice. Lots are manually synced "
        "(broker vest feed); gain/loss + harvestability derive from the live price.[/dim]"
    )


def holdings(
    as_json: bool = typer.Option(False, "--json", help="Emit the HOLDINGS-tab dashboard contract"),
    feed: str = typer.Option("iex", "--feed", help="Alpaca data feed (iex free / sip paid)"),
) -> None:
    """Holdings appraisal — the held book through the projections lens (the Holdings tab's feed).

    The inventory to the projections verb's shop: every held instrument, valued from the
    positions snapshot and appraised against the params registry — item level (the modeled
    12-month mid-scenario return %, tiered legendary / epic / rare / common / poor exactly as the
    projections verb scores it) from the LIVE price plus entry_item_level from the AVERAGE COST
    (how good was the buy, against the current model). Names without a registry entry render
    unappraised, reason named.
    Deterministic join; the appraisal is a lens, never a verdict.
    """
    from harness.finance.holdings import build_holdings_contract
    from harness.finance.projections import ProjectionError, load_params

    path = get_settings().projections_path
    params = []
    if path is not None:
        try:
            params = load_params(path)
        except (OSError, ProjectionError) as e:
            _fail(f"projection registry unreadable ({path}): {e}")
            raise typer.Exit(code=1) from e
    try:
        snap = _svc(feed).positions()
    except Exception as e:  # noqa: BLE001 — no corpus seed = nothing to appraise; fail loud
        _fail(f"positions unavailable: {e}")
        raise typer.Exit(code=1) from e
    rows = [
        {
            "symbol": pos.symbol,
            "name": pos.name,
            "account": pos.account,
            "valuation": pos.valuation,
            "shares": pos.shares,
            "avg_cost": pos.avg_cost,
            "cost_basis": pos.cost_basis,
            "price": pos.price,
            "market_value": pos.market_value,
        }
        # Instruments only — static balances (retirement / cash sweeps) aren't holdings to
        # appraise; they stay on the networth/positions surfaces.
        for pos in snap.positions
        if pos.valuation in ("live", "last_known") and pos.shares > 0
    ]
    contract = build_holdings_contract(params, rows)
    if as_json:
        console.print_json(json.dumps(contract))
        return
    table = Table(title="Holdings — appraised against the projection params")
    table.add_column("Symbol")
    table.add_column("Value", justify="right")
    table.add_column("Weight %", justify="right")
    table.add_column("G/L %", justify="right")
    table.add_column("item lvl", justify="right")
    table.add_column("entry lvl", justify="right")
    table.add_column("Edge", justify="right")
    table.add_column("Rarity")
    for h in contract["holdings"]:
        gl = h.get("unrealized_gl_pct")
        gl_s = f"[{'green' if gl >= 0 else 'red'}]{gl:+.1f}%[/]" if gl is not None else "—"
        if h["appraised"]:
            flags = " [dark_orange](stale)[/dark_orange]" if h["stale"] else ""
            table.add_row(
                h["symbol"],
                f"${h['value']:,.0f}",
                f"{h['weight_pct']:.1f}%",
                gl_s,
                str(h["item_level"]),
                str(h["entry_item_level"]),
                f"{h['entry_edge']:+d}",
                f"{h['rarity']}{flags}",
            )
        else:
            table.add_row(
                h["symbol"],
                f"${h['value']:,.0f}",
                f"{h['weight_pct']:.1f}%",
                gl_s,
                "—",
                "—",
                "—",
                f"[dim]{h['unappraised_reason']}[/dim]",
            )
    console.print(table)
    s = contract["summary"]
    line = (
        f"[dim]{s['appraised']}/{s['count']} appraised · invested ${s['invested_total']:,.0f}"
    )
    if "best_entry" in s:
        line += (
            f" · best entry {s['best_entry']['symbol']} ({s['best_entry']['entry_item_level']:+d})"
            f" · weakest {s['weakest_entry']['symbol']} ({s['weakest_entry']['entry_item_level']:+d})"
        )
    console.print(line + "[/dim]")
    console.print(f"[dim]{contract['disclaimer']}[/dim]")


def symbols(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """The QUOTABLE portfolio symbols (stocks/ETFs), largest cost-basis first — corpus-only,
    no network. The dynamic source behind `symbols_from: portfolio` dashboard widgets: a
    parameterized chart derives its chip list from the portfolio seed at
    refresh, so it tracks buys/sells lock-step instead of drifting as a static config snapshot.
    Mutual funds / statics are excluded (not bar-chartable); symbols dedupe across lot entries."""
    ordered = _svc().held_symbols()
    if as_json:
        import json as _json

        console.print_json(_json.dumps({"symbols": ordered}))
        return
    for s in ordered:
        console.print(s)


def _log_networth(total: float) -> None:
    """Upsert today's net-worth total into finance/networth-history.json (the `line` viz data shape).
    Non-destructive: replaces today's point if present, else appends; keeps the series date-sorted."""
    path = get_settings().tracker_path / "finance" / "networth-history.json"
    today = date.today().isoformat()
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = {
            "title": "Net worth over time",
            "subtitle": "logged via `hn finance networth --log`",
            "yPrefix": "$",
            "series": [{"label": "Net worth", "points": []}],
        }
    points = [p for p in data["series"][0]["points"] if p.get("x") != today]
    points.append({"x": today, "y": round(total, 2)})
    points.sort(key=lambda p: str(p["x"]))
    data["series"][0]["points"] = points
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[dim]  ↳ logged ${total:,.2f} for {today} → {path.name}[/dim]")
