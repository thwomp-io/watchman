"""Valuation-multiples engine — GAAP-honest, deterministic, model-free.

Composes SEC EDGAR reported figures (keyless) + a live Alpaca price into the standard valuation
multiples, surfacing EVERY underlying component so the arithmetic is auditable:

    market cap        = shares_outstanding × live_price
    enterprise value  = market cap + total_debt − cash_and_equivalents
    EBITDA (TTM)      = operating_income_TTM + D&A_TTM
    EV/EBITDA · P/E (= mktcap / net_income_TTM) · P/S (= mktcap / revenue_TTM)

Why this exists: vendor multiples can mislabel premium names as "cheap" — the screened-core
engine + research profiles need honest, source-traceable multiples, not a vendor's
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
# Net income: some filers (e.g. ITW) report only ProfitLoss (incl. NCI), not NetIncomeLoss.
_NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic")
_OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
# D&A is a cash-flow-statement concept; tags vary widely by filer. Combined tags tried first; if a
# filer reports it SPLIT (Depreciation + Amortization as separate lines, e.g. ITW), we sum them below.
_DA_TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
    "DepreciationDepletionAndAmortizationExcludingAmountsAttributableToAssetRetirementObligations",
)
_DEPRECIATION_TAGS = ("Depreciation", "DepreciationNonproduction")
_AMORTIZATION_TAGS = ("AmortizationOfIntangibleAssets",)
# Total debt = long-term (noncurrent) + current maturities; we SUM whichever components exist. The
# noncurrent chain includes convertibles/notes — debt-light software (DDOG, NTNX) carries its debt there.
_LT_DEBT_NONCURRENT_TAGS = (
    "LongTermDebtNoncurrent",
    "ConvertibleDebtNoncurrent",
    "ConvertibleNotesPayableNoncurrent",
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
    quarters = _quarter_facts(facts)
    if len(quarters) >= 4:
        window = quarters[:4]
        value = sum(f.value for f in window)
        comp = MultiplesComponent(label=label, value=value, tag=tag, period="4Q")
        span = _window_span_days(window)
        if span is not None and not (_TTM_MIN_DAYS <= span <= _TTM_MAX_DAYS):
            comp.note = (
                f"4 most-recent quarters span ~{span}d (not ~365d) — likely a missing-quarter gap "
                "(filer reports Q4 only inside the 10-K); TTM is an approximation from those quarters."
            )
        return comp
    annual = _annual_facts(facts)
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


def _da_ttm(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """D&A (TTM): try the combined cash-flow tags first; if a filer reports it SPLIT (Depreciation +
    Amortization as separate lines — ITW does this), sum the TTM of each component. Amortization alone
    missing is tolerated (depreciation dominates for asset-heavy filers); depreciation missing → the
    combined-tag 'unavailable' is returned so EBITDA honestly reports the gap."""
    facts, _, tag = provider.concept_facts(cik, _DA_TAGS)
    combined = _ttm_flow("D&A (TTM)", facts, tag, _DA_TAGS)
    if combined.value is not None:
        return combined
    dep_facts, _, dep_tag = provider.concept_facts(cik, _DEPRECIATION_TAGS)
    dep = _ttm_flow("Depreciation", dep_facts, dep_tag, _DEPRECIATION_TAGS)
    if dep.value is None:
        return combined  # the 'unavailable' component (neither combined nor a depreciation tag)
    amort_facts, _, amort_tag = provider.concept_facts(cik, _AMORTIZATION_TAGS)
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
    provider: EdgarProvider, symbol: str, cik: str, quote: Quote, *, entity_name: str = ""
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
    rev_facts, ent, rev_tag = provider.concept_facts(cik, _REVENUE_TAGS)
    entity_name = entity_name or ent
    revenue = _ttm_flow("Revenue (TTM)", rev_facts, rev_tag, _REVENUE_TAGS)

    ni_facts, ent, ni_tag = provider.concept_facts(cik, _NET_INCOME_TAGS)
    entity_name = entity_name or ent
    net_income = _ttm_flow("Net income (TTM)", ni_facts, ni_tag, _NET_INCOME_TAGS)

    oi_facts, ent, oi_tag = provider.concept_facts(cik, _OPERATING_INCOME_TAGS)
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

    shares = _shares_outstanding(provider, cik)

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
        ev_comp = MultiplesComponent(
            label="Enterprise value", value=m.enterprise_value, period="live",
            note="= market cap + total debt − cash",
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
    m.ps = _ratio(m.market_cap, revenue.value, denom_label="revenue")
    return m


def _price_component(m: Multiples) -> MultiplesComponent:
    return MultiplesComponent(
        label="Live price", value=m.price, period="live",
        note=f"as of {m.price_as_of}" if m.price_as_of else ("unavailable" if m.price is None else ""),
    )


def _total_debt(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """Total debt = LongTermDebtNoncurrent + LongTermDebtCurrent (+ short-term borrowings), summing
    the components that exist. Falls back to the single `LongTermDebt` tag when the split isn't
    reported. A filer with NO debt tag at all → unavailable (named), never assumed zero."""
    noncurrent, _, nc_tag = provider.concept_facts(cik, _LT_DEBT_NONCURRENT_TAGS, include_instant=True)
    current, _, _ = provider.concept_facts(cik, _LT_DEBT_CURRENT_TAGS, include_instant=True)
    short_term, _, st_tag = provider.concept_facts(cik, _SHORT_TERM_DEBT_TAGS, include_instant=True)

    parts: list[str] = []
    tags_used: list[str] = []
    total = 0.0
    found = False
    if noncurrent:
        total += noncurrent[0].value
        parts.append(f"LT-noncurrent {_b(noncurrent[0].value)}")
        tags_used.append(nc_tag)
        found = True
    if current:
        total += current[0].value
        parts.append(f"LT-current {_b(current[0].value)}")
        tags_used.append(_LT_DEBT_CURRENT_TAGS[0])
        found = True
    if short_term:
        total += short_term[0].value
        parts.append(f"short-term {_b(short_term[0].value)}")
        tags_used.append(st_tag)
        found = True

    if found:
        return MultiplesComponent(
            label="Total debt", value=total, tag=" + ".join(t for t in tags_used if t),
            period="instant", note="sum of " + " + ".join(parts),
        )
    # fallback: single combined LongTermDebt tag
    combined, _, combined_tag = provider.concept_facts(cik, _LT_DEBT_TOTAL_TAGS, include_instant=True)
    if combined:
        return MultiplesComponent(
            label="Total debt", value=combined[0].value, tag=combined_tag,
            period="instant", note="from the combined LongTermDebt tag (issuer didn't split cur/noncur)",
        )
    tried = (*_LT_DEBT_NONCURRENT_TAGS, *_LT_DEBT_CURRENT_TAGS, *_SHORT_TERM_DEBT_TAGS, *_LT_DEBT_TOTAL_TAGS)
    return MultiplesComponent(
        label="Total debt", note="not reported under tried tags: " + ", ".join(tried)
    )


def _shares_outstanding(provider: EdgarProvider, cik: str) -> MultiplesComponent:
    """Shares outstanding: dei `EntityCommonStockSharesOutstanding` (instant, units.shares) first;
    fallback to us-gaap diluted weighted-average (a flow → newest quarter's value)."""
    dei_facts, _, _ = provider.concept_facts(
        cik, _SHARES_DEI_TAGS, taxonomy="dei", unit="shares", include_instant=True
    )
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
            note="dei shares not reported — using diluted weighted-avg shares (latest quarter) as a proxy",
        )
    return MultiplesComponent(
        label="Shares outstanding",
        note="not reported under tried tags: "
        + ", ".join((*_SHARES_DEI_TAGS, *_SHARES_GAAP_FALLBACK_TAGS)),
    )


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
