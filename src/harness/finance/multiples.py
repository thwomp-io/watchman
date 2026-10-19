"""Valuation-multiples engine — GAAP-honest, deterministic, model-free.

Composes SEC EDGAR reported figures (keyless) + a live Alpaca price into the standard valuation
multiples, surfacing EVERY underlying component so the arithmetic is auditable:

    market cap        = shares_outstanding × live_price
    enterprise value  = market cap + total_debt − cash_and_equivalents
    EBITDA (TTM)      = operating_income_TTM + D&A_TTM
    EV/EBITDA · P/E (= mktcap / net_income_TTM) · P/S (= mktcap / revenue_TTM)

Why this exists: vendor multiples can mislabel premium names as "cheap" — valuation
research needs honest, source-traceable multiples, not a vendor's
black-box number. No Yahoo, no paid API; pure computation over reported facts + one live price.

The honesty rules are the whole point (NEVER fabricate, NEVER mislead):
- EBITDA ≤ 0 → EV/EBITDA = "N/M" (not a huge/negative float). Same: net income ≤ 0 → P/E = "N/M".
- A required component not reported under any XBRL tag we try → that multiple = "unavailable", with a
  note naming the missing piece.
- TTM assembly approximations (e.g. a quarter-gap because a filer reports Q4 only inside the 10-K) are
  flagged in `notes`, never silently smoothed over.

TTM doctrine:
- FLOW concepts (revenue, net income, operating income, D&A): sum the 4 most-recent DISCRETE QUARTERLY
  facts (`_classify_period` already drops YTD/partial spans). If <4 quarters are reported, fall back to
  the most-recent ANNUAL (10-K) fact.
- POINT-IN-TIME balance-sheet concepts (debt, cash, shares): take the most-recent INSTANT fact.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from harness.finance.models import (
    FundamentalFact,
    Multiples,
    MultiplesComponent,
    Quote,
)
from harness.finance.providers.edgar_provider import EdgarProvider

# Tag-fallback chains (first tag with facts wins), mirroring DEFAULT_CONCEPTS' discipline.
_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
# Net income: some filers report only ProfitLoss (incl. NCI), not NetIncomeLoss.
_NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic")
_PARENT_NI_TAGS = ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic")
_OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
# D&A is a cash-flow-statement concept; tags vary widely by filer. Combined tags tried first; if a
# filer reports it SPLIT (Depreciation + Amortization as separate lines), we sum them below.
_DA_TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
    "DepreciationDepletionAndAmortizationExcludingAmountsAttributableToAssetRetirementObligations",
)
_DEPRECIATION_TAGS = ("Depreciation", "DepreciationNonproduction")
_AMORTIZATION_TAGS = ("AmortizationOfIntangibleAssets",)
# Total debt = long-term (noncurrent) + current maturities; we SUM whichever components exist. The
# noncurrent chain includes convertibles/notes — debt-light software filers carry their debt there.
_LT_DEBT_NONCURRENT_TAGS = (
    "LongTermDebtNoncurrent",
    "ConvertibleDebtNoncurrent",
    "ConvertibleNotesPayableNoncurrent",
    "ConvertibleLongTermNotesPayable",
    "LongTermNotesPayableNoncurrent",
    "NotesPayableNoncurrent",
)
_LT_DEBT_CURRENT_TAGS = ("LongTermDebtCurrent",)
_SHORT_TERM_DEBT_TAGS = ("DebtCurrent", "ShortTermBorrowings")
# Fallbacks when the split isn't tagged — combined LT debt, then convertible/notes variants.
_LT_DEBT_TOTAL_TAGS = (
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligations",
    "ConvertibleDebt",
    "ConvertibleNotesPayable",
    "NotesPayable",
)
_CASH_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
# Shares are in the `dei` taxonomy under units.shares (NOT us-gaap/USD); diluted WANSO is the fallback.
_SHARES_DEI_TAGS = ("EntityCommonStockSharesOutstanding",)
_SHARES_GAAP_FALLBACK_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",)

# A clean trailing-twelve from 4 discrete quarters should span ~365 days; allow generous slack for
# 52/53-week fiscal calendars + a missing-quarter gap (e.g. a filer reporting Q4 only inside the 10-K).
_TTM_MIN_DAYS = 330
_TTM_MAX_DAYS = 400


def _quarter_facts(facts: list[FundamentalFact]) -> list[FundamentalFact]:
    return [f for f in facts if f.period_type == "quarter"]


def _annual_facts(facts: list[FundamentalFact]) -> list[FundamentalFact]:
    return [f for f in facts if f.period_type == "annual"]


def _ttm_flow(
    label: str, facts: list[FundamentalFact], tag: str, tags: tuple[str, ...]
) -> MultiplesComponent:
    """TTM for a flow concept: sum 4 most-recent discrete quarters, else most-recent annual.

    Facts arrive newest-first (the provider sorts by end date, deduped). Flags a quarter-gap when the
    4 quarters don't span ~12 months (a filer reporting Q4 only inside the 10-K — common; the value is
    still a defensible trailing-4-quarters, but the approximation is surfaced, never hidden)."""
    if not facts:
        return MultiplesComponent(
            label=label, note=f"not reported under tried tags: {', '.join(tags)}"
        )
    quarters, derived = _with_derived_q4(facts)
    annual = _annual_facts(facts)
    ytd = _ttm_from_ytd(facts)
    if ytd is not None and (len(quarters) < 4 or (quarters[0].end or "") < (ytd[1] or "")):
        return MultiplesComponent(
            label=label, value=ytd[0], tag=tag, period="4Q",
            note=f"TTM = last fiscal year + year-to-date to {ytd[1]} − the prior-year year-to-date "
            "(the filer reports cumulative quarters)",
        )
    if len(quarters) >= 4:
        window = quarters[:4]
        span = _window_span_days(window)
        if span is None or _TTM_MIN_DAYS <= span <= _TTM_MAX_DAYS:
            comp = MultiplesComponent(
                label=label, value=sum(f.value for f in window), tag=tag, period="4Q"
            )
            if any(f in derived for f in window):
                comp.note = (
                    "Q4 derived as the fiscal year minus Q1-Q3 (the filer reports Q4 only in the 10-K)"
                )
            return comp
        if annual:
            # A window that isn't a year (a gap, a fiscal-year change) would stitch ~15 months:
            # the latest annual is a clean twelve, so it wins, labeled.
            return MultiplesComponent(
                label=label, value=annual[0].value, tag=tag, period="annual",
                note=f"the 4 newest quarters span ~{span}d, not a year — using the latest annual "
                f"figure (FY ending {annual[0].end}) instead of stitching them",
            )
        return MultiplesComponent(
            label=label, tag=tag,
            note=f"the 4 newest quarters span ~{span}d, not a year, and no annual figure exists — "
            "TTM unavailable rather than stitched",
        )
    if annual:
        return MultiplesComponent(
            label=label,
            value=annual[0].value,
            tag=tag,
            period="annual",
            note=f"only {len(quarters)} discrete quarter(s) reported — using the most-recent annual "
            "(10-K) figure as the TTM proxy.",
        )
    return MultiplesComponent(
        label=label,
        tag=tag,
        note=f"reported, but neither 4 quarters nor an annual figure available "
        f"({len(quarters)} quarter(s), 0 annual).",
    )


def _revenue_ttm(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """Revenue (TTM): among the tags reporting the NEWEST period, the LARGER total wins. The ASC 606
    tag excludes lease income, so a lessor (towers, rental fleets, REITs) reports its real top line
    under `Revenues` — for a tower REIT the contract-revenue tag can read a small fraction of
    total revenue."""
    by_tag = provider.chain_facts(cik, _REVENUE_TAGS, include_ytd=True)
    if not by_tag:
        return _ttm_flow("Revenue (TTM)", [], "", _REVENUE_TAGS)
    newest = max(f[0].end or "" for f in by_tag.values())
    comps = [
        _ttm_flow("Revenue (TTM)", facts, tag, _REVENUE_TAGS)
        for tag, facts in by_tag.items() if (facts[0].end or "") == newest
    ]
    valued = [c for c in comps if c.value is not None]
    if not valued:
        # No single tag has a clean year (e.g. a filer that files two quarters under another tag): merge the
        # chain per period, the larger value where tags overlap, and assemble the TTM from that.
        merged: dict[tuple[str | None, str], FundamentalFact] = {}
        for facts in by_tag.values():
            for f in facts:
                k = (f.end, f.period_type)
                if k not in merged or f.value > merged[k].value:
                    merged[k] = f
        series = sorted(merged.values(), key=lambda f: f.end or "", reverse=True)
        comp = _ttm_flow("Revenue (TTM)", series, " ∪ ".join(by_tag), _REVENUE_TAGS)
        if comp.value is not None:
            comp.note = (comp.note + " · " if comp.note else "") + "merged across the revenue tags"
        return comp
    best = max(valued, key=lambda c: c.value or 0.0)
    if len(valued) > 1:
        others = ", ".join(f"{c.tag} {_b(c.value or 0.0)}" for c in valued if c is not best)
        extra = f"larger of the tags for this period (also {others})"
        best.note = f"{best.note} · {extra}" if best.note else extra
    return best


def _da_ttm(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """D&A (TTM): try the combined cash-flow tags first; if a filer reports it SPLIT (Depreciation +
    Amortization as separate lines), sum the TTM of each component. Amortization alone
    missing is tolerated (depreciation dominates for asset-heavy filers); depreciation missing → the
    combined-tag 'unavailable' is returned so EBITDA honestly reports the gap."""
    facts, _, tag = provider.concept_facts(cik, _DA_TAGS, include_ytd=True)
    combined = _ttm_flow("D&A (TTM)", facts, tag, _DA_TAGS)
    if combined.value is not None:
        return combined
    dep_facts, _, dep_tag = provider.concept_facts(cik, _DEPRECIATION_TAGS, include_ytd=True)
    dep = _ttm_flow("Depreciation", dep_facts, dep_tag, _DEPRECIATION_TAGS)
    if dep.value is None:
        return combined  # the 'unavailable' component (neither combined nor a depreciation tag)
    amort_facts, _, amort_tag = provider.concept_facts(cik, _AMORTIZATION_TAGS, include_ytd=True)
    amort = _ttm_flow("Amortization", amort_facts, amort_tag, _AMORTIZATION_TAGS)
    total = dep.value + (amort.value or 0.0)
    note = (
        "summed split D&A: Depreciation + Amortization of intangibles"
        if amort.value is not None
        else "summed split D&A: Depreciation only (no separate amortization-of-intangibles tag)"
    )
    return MultiplesComponent(
        label="D&A (TTM)", value=total, tag=f"{dep_tag}{' + ' + amort_tag if amort.value else ''}",
        period="4Q", note=note,
    )


def _with_derived_q4(facts: list[FundamentalFact]) -> tuple[list[FundamentalFact], list[FundamentalFact]]:
    """Discrete quarters, newest-first, plus a DERIVED Q4 (= fiscal year − the three reported
    quarters inside it) for each annual whose fourth quarter was never filed as a quarter — the
    common filer shape where Q4 lives only in the 10-K. Returns (quarters, the derived ones)."""
    from datetime import date, timedelta

    quarters = _quarter_facts(facts)
    derived: list[FundamentalFact] = []
    ends = {q.end for q in quarters}
    for a in _annual_facts(facts):
        if not a.start or not a.end or a.end in ends:
            continue
        inside = [q for q in quarters if q.start and q.end and a.start <= q.start and q.end <= a.end]
        if len(inside) != 3:
            continue
        last_end = max(q.end for q in inside if q.end)
        start = (date.fromisoformat(last_end) + timedelta(days=1)).isoformat()
        derived.append(a.model_copy(update={
            "value": a.value - sum(q.value for q in inside), "period_type": "quarter",
            "start": start, "fiscal_period": "Q4",
        }))
    merged = sorted([*quarters, *derived], key=lambda f: f.end or "", reverse=True)
    return merged, derived


def _ttm_from_ytd(facts: list[FundamentalFact]) -> tuple[float, str | None] | None:
    """TTM for a filer that reports cumulative (year-to-date) quarters: the latest fiscal year +
    the current YTD − the prior year's YTD of the same length. None when the pieces don't line up
    (the latest YTD must be newer than the latest annual, with its year-ago twin on file)."""
    from datetime import date

    def d(s: str | None) -> date | None:
        try:
            return date.fromisoformat(s) if s else None
        except ValueError:
            return None

    ytds = [f for f in facts if f.period_type == "ytd" and d(f.start) and d(f.end)]
    annual = _annual_facts(facts)
    if not ytds or not annual:
        return None
    cur = max(ytds, key=lambda f: f.end or "")
    fy = max(annual, key=lambda f: f.end or "")
    cur_start, cur_end, fy_end = d(cur.start), d(cur.end), d(fy.end)
    if not (cur_start and cur_end and fy_end) or not cur_end > fy_end:
        return None
    if abs((cur_start - fy_end).days) > 10:
        return None  # the YTD must start where the last fiscal year ended
    span = (cur_end - cur_start).days
    for f in ytds:
        fs, fe = d(f.start), d(f.end)
        if fs and fe and abs((cur_end - fe).days - 365) <= 10 and abs((fe - fs).days - span) <= 10:
            return fy.value + cur.value - f.value, cur.end
    return None


def _merge_by_period(
    by_tag: dict[str, list[FundamentalFact]], preference: tuple[str, ...]
) -> list[FundamentalFact]:
    """One series from several tags: per (end, period type) the first tag in `preference` that
    reports the period. Newest-first."""
    merged: dict[tuple[str | None, str], FundamentalFact] = {}
    for tag in reversed(preference):
        for f in by_tag.get(tag, []):
            merged[(f.end, f.period_type)] = f
    return sorted(merged.values(), key=lambda f: f.end or "", reverse=True)


def _window_span_days(window: list[FundamentalFact]) -> int | None:
    """Calendar days from the earliest fact's start to the latest fact's end across the 4-quarter
    window — the TTM coverage check (None if the spans aren't parseable)."""
    from datetime import date

    starts = [f.start for f in window if f.start]
    ends = [f.end for f in window if f.end]
    if not starts or not ends:
        return None
    try:
        return (date.fromisoformat(max(ends)) - date.fromisoformat(min(starts))).days
    except ValueError:
        return None


def _latest_instant(label: str, facts: list[FundamentalFact], tag: str) -> MultiplesComponent | None:
    """Most-recent point-in-time (balance-sheet) value, or None if nothing reported."""
    if not facts:
        return None
    return MultiplesComponent(label=label, value=facts[0].value, tag=tag, period="instant")


def compute_multiples(
    provider: EdgarProvider, symbol: str, cik: str, quote: Quote, *, entity_name: str = "",
    splits: list[tuple[str, float]] | None = None,
) -> Multiples:
    """The deterministic multiples computation. `provider` is a CIK→data EdgarProvider; `quote` is the
    live Alpaca quote (price comes from there). Pure: no model, no hidden state, every figure traced
    to its XBRL tag + TTM basis."""
    notes: list[str] = [
        "Multiples = SEC EDGAR reported figures (keyless, GAAP/XBRL) + a LIVE Alpaca price. Only the "
        "price is real-time; reported figures LAG (newest = most recent 10-Q/10-K).",
        "XBRL tags vary by issuer — a 'not reported under tried tags' component means the filer tags "
        "that concept differently, not that the figure is zero. Read-only; surfaces the math, the "
        "user judges.",
    ]
    m = Multiples(symbol=symbol.upper(), cik=cik, entity_name=entity_name, notes=notes)

    # --- live price ---
    if quote.available and quote.price is not None:
        m.price = quote.price
        m.price_as_of = quote.as_of
    else:
        m.notes.append(
            f"no live price for {symbol.upper()} (not on the feed — OTC/ADR/fund?): "
            f"{quote.note or 'unavailable'}. Market cap / EV / all multiples are unavailable."
        )

    # --- flow concepts (TTM) ---
    revenue = _revenue_ttm(provider, cik)

    ni_facts, ent, ni_tag = provider.concept_facts(cik, _NET_INCOME_TAGS, include_ytd=True)
    entity_name = entity_name or ent
    net_income = _ttm_flow("Net income (TTM)", ni_facts, ni_tag, _NET_INCOME_TAGS)
    if net_income.value is None:
        # Parent-attributable net income split across two tags that agree where they overlap
        # (e.g. a miner that files its second quarters under one and everything else under the
        # other). Merged per period; ProfitLoss (which includes the minority stake) never joins the merge.
        parent = provider.chain_facts(cik, _PARENT_NI_TAGS, include_ytd=True)
        if len(parent) > 1:
            merged = _ttm_flow(
                "Net income (TTM)", _merge_by_period(parent, _PARENT_NI_TAGS),
                " ∪ ".join(parent), _NET_INCOME_TAGS,
            )
            if merged.value is not None:
                extra = "merged across the parent net-income tags"
                merged.note = f"{merged.note} · {extra}" if merged.note else extra
                net_income = merged

    oi_facts, ent, oi_tag = provider.concept_facts(cik, _OPERATING_INCOME_TAGS, include_ytd=True)
    entity_name = entity_name or ent
    op_income = _ttm_flow("Operating income (TTM)", oi_facts, oi_tag, _OPERATING_INCOME_TAGS)

    da = _da_ttm(provider, cik)

    # --- balance-sheet instants ---
    debt = _total_debt(provider, cik)
    cash_facts, ent, cash_tag = provider.concept_facts(cik, _CASH_TAGS, include_instant=True)
    entity_name = entity_name or ent
    cash = _latest_instant("Cash & equivalents", cash_facts, cash_tag) or MultiplesComponent(
        label="Cash & equivalents", note=f"not reported under tried tags: {', '.join(_CASH_TAGS)}"
    )

    shares = apply_splits(_shares_outstanding(provider, cik), splits or [])
    nci_facts, _, nci_tag = provider.concept_facts(cik, ("MinorityInterest",), include_instant=True)
    nci = next((f for f in nci_facts if f.period_type == "instant"), None)

    if entity_name:
        m.entity_name = entity_name

    # --- EBITDA = operating income + D&A ---
    ebitda: MultiplesComponent
    if op_income.value is not None and da.value is not None:
        ebitda = MultiplesComponent(
            label="EBITDA (TTM)", value=op_income.value + da.value, period="4Q",
            note="= operating income + D&A (both TTM)",
        )
    else:
        missing = [c.label for c in (op_income, da) if c.value is None]
        ebitda = MultiplesComponent(
            label="EBITDA (TTM)", note=f"unavailable — missing {', '.join(missing)}"
        )

    # --- market cap + EV ---
    mktcap_comp: MultiplesComponent
    ev_comp: MultiplesComponent
    if m.price is not None and shares.value is not None:
        m.market_cap = m.price * shares.value
        mktcap_comp = MultiplesComponent(
            label="Market cap", value=m.market_cap, period="live",
            note="= shares outstanding × live price",
        )
    else:
        missing = []
        if m.price is None:
            missing.append("live price")
        if shares.value is None:
            missing.append("shares outstanding")
        mktcap_comp = MultiplesComponent(
            label="Market cap", note=f"unavailable — missing {', '.join(missing)}"
        )

    if m.market_cap is not None and debt.value is not None and cash.value is not None:
        m.enterprise_value = m.market_cap + debt.value - cash.value
        ev_note = "= market cap + total debt − cash"
        if nci is not None and nci.value > 0:
            # EBITDA is consolidated, so EV carries the minority stake too
            m.enterprise_value += nci.value
            ev_note += f" + non-controlling interest {_b(nci.value)} ({nci_tag}, {nci.end})"
        ev_comp = MultiplesComponent(
            label="Enterprise value", value=m.enterprise_value, period="live", note=ev_note,
        )
    else:
        missing = []
        if m.market_cap is None:
            missing.append("market cap")
        if debt.value is None:
            missing.append("total debt")
        if cash.value is None:
            missing.append("cash")
        ev_comp = MultiplesComponent(
            label="Enterprise value", note=f"unavailable — missing {', '.join(missing)}"
        )

    # Surface EVERY component (auditable), in a sensible reading order.
    m.components = [
        shares, _price_component(m), mktcap_comp, debt, cash, ev_comp,
        op_income, da, ebitda, net_income, revenue,
    ]

    # --- the multiples (honesty strings where a number would mislead) ---
    m.ev_ebitda = _ratio(m.enterprise_value, ebitda.value, denom_label="EBITDA")
    m.pe = _ratio(m.market_cap, net_income.value, denom_label="net income")
    m.notes.append(
        f"P/E is on GAAP net income ({net_income.tag or 'n/a'}, TTM) — a non-GAAP/adjusted P/E from a "
        "press release will read lower; name the basis when comparing."
    )
    m.ps = _ratio(m.market_cap, revenue.value, denom_label="revenue")
    return m


def _price_component(m: Multiples) -> MultiplesComponent:
    return MultiplesComponent(
        label="Live price", value=m.price, period="live",
        note=f"as of {m.price_as_of}" if m.price_as_of else ("unavailable" if m.price is None else ""),
    )


_DEBT_COMBINED_TOTAL = ("DebtLongtermAndShorttermCombinedAmount",)
_DEBT_INSTRUMENTS = ("DebtInstrumentCarryingAmount",)
_LT_INCL_CURRENT = ("LongTermDebt",)  # long-term debt INCLUDING current maturities
_CAPITAL_LEASE_NONCURRENT = ("LongTermDebtAndCapitalLeaseObligations",)
_CAPITAL_LEASE_CURRENT = ("LongTermDebtAndCapitalLeaseObligationsCurrent",)
_SHORT_TERM_ONLY = ("ShortTermBorrowings", "CommercialPaper", "OtherShortTermBorrowings")
_CONVERTIBLE_TOTAL = (
    "ConvertibleLongTermNotesPayable", "ConvertibleDebt", "ConvertibleNotesPayable", "NotesPayable",
)
# Last resort, used only when no complete construction exists at any date: a filer whose only debt
# tag is its notes (e.g. all debt reported solely as UnsecuredLongTermDebt). Kept out of the main
# pass because a single tranche on a newer date would otherwise beat a complete total.
_NOTES_ONLY = ("UnsecuredLongTermDebt", "SeniorLongTermNotes", "SeniorNotes")


def _instants(provider: EdgarProvider, cik: str, tags: tuple[str, ...]) -> dict[str, dict[str, float]]:
    """tag → {balance date → value} for every tag in `tags` that reports instants."""
    out: dict[str, dict[str, float]] = {}
    for tag, facts in provider.chain_facts(cik, tags, include_instant=True).items():
        dated = {f.end: f.value for f in facts if f.period_type == "instant" and f.end}
        if dated:
            out[tag] = dated
    return out


def _total_debt(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """Total debt from the most complete construction the filer tags, on ONE balance-sheet date.

    Filers tag debt in incompatible ways, and summing whatever pieces exist under-read by tens of
    billions (a total built from short-term borrowings alone, components from different quarters,
    `DebtCurrent` added on top of the current maturities it already contains). Each construction
    below is built per balance date from tags reported AT that date; the newest date wins, and among
    constructions on that date the largest (under-reading is the observed failure; double counting is
    prevented inside each construction). A short-term-only figure is never a headline."""
    tags = (*_DEBT_COMBINED_TOTAL, *_DEBT_INSTRUMENTS, *_LT_INCL_CURRENT, *_LT_DEBT_NONCURRENT_TAGS,
            *_LT_DEBT_CURRENT_TAGS, *_CAPITAL_LEASE_NONCURRENT, *_CAPITAL_LEASE_CURRENT,
            "DebtCurrent", *_SHORT_TERM_ONLY, *_CONVERTIBLE_TOTAL, *_NOTES_ONLY)
    inst = _instants(provider, cik, tags)

    def first(group: tuple[str, ...], d: str) -> tuple[str, float] | None:
        for t in group:
            if d in inst.get(t, {}):
                return t, inst[t][d]
        return None

    def short_term(d: str) -> list[tuple[str, float]]:
        return [(t, inst[t][d]) for t in _SHORT_TERM_ONLY if d in inst.get(t, {})]

    def current(d: str, lt_current: tuple[str, ...]) -> list[tuple[str, float]]:
        dc = first(("DebtCurrent",), d)
        if dc:  # DebtCurrent already includes current maturities + short-term borrowings
            return [dc]
        cur = first(lt_current, d)
        return ([cur] if cur else []) + short_term(d)

    dates = sorted({d for series in inst.values() for d in series}, reverse=True)
    from datetime import date as _date

    newest = dates[0] if dates else ""
    stale: MultiplesComponent | None = None
    for last_resort in (False, True):
        found = _debt_pass(dates, first, short_term, current, last_resort)
        if found is None:
            continue
        built = found.period_date or ""
        if built and newest and (_date.fromisoformat(newest) - _date.fromisoformat(built)).days > 200:
            stale = stale or found  # remember it; the last-resort pass may hold a current figure
            continue
        return found
    if stale is not None:
        return MultiplesComponent(
            label="Total debt",
            note=f"unavailable — the newest complete debt figure is from {stale.period_date} ("
            f"{_b(stale.value or 0.0)}) while the filer's balance sheet runs to {newest}; the current "
            "filings tag only pieces (a captive-finance filer splits its books) — read the balance sheet",
        )
    return MultiplesComponent(
        label="Total debt",
        note="unavailable — no long-term or total debt tag is reported (a short-term figure alone is "
        "not shown as total debt); read the balance sheet (`hn finance filing`)",
    )


_Pick = Callable[[tuple[str, ...], str], "tuple[str, float] | None"]


def _debt_pass(
    dates: list[str],
    first: _Pick,
    short_term: Callable[[str], list[tuple[str, float]]],
    current: Callable[[str, tuple[str, ...]], list[tuple[str, float]]],
    last_resort: bool,
) -> MultiplesComponent | None:
    for d in dates:
        options: list[tuple[str, list[tuple[str, float]]]] = []
        if last_resort:
            if (x := first(_NOTES_ONLY, d)):
                options.append(("the notes tag (no complete debt construction is tagged)",
                                [x, *current(d, _LT_DEBT_CURRENT_TAGS)]))
            if options:
                how, parts = options[0]
                return _debt_component(d, how, parts)
            continue
        if (x := first(_DEBT_COMBINED_TOTAL, d)):
            options.append(("the combined total tag", [x]))
        if (x := first(_LT_INCL_CURRENT, d)):
            options.append(("long-term debt incl. current maturities + short-term", [x, *short_term(d)]))
        if (x := first(_LT_DEBT_NONCURRENT_TAGS, d)):
            options.append(("noncurrent + current debt", [x, *current(d, _LT_DEBT_CURRENT_TAGS)]))
        if (x := first(_CAPITAL_LEASE_NONCURRENT, d)):
            options.append(("long-term debt & capital leases + current",
                            [x, *current(d, (*_CAPITAL_LEASE_CURRENT, *_LT_DEBT_CURRENT_TAGS))]))
        if (x := first(_DEBT_INSTRUMENTS, d)):
            options.append(("the debt-instrument carrying amount", [x]))
        if (x := first(_CONVERTIBLE_TOTAL, d)):
            options.append(("convertible / notes payable", [x]))
        if not options:
            continue  # a date with only short-term or lease pieces is not a total
        how, parts = max(options, key=lambda o: sum(v for _, v in o[1]))
        return _debt_component(d, how, parts)
    return None


def _debt_component(d: str, how: str, parts: list[tuple[str, float]]) -> MultiplesComponent:
    note = f"{how} at {d}: " + " + ".join(f"{t} {_b(v)}" for t, v in parts)
    return MultiplesComponent(
        label="Total debt", value=sum(v for _, v in parts), tag=" + ".join(t for t, _ in parts),
        period="instant", note=note, period_date=d,
    )


def _shares_outstanding(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """Shares outstanding: dei `EntityCommonStockSharesOutstanding` (the cover count) first — unless
    it is STALE against the newest reported quarter (a filer that moved to per-class cover counts
    leaves an undimensioned count years old: Mastercard's read 122.5M shares from 2010); then the
    us-gaap diluted weighted-average (newest quarter)."""
    from datetime import date

    dei_facts, _, _ = provider.concept_facts(
        cik, _SHARES_DEI_TAGS, taxonomy="dei", unit="shares", include_instant=True
    )
    wanso_peek, _, _ = provider.concept_facts(cik, _SHARES_GAAP_FALLBACK_TAGS, unit="shares")
    if dei_facts and wanso_peek and dei_facts[0].end and wanso_peek[0].end:
        gap = (date.fromisoformat(wanso_peek[0].end) - date.fromisoformat(dei_facts[0].end)).days
        if gap > 200:
            dei_facts = []  # stale cover count — fall through to the weighted average
    if dei_facts:
        return MultiplesComponent(
            label="Shares outstanding", value=dei_facts[0].value,
            tag=_SHARES_DEI_TAGS[0], period="instant",
            note=f"dei EntityCommonStockSharesOutstanding ({dei_facts[0].end})",
        )
    # fallback — diluted weighted-average (a duration/flow concept); take the newest quarter.
    wanso, _, _ = provider.concept_facts(cik, _SHARES_GAAP_FALLBACK_TAGS, unit="shares")
    if wanso:
        return MultiplesComponent(
            label="Shares outstanding", value=wanso[0].value,
            tag=_SHARES_GAAP_FALLBACK_TAGS[0], period="4Q",
            note="no current dei cover count — using diluted weighted-avg shares for the quarter "
            f"ending ({wanso[0].end})",
        )
    return MultiplesComponent(
        label="Shares outstanding",
        note="not reported under tried tags: "
        + ", ".join((*_SHARES_DEI_TAGS, *_SHARES_GAAP_FALLBACK_TAGS)),
    )


def apply_splits(
    shares: MultiplesComponent, splits: list[tuple[str, float]]
) -> MultiplesComponent:
    """Scale a share count for every split whose ex-date falls AFTER the count's date (the filing
    predates the split while the price is post-split: a 4:1 split otherwise reads P/S at a quarter of its
    true value).
    `splits` = [(ex_date ISO, new/old ratio)]."""
    as_of = ""
    m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", shares.note or "")
    if m:
        as_of = m.group(1)
    if shares.value is None or not as_of:
        return shares
    after = [(d, r) for d, r in splits if d > as_of and r > 0 and r != 1]
    if not after:
        return shares
    factor = 1.0
    for _, r in after:
        factor *= r
    desc = ", ".join(f"{r:g}:1 on {d}" for d, r in after)
    return shares.model_copy(update={
        "value": shares.value * factor,
        "note": (shares.note or "") + f" · ×{factor:g} for the split(s) since ({desc})",
    })


def _ratio(numerator: float | None, denominator: float | None, *, denom_label: str) -> float | str | None:
    """A multiple, with the honesty rules baked in:
    - denominator ≤ 0 (unprofitable) → "N/M" (a huge/negative ratio would mislead).
    - a missing input → "unavailable".
    - otherwise the rounded float."""
    if numerator is None or denominator is None:
        return "unavailable"
    if denominator <= 0:
        return "N/M"
    return round(numerator / denominator, 2)


def _b(v: float) -> str:
    """Compact billions for the debt-composition note."""
    return f"${v / 1e9:.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:.0f}M"
