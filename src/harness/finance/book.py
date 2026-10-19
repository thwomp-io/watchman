"""Book valuation — positions, net worth, the concentration treemap, the allocation pie, the fund
proxy and the full-book day G/L. Functions of the service (quotes + the portfolio seed).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    pass

from harness.finance.models import (
    DayGL,
    DayGLRow,
    NetWorth,
    NetWorthGroup,
    PortfolioSnapshot,
    Position,
    ProxyComponent,
    ProxyEstimate,
)
from harness.finance.providers.base import ProviderError

if TYPE_CHECKING:
    from harness.finance.service import FinanceService

def positions(svc: FinanceService) -> PortfolioSnapshot:
    seed = svc.reader.read_portfolio()
    quotable = [h for h in seed.holdings if h.quotable]
    quotes = {q.symbol: q for q in svc.quote([h.symbol for h in quotable])} if quotable else {}

    snap = PortfolioSnapshot()
    for h in seed.holdings:
        pos = Position(
            symbol=h.symbol,
            name=h.name,
            account=h.account,
            asset_type=h.asset_type,
            shares=h.shares,
            avg_cost=h.avg_cost,
            cost_basis=h.cost_basis,
            quotable=h.quotable,
            valuation=h.valuation,
            as_of=h.as_of,
        )
        q = quotes.get(h.symbol)
        if h.valuation == "live" and q is not None and q.available and q.price is not None:
            pos.price = q.price
            pos.market_value = q.price * h.shares
            pos.unrealized_gl = pos.market_value - h.cost_basis
            if h.cost_basis:
                pos.unrealized_gl_pct = pos.unrealized_gl / h.cost_basis * 100.0
            pos.day_change_pct = q.day_change_pct
            if q.day_change is not None:
                pos.day_gl = q.day_change * h.shares
            snap.live_value += pos.market_value
            snap.quoted_cost_basis += h.cost_basis
            snap.quoted_unrealized_gl += pos.unrealized_gl
            if pos.day_gl is not None:
                snap.quoted_day_gl += pos.day_gl
        elif h.valuation == "last_known" and h.last_price is not None:
            pos.price = h.last_price
            pos.market_value = h.last_price * h.shares
            pos.unrealized_gl = pos.market_value - h.cost_basis
            if h.cost_basis:
                pos.unrealized_gl_pct = pos.unrealized_gl / h.cost_basis * 100.0
            pos.note = f"last-known NAV ({h.as_of or 'undated'}); `finance fund-proxy` for direction"
            snap.last_known_value += pos.market_value
        elif h.valuation == "static" and h.balance is not None:
            pos.market_value = h.balance
            pos.note = f"static balance ({h.as_of or 'undated'})"
            snap.static_value += pos.market_value
        else:
            # a live holding whose quote failed (feed gap) — surface it, don't fabricate a value
            pos.note = q.note if q is not None else "no valuation available"
        snap.positions.append(pos)

    snap.quoted_market_value = snap.live_value  # back-compat alias (live-quoted only)
    snap.net_worth = snap.live_value + snap.last_known_value + snap.static_value
    snap.notes.append(
        "Net worth = live quotes + last-known NAV + static balances. Last-known / static values "
        "are manually synced (see per-row as-of) — refresh from a current broker screenshot "
        "before high-stakes finance work. Read-only observation, not a recommendation."
    )
    return snap


def networth(svc: FinanceService) -> NetWorth:
    snap = svc.positions()
    groups: dict[str, NetWorthGroup] = {}
    seen_valuations: dict[str, set[str]] = {}
    for p in snap.positions:
        if p.market_value is None:
            continue
        g = groups.get(p.account)
        if g is None:
            g = NetWorthGroup(account=p.account)
            groups[p.account] = g
            seen_valuations[p.account] = set()
        g.value += p.market_value
        seen_valuations[p.account].add(p.valuation)
        # carry the earliest (most-stale) as-of in the group
        if p.as_of and (not g.as_of or p.as_of < g.as_of):
            g.as_of = p.as_of
    for acct, g in groups.items():
        vs = seen_valuations[acct]
        g.valuation = next(iter(vs)) if len(vs) == 1 else "mixed"
    nw = NetWorth(
        groups=sorted(groups.values(), key=lambda x: -x.value),
        total=snap.net_worth,
        live_value=snap.live_value,
        last_known_value=snap.last_known_value,
        static_value=snap.static_value,
    )
    nw.notes.append(
        "Full-picture net worth (brokerage + retirement + cash, across institutions). Static / "
        "last-known balances are manually synced — refresh from a current broker screenshot at "
        "the start of finance work. Excludes anything not in the corpus (e.g. real estate, vehicles)."
    )
    return nw


def concentration_treemap(svc: FinanceService) -> dict[str, object]:
    """LIVE net-worth concentration treemap data (the `treemap` contract), derived from holdings.

    Grouping is the semantic risk taxonomy — but the two isolated-by-design groups are derived,
    NOT hardcoded tickers: the concentrated single-stock = the lotted holding (`seed.concentrated`),
    the single-theme fund = the proxied mutual fund (`seed.proxy.fund`). Everything else falls to
    diversified-brokerage / retirement / cash by asset type. So the risk-isolation that made the
    hand-authored treemap legible stays fully config-derived — never hardcoded tickers."""
    seed = svc.reader.read_portfolio()
    snap = svc.positions()
    concentrated = seed.concentrated.symbol if seed.concentrated else None
    fund = seed.proxy.fund or None

    def group_of(sym: str, asset_type: str) -> str:
        if sym == concentrated:
            return "concentrated"
        if sym == fund:
            return "fund"
        if asset_type == "retirement":
            return "retirement"
        if asset_type == "cash":
            return "cash"
        return "brokerage"

    labels = {
        "concentrated": f"{concentrated} — concentrated single-stock" if concentrated else "Concentrated",
        "fund": f"{fund} — single-theme fund" if fund else "Single-theme fund",
        "brokerage": "Diversified brokerage",
        "retirement": "Retirement (locked)",
        "cash": "Cash",
    }
    nodes: list[dict[str, object]] = []
    present: list[str] = []
    for p in snap.positions:
        if p.market_value is None:
            continue
        g = group_of(p.symbol, p.asset_type)
        if g not in present:
            present.append(g)
        nodes.append(
            {
                "label": p.symbol,
                "value": round(p.market_value, 2),
                "group": g,
                "detail": {
                    "shares": round(p.shares, 4),
                    "avg_cost": p.avg_cost,
                    "cost_basis": p.cost_basis,
                    "account": p.account,
                    "valuation": p.valuation,
                },
            }
        )
    nodes.sort(key=lambda n: -float(n["value"]))  # type: ignore[arg-type]
    # Stable, risk-first group order (the isolated concentrations lead), filtered to those present.
    order = ["concentrated", "fund", "brokerage", "retirement", "cash"]
    groups = [{"key": k, "label": labels[k]} for k in order if k in present]
    return {
        "title": "Net-worth concentration — where the money actually is",
        "subtitle": (
            f"LIVE-derived {date.today().isoformat()} · "
            f"live ${snap.live_value:,.0f} + last-known ${snap.last_known_value:,.0f} + "
            f"static ${snap.static_value:,.0f} · net worth ${snap.net_worth:,.0f}"
        ),
        "groups": groups,
        "nodes": nodes,
    }


def allocation_pie(svc: FinanceService) -> dict[str, object]:
    """Current → Target allocation (the `pie` contract). The CURRENT pie is derived live — the
    liquid pool (every non-retirement holding, each its own slice). The TARGET pie is the ratified
    strategic plan, read from `allocation_target:` in portfolio.yaml (config-stored judgment, synced
    when the *strategy* changes — not per-fill). If no target is configured, only Current renders."""
    seed = svc.reader.read_portfolio()
    snap = svc.positions()
    liquid = [
        p for p in snap.positions if p.asset_type != "retirement" and p.market_value is not None
    ]
    liquid.sort(key=lambda p: -(p.market_value or 0.0))
    liquid_total = sum(p.market_value or 0.0 for p in liquid)
    current: dict[str, object] = {
        "label": "Current",
        "caption": f"~${liquid_total / 1000:,.0f}k liquid ({date.today().isoformat()}) · derived live",
        "slices": [{"label": p.symbol, "value": round(p.market_value or 0.0, 2)} for p in liquid],
    }
    pies: list[dict[str, object]] = [current]
    if seed.allocation_target and seed.allocation_target.slices:
        pies.append(
            {
                "label": "Target",
                "caption": seed.allocation_target.caption,
                "slices": [
                    {"label": s.label, "value": s.value} for s in seed.allocation_target.slices
                ],
            }
        )
    return {
        "title": "Allocation — current → target",
        "subtitle": "liquid pool · Current derived live from holdings · Target = ratified strategic plan",
        "pies": pies,
    }


def fund_proxy(svc: FinanceService,  now: datetime | None = None) -> ProxyEstimate:
    basket = svc.reader.read_portfolio().proxy
    weighted = bool(basket.weights)
    # Two quote lanes: exchange-listed names ride the Alpaca IEX feed; OTC
    # ADRs (invisible to IEX) ride the keyless Yahoo v8 client the global command already uses. The lane is
    # config-declared per name (source: yahoo), never guessed.
    alpaca_syms = [sym for sym in basket.symbols if basket.sources.get(sym, "alpaca") != "yahoo"]
    yahoo_syms = [sym for sym in basket.symbols if basket.sources.get(sym) == "yahoo"]
    quotes = svc.quote(alpaca_syms) if alpaca_syms else []
    yahoo_quotes: dict[str, Any] = {}
    if yahoo_syms:
        try:
            from harness.finance.providers.global_provider import fetch_global_quotes

            yahoo_quotes = fetch_global_quotes(yahoo_syms)
        except Exception:
            yahoo_quotes = {}  # lane-level outage degrades honestly to per-name missing below
    components: list[ProxyComponent] = []
    avail: list[tuple[float, float]] = []  # (move_pct, weight) — weight=1.0 in equal-weight mode
    missing: list[str] = []
    for q in quotes:
        w = basket.weights.get(q.symbol) if weighted else None
        if q.available and q.day_change_pct is not None:
            components.append(
                ProxyComponent(
                    symbol=q.symbol,
                    available=True,
                    price=q.price,
                    prev_close=q.prev_close,
                    move_pct=q.day_change_pct,
                    weight=w,
                    source="alpaca",
                )
            )
            avail.append((q.day_change_pct, w if (weighted and w is not None) else 1.0))
        else:
            components.append(ProxyComponent(symbol=q.symbol, available=False, weight=w, source="alpaca"))
            missing.append(q.symbol)
    for sym in yahoo_syms:
        w = basket.weights.get(sym) if weighted else None
        gq = yahoo_quotes.get(sym)
        if gq is not None and gq.change_pct is not None:
            prev = (
                round(gq.price - gq.change, 4)
                if (gq.price is not None and gq.change is not None)
                else None
            )
            components.append(
                ProxyComponent(
                    symbol=sym,
                    available=True,
                    price=gq.price,
                    prev_close=prev,
                    move_pct=gq.change_pct,
                    weight=w,
                    source="yahoo",
                )
            )
            avail.append((gq.change_pct, w if (weighted and w is not None) else 1.0))
        else:
            # thin OTC names miss prints some sessions — drop from the day's coverage, say so
            components.append(ProxyComponent(symbol=sym, available=False, weight=w, source="yahoo"))
            missing.append(sym)
    if weighted and basket.cash_weight > 0:
        # the fund's cash sweep: a real slice of the book that moves 0% by definition —
        # including it dampens the estimate exactly the way the fund's own cash does.
        components.append(
            ProxyComponent(
                symbol="CASH", available=True, move_pct=0.0,
                weight=basket.cash_weight, source="flat",
            )
        )
        avail.append((0.0, basket.cash_weight))

    weight_sum = sum(w for _, w in avail)
    estimate = sum(m * w for m, w in avail) / weight_sum if avail and weight_sum else None
    coverage = round(sum(basket.weights.values()) + basket.cash_weight, 2) if weighted else None
    live_coverage = round(sum(w for _, w in avail), 2) if weighted else None
    notes = [basket.note] if basket.note else []
    if missing:
        notes.append(
            f"Unavailable on the feed (OTC/ADR not covered): {', '.join(missing)}. "
            "Estimate uses the available names only."
        )
    if weighted:
        notes.append(
            f"CAP-WEIGHTED proxy (N-PORT weights): the basket represents "
            f"{coverage}% of the fund ({live_coverage}% quoted live this run). The unmapped "
            "tail is a disclosed blind spot — quiet-day sign flips remain possible when the "
            "tail moves against the basket; NAV truth is the EOD sync, never the proxy."
        )
    else:
        notes.append(
            "Equal-weight directional proxy ONLY — a cap-weighted fund won't match an equal-weight "
            "basket, so treat this as a rough sign-and-magnitude read, not a NAV prediction."
        )
    from harness.finance.sessions import us_session

    state, sdate = us_session(now)
    if state != "live":
        # First note, not a footnote: an off-hours estimate re-measures a struck session.
        notes.insert(0, (
            f"US session {state.upper()} — this estimate measures the {sdate:%a %m-%d} US session "
            f"(its NAV is already struck) plus thin off-hours ADR ticks, which move it without "
            "new information; Asia's live session is NOT in this number — read "
            "`hn finance global -s asia` for the next session's lean."
        ))
    return ProxyEstimate(
        fund=basket.fund,
        components=components,
        estimate_pct=estimate,
        available_count=len(avail),
        missing=missing,
        notes=notes,
        weighted=weighted,
        coverage_pct=coverage,
        live_coverage_pct=live_coverage,
        session_state=state,
        session_date=sdate.isoformat(),
    )


def daygl(svc: FinanceService) -> DayGL:
    """One glanceable day-G/L number for the WHOLE book. Composition, by valuation basis:
    live-quoted positions exact (cross-brokerage) · non-intraday funds estimated via the
    fund-proxy day% × last-known NAV value (honestly labeled est) · statics flat by definition.
    The est sleeve reconciles at the EOD NAV sync; statics stay honest via manual balance updates
    from broker statements."""
    snap = svc.positions()

    # exact sleeve — live quotes; track per-account split + quote gaps honestly
    accounts: dict[str, float] = {}
    quoted = 0
    gaps: list[str] = []
    live_value = 0.0
    for p in snap.positions:
        if p.valuation != "live":
            continue
        if p.day_gl is not None:
            quoted += 1
            live_value += p.market_value or 0.0
            accounts[p.account] = accounts.get(p.account, 0.0) + p.day_gl
        else:
            gaps.append(p.symbol)

    rows: list[DayGLRow] = [
        DayGLRow(
            label="Quoted sleeve",
            kind="exact",
            value=live_value,
            day_gl=snap.quoted_day_gl,
            day_pct=(
                snap.quoted_day_gl / (live_value - snap.quoted_day_gl) * 100.0
                if live_value - snap.quoted_day_gl
                else None
            ),
            detail=f"{quoted} live positions"
            + (f" · no quote: {', '.join(gaps)} (treated flat)" if gaps else ""),
        )
    ]

    # est sleeve — any last-known fund the proxy covers gets a proxy-estimated day move
    est_total: float | None = None
    est_coverage: float | None = None
    proxy: ProxyEstimate | None = None
    flat_value = snap.static_value
    for p in snap.positions:
        if p.valuation != "last_known" or not p.market_value:
            continue
        if proxy is None:
            try:
                proxy = svc.fund_proxy()
            except ProviderError:
                proxy = ProxyEstimate(fund="")  # no proxy reachable — funds fall to flat below
        if proxy.fund == p.symbol and proxy.estimate_pct is not None:
            est = p.market_value * proxy.estimate_pct / 100.0
            est_total = (est_total or 0.0) + est
            est_coverage = proxy.live_coverage_pct or proxy.coverage_pct
            rows.append(
                DayGLRow(
                    label=f"{p.symbol} (proxy est)",
                    kind="est",
                    value=p.market_value,
                    day_gl=est,
                    day_pct=proxy.estimate_pct,
                    detail=(
                        f"cap-weighted basket, {est_coverage or '?'}% of fund quoted"
                        if proxy.weighted
                        else "equal-weight basket (rough sign/magnitude only)"
                    ),
                )
            )
        else:
            flat_value += p.market_value
            rows.append(
                DayGLRow(
                    label=f"{p.symbol} (last-known NAV)",
                    kind="flat",
                    value=p.market_value,
                    detail="no proxy estimate — treated flat until the next NAV sync",
                )
            )

    rows.append(
        DayGLRow(
            label="Statics (retirement + cash)",
            kind="flat",
            value=snap.static_value,
            detail="flat intraday by definition — manually synced balances",
        )
    )

    total = snap.quoted_day_gl + (est_total or 0.0) if (quoted or est_total is not None) else None
    prior = snap.net_worth - total if total is not None else None
    return DayGL(
        total_day_gl=total,
        total_day_pct=(total / prior * 100.0 if total is not None and prior else None),
        exact_day_gl=snap.quoted_day_gl,
        est_day_gl=est_total,
        est_coverage_pct=est_coverage,
        flat_value=flat_value,
        net_worth=snap.net_worth,
        quoted_positions=quoted,
        quote_gaps=gaps,
        accounts=accounts,
        rows=rows,
        notes=[
            "Full-book day G/L: quoted sleeve exact + fund sleeve proxy-ESTIMATED + statics flat. "
            "The est is directional (proxy coverage is partial) — NAV truth arrives at the EOD "
            "sync, and static balances stay honest via manual balance updates from broker statements.",
            "Read-only observation, not a recommendation.",
        ],
    )
