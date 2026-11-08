"""Book valuation — positions, net worth, the concentration treemap, the allocation pie, the fund
proxy and the full-book day G/L. Functions of the service (quotes + the portfolio seed).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    pass

from harness.finance.models import (
    AfterHours,
    AfterHoursRow,
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
from harness.finance.sessions import us_session

if TYPE_CHECKING:
    from harness.finance.service import FinanceService

def positions(svc: FinanceService, now: datetime | None = None) -> PortfolioSnapshot:
    """The portfolio priced now. During the regular session a quoted holding is priced at its latest
    trade; once the session is over (after the bell, overnight, weekends) at that session's official
    close, so an after-hours print (an earnings reaction) never lands in an end-of-day number. The
    basis is stated in `price_basis` and the notes."""
    seed = svc.reader.read_portfolio()
    quotable = [h for h in seed.holdings if h.quotable]
    quotes = {q.symbol: q for q in svc.quote([h.symbol for h in quotable])} if quotable else {}
    state, session = us_session(now)
    closes = svc.session_closes([h.symbol for h in quotable], session) if quotable and state != "live" else {}

    snap = PortfolioSnapshot()
    if state != "live":
        sources = {c.source for c in closes.values()}
        snap.price_basis = f"close {session.isoformat()}"
        snap.notes.append(
            f"The regular session is over: quoted positions are valued at the {session.isoformat()} "
            f"close ({' + '.join(sorted(sources)) or 'none available'}), not after-hours prints."
        )
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
        closed = closes.get(h.symbol.upper())
        if h.valuation == "live" and closed is not None:
            pos.price = closed.close
            pos.market_value = closed.close * h.shares
            pos.unrealized_gl = pos.market_value - h.cost_basis
            if h.cost_basis:
                pos.unrealized_gl_pct = pos.unrealized_gl / h.cost_basis * 100.0
            if closed.prior_close:
                pos.day_change_pct = (closed.close / closed.prior_close - 1.0) * 100.0
                pos.day_gl = (closed.close - closed.prior_close) * h.shares
            pos.note = f"{closed.session} close ({closed.source})"
            snap.live_value += pos.market_value
            snap.quoted_cost_basis += h.cost_basis
            snap.quoted_unrealized_gl += pos.unrealized_gl
            if pos.day_gl is not None:
                snap.quoted_day_gl += pos.day_gl
        elif h.valuation == "live" and q is not None and q.available and q.price is not None:
            if state != "live":
                pos.note = f"no {session.isoformat()} close available: priced at the latest trade"
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
        price_basis=snap.price_basis,
    )
    nw.notes.extend(n for n in snap.notes if n.startswith("The regular session is over"))
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


def fund_proxy(svc: FinanceService, now: datetime | None = None) -> ProxyEstimate:
    """A mutual fund's direction from its roster's live proxies: the basket names, each at its N-PORT
    weight; the cash sweep flat; and, with `fills:` configured, one exposure ETF per region standing in
    for the roster names the basket can't quote (and any basket name unavailable this run).

    Pricing follows the session it measures. During the regular session the Alpaca lane reads the last
    trade; once it's over, the official close against the prior close (the basis `positions` uses), so an
    evening read stops drifting on after-hours prints. OTC names are read from their daily bars and count
    only with a trade on the session and a real close on the session before (`proxy_fills.otc_move`)."""
    from harness.finance import proxy_fills
    from harness.finance.providers import global_provider
    from harness.finance.sessions import us_session

    basket = svc.reader.read_portfolio().proxy
    weighted = bool(basket.weights)
    state, sdate = us_session(now)
    fill_syms = [f.symbol for f in basket.fills] if weighted else []
    alpaca_syms = [sym for sym in basket.symbols if basket.sources.get(sym, "alpaca") != "yahoo"]
    yahoo_syms = [sym for sym in basket.symbols if basket.sources.get(sym) == "yahoo"]

    exchange = list(dict.fromkeys(alpaca_syms + fill_syms))
    quotes = {q.symbol: q for q in svc.quote(exchange)} if exchange else {}
    closes = svc.session_closes(exchange, sdate) if exchange and state != "live" else {}
    priced: dict[str, ProxyComponent] = {}
    for sym in exchange:
        q, close = quotes.get(sym), closes.get(sym)
        if close is not None and close.prior_close:
            priced[sym] = ProxyComponent(
                symbol=sym, price=close.close, prev_close=close.prior_close,
                move_pct=(close.close / close.prior_close - 1.0) * 100.0,
                basis=f"close {sdate.isoformat()}",
            )
        elif q is not None and q.available and q.day_change_pct is not None:
            basis = "last trade" if state == "live" else f"last trade (no official {sdate.isoformat()} close)"
            priced[sym] = ProxyComponent(symbol=sym, price=q.price, prev_close=q.prev_close,
                                         move_pct=q.day_change_pct, basis=basis)

    try:
        histories = global_provider.fetch_daily_history(yahoo_syms) if yahoo_syms else {}
    except Exception:  # noqa: BLE001 — a lane-level outage degrades to per-name unavailable below
        histories = {}
    prior = proxy_fills.previous_session(list(histories.values()), sdate)

    components: list[ProxyComponent] = []
    avail: list[tuple[float, float]] = []  # (move_pct, weight) — weight=1.0 in equal-weight mode
    missing: list[str] = []
    for sym in basket.symbols:
        w = basket.weights.get(sym) if weighted else None
        lane = "yahoo" if basket.sources.get(sym) == "yahoo" else "alpaca"
        if lane == "yahoo":
            r = proxy_fills.otc_move(histories.get(sym), sdate, prior)
            c = ProxyComponent(symbol=sym, available=r.ok, price=r.price, prev_close=r.prev_close,
                               move_pct=r.move_pct, basis=r.basis, reason=r.reason)
        else:
            c = priced.get(sym) or ProxyComponent(symbol=sym, available=False,
                                                  reason="no quote")
        c.weight, c.source = w, lane
        components.append(c)
        if c.available and c.move_pct is not None:
            avail.append((c.move_pct, w if (weighted and w is not None) else 1.0))
        else:
            missing.append(sym)

    if weighted and basket.cash_weight > 0:
        # the fund's cash sweep: a real slice of the book that moves 0% by definition —
        # including it dampens the estimate exactly the way the fund's own cash does.
        components.append(ProxyComponent(symbol="CASH", available=True, move_pct=0.0,
                                         weight=basket.cash_weight, source="flat"))
        avail.append((0.0, basket.cash_weight))
    live_coverage = round(sum(w for _, w in avail), 2) if weighted else None

    plan = proxy_fills.FillPlan()
    if fill_syms:
        roster = proxy_fills.read_roster(basket.holdings_ref)
        plan = proxy_fills.plan_fills(roster, basket.weights, set(missing), basket.fills)
        for f in basket.fills:
            w = round(plan.weights.get(f.exposure, 0.0), 2)
            if not w:
                continue
            got = priced.get(f.symbol)
            c = got.model_copy() if got else ProxyComponent(symbol=f.symbol, available=False,
                                                            reason="no quote")
            c.weight, c.source, c.exposure = w, "fill", f.exposure
            c.fills_for = plan.parts.get(f.exposure, [])
            components.append(c)
            if c.available and c.move_pct is not None:
                avail.append((c.move_pct, w))

    weight_sum = sum(w for _, w in avail)
    estimate = sum(m * w for m, w in avail) / weight_sum if avail and weight_sum else None
    coverage = round(sum(basket.weights.values()) + basket.cash_weight, 2) if weighted else None
    filled = round(weight_sum, 2) if (weighted and fill_syms) else None
    notes = [basket.note] if basket.note else []
    gone = [c for c in components if not c.available]
    if gone:
        listed = "; ".join(f"{c.symbol} ({c.reason or 'no quote'})" for c in gone)
        notes.append(f"Unavailable: {listed}.")
    if weighted:
        notes.append(
            f"CAP-WEIGHTED proxy (N-PORT weights): the basket represents {coverage}% of the fund "
            f"({live_coverage}% quoted this run)."
            + (f" Exposure fills take it to {filled}%: each fill stands in for the roster names the basket "
               "can't quote in that region, plus any basket name unavailable this run."
               + (f" {plan.unfilled}% fits no fill and is spread over the rest." if plan.unfilled else "")
               + (" Over 100% is the filing's own: its positions sum past 100 when other assets net negative."
                  if filled is not None and filled > 100 else "")
               if filled is not None else
               " The unmapped tail is spread over the quoted names, so a selloff concentrated there reads "
               "shallow.")
            + " NAV truth is the EOD sync, never the proxy."
        )
        if fill_syms and not proxy_fills.read_roster(basket.holdings_ref):
            notes.append("Fills are configured but the roster file wasn't found, so none were weighed.")
    else:
        notes.append(
            "Equal-weight directional proxy ONLY — a cap-weighted fund won't match an equal-weight "
            "basket, so treat this as a rough sign-and-magnitude read, not a NAV prediction."
        )
    if state != "live":
        # First note, not a footnote: an off-hours estimate re-measures a struck session.
        notes.insert(0, (
            f"US session {state.upper()} — this estimate measures the {sdate:%a %m-%d} US session "
            f"(its NAV is already struck) at the official close where one exists; Asia's live session is "
            "NOT in this number — read `hn finance global -s asia` for the next session's lean."
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
        filled_coverage_pct=filled,
        session_state=state,
        session_date=sdate.isoformat(),
    )


def after_hours(svc: FinanceService, now: datetime | None = None) -> AfterHours:
    """The quoted sleeve at its latest extended-hours trades vs the last regular close: the implied
    move into the next open. Only outside the regular session (after the bell, overnight, pre-market);
    the anchor is the same official close `positions` uses once the session is over. Fund NAVs and
    static balances are not in it (no extended-hours price exists for them)."""
    state, session = us_session(now)
    if state == "live":
        raise ProviderError(
            "the regular session is live: there are no extended-hours prints to read yet "
            "(`hn finance daygl` is the session's move)"
        )
    seed = svc.reader.read_portfolio()
    live = [h for h in seed.holdings if h.quotable and h.valuation == "live" and h.shares]
    symbols = sorted({h.symbol.upper() for h in live})
    closes = svc.session_closes(symbols, session) if symbols else {}
    trades = svc.extended_trades(symbols, session) if symbols else {}
    out = AfterHours(session=session.isoformat())
    for h in live:
        sym = h.symbol.upper()
        row = AfterHoursRow(symbol=sym, account=h.account, shares=h.shares)
        close, trade = closes.get(sym), trades.get(sym)
        row.close = close.close if close else None
        if close is None:
            row.note = f"no {session.isoformat()} close"
        elif trade is None:
            row.note = "no extended-hours trade"
        else:
            row.price, row.t = trade.price, trade.t
            row.move = (trade.price - close.close) * h.shares
            row.move_pct = (trade.price / close.close - 1.0) * 100.0
            out.total_move += row.move
            out.value_at_close += close.close * h.shares
        if row.move is None:
            out.excluded.append(sym)
        out.rows.append(row)
    out.rows.sort(key=lambda r: (r.move is None, -abs(r.move or 0.0)))
    if out.value_at_close:
        out.total_pct = out.total_move / out.value_at_close * 100.0
    funds = [h.symbol for h in seed.holdings if h.valuation == "last_known"]
    statics = [h.symbol for h in seed.holdings if h.valuation == "static"]
    if funds:
        out.notes.append(
            f"Not in it: {', '.join(funds)} (a fund strikes NAV at the close; for its direction "
            "`hn finance fund-proxy` + the Asia rows of `hn finance global`)."
        )
    if statics:
        out.notes.append(f"Static balances, flat by definition: {', '.join(statics)}.")
    out.notes.append(
        "Thin-print estimate, not a forecast: extended-hours volume is a sliver of the session's and "
        "the open can land anywhere."
    )
    return out


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
