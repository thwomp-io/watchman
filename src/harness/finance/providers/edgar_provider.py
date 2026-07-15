"""SEC EDGAR fundamentals provider — KEYLESS (data.sec.gov), UA-only.

Reported GAAP/XBRL financials for a holding via the `companyconcept` API. **Keyless**: SEC requires
only a descriptive User-Agent, which the shared `harness/<version>` UA already satisfies (see _http) —
so this is a read-only public-data GET with **no key and no new library** (the API-over-library opsec
posture — no key, no new library).

Resolution split: this provider is **CIK → data** only. Ticker → CIK is a separate static lookup
(`CikResolver` over the bundled company_tickers.json), because SEC serves the forward phonebook solely
from the WAF-blocked www.sec.gov while the data.sec.gov XBRL host is open. Callers pass a resolved CIK.

Two honesty constraints, both surfaced (never hidden):
- **Reported figures lag** — the newest data point is the most recent 10-Q/10-K, not real-time.
- **XBRL tags vary by issuer** — a concept may not exist under the tags we try (e.g. CRM tags revenue
  as `RevenueFromContractWithCustomerExcludingAssessedTax`, not `Revenues`). We try a fallback list;
  a genuine miss comes back as an empty series + a note, never a fabricated number.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.models import ConceptSeries, FundamentalFact, Fundamentals

_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json"

# (display label, candidate us-gaap tags tried in order). Revenue especially varies by issuer.
DEFAULT_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
    ),
    ("Net income", ("NetIncomeLoss",)),
    ("Operating income", ("OperatingIncomeLoss",)),
    ("Gross profit", ("GrossProfit",)),
    ("R&D expense", ("ResearchAndDevelopmentExpense",)),
)

_FORMS = {"10-Q", "10-K"}


class EdgarProvider:
    name = "edgar"

    def __init__(self, recent: int = 6) -> None:
        self.recent = recent
        self.request_count = 0

    # --- seam for tests: override to feed canned JSON instead of hitting the network ---
    def _raw_get(self, url: str) -> dict[str, Any] | None:
        """GET a JSON doc. Returns None on 404 — a concept tag a filer doesn't report is an EXPECTED
        miss in the fallback loop, not an error. Other failures raise ProviderError."""
        resp = get_with_retry(url, allow_status={404})
        self.request_count += 1
        if resp.status_code == 404:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None

    def _facts_for_tag(
        self,
        cik: str,
        tag: str,
        *,
        taxonomy: str = "us-gaap",
        unit: str = "USD",
        include_instant: bool = False,
    ) -> tuple[list[FundamentalFact], str]:
        """Fetch one concept; parse `units[unit]` into facts, newest-first, deduped. ([], "") if
        not reported.

        Flow concepts (income/cash-flow, with a start+end span) are classified quarter/annual and
        YTD/partial spans are dropped. Balance-sheet INSTANT concepts (debt/cash/shares — end only,
        no start) are skipped by default (the fundamentals command wants spans) and surfaced as
        `period_type="instant"` only when `include_instant=True` (the multiples command needs them).
        """
        raw = self._raw_get(
            _CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, tag=tag)
        )
        if not raw:
            return [], ""
        entity = str(raw.get("entityName", ""))
        rows = (raw.get("units") or {}).get(unit) or []
        facts: list[FundamentalFact] = []
        for item in rows:
            if not isinstance(item, dict) or item.get("form") not in _FORMS:
                continue
            start, end = item.get("start"), item.get("end")
            if start is None and end is not None:
                # instant / point-in-time (balance-sheet) — no duration span
                if not include_instant:
                    continue
                period_type = "instant"
            else:
                pt = _classify_period(start, end)
                if pt is None:  # year-to-date / partial — skip to avoid double-counting
                    continue
                period_type = pt
            facts.append(
                FundamentalFact(
                    value=float(item["val"]),
                    unit=unit,
                    fiscal_year=item.get("fy"),
                    fiscal_period=item.get("fp"),
                    period_type=period_type,
                    start=start,
                    end=end,
                    form=str(item.get("form", "")),
                    filed=item.get("filed"),
                )
            )
        return _dedupe_recent(facts, self.recent), entity

    def concept_facts(
        self,
        cik: str,
        tags: tuple[str, ...],
        *,
        taxonomy: str = "us-gaap",
        unit: str = "USD",
        include_instant: bool = False,
    ) -> tuple[list[FundamentalFact], str, str]:
        """Walk a tag-fallback chain (first tag with facts wins) and return (facts, entity_name,
        resolved_tag). ([], "", "") if none of the tags is reported. The multiples engine's building
        block — same fallback discipline as `get_fundamentals`, exposed for direct composition. The
        resolved tag rides back so callers don't re-walk the chain to label the source."""
        for tag in tags:
            facts, entity = self._facts_for_tag(
                cik, tag, taxonomy=taxonomy, unit=unit, include_instant=include_instant
            )
            if facts:
                return facts, entity, tag
        return [], "", ""

    def get_fundamentals(
        self,
        symbol: str,
        cik: str,
        *,
        entity_name: str = "",
        concepts: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CONCEPTS,
    ) -> Fundamentals:
        """Reported financials for a holding at the given (already-resolved) CIK. `entity_name` is a
        fallback display name (e.g. from the CikResolver) used if the XBRL responses carry none."""
        if not cik:
            raise ProviderError(f"no CIK provided for {symbol.upper()} — resolve it first.")
        resolved_name = entity_name
        series: list[ConceptSeries] = []
        for label, tags in concepts:
            resolved: ConceptSeries | None = None
            for tag in tags:
                facts, entity = self._facts_for_tag(cik, tag)
                if entity and not resolved_name:
                    resolved_name = entity
                if facts:
                    resolved = ConceptSeries(label=label, tag=tag, facts=facts)
                    break
            if resolved is None:
                resolved = ConceptSeries(
                    label=label, note=f"not reported under tried tags: {', '.join(tags)}"
                )
            series.append(resolved)
        return Fundamentals(
            symbol=symbol.upper(),
            cik=cik,
            entity_name=resolved_name,
            concepts=series,
            notes=[
                "Reported GAAP/XBRL figures from SEC EDGAR — they LAG real-time (newest = most recent "
                "10-Q/10-K filing). Read-only observation; the sounding-board surfaces, the user judges.",
                "Quarterly + annual periods only; year-to-date facts dropped to avoid double-count. "
                "A concept with no facts wasn't reported under the tags tried (XBRL varies by issuer).",
            ],
        )


def _classify_period(start: str | None, end: str | None) -> str | None:
    """quarter (~3mo) | annual (~12mo) from the reporting span. None = YTD/partial (skip)."""
    if not start or not end:
        return "annual"  # instant/point-in-time facts (rare for these concepts) — treat as annual
    try:
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None
    if 80 <= span <= 100:
        return "quarter"
    if span >= 350:
        return "annual"
    return None


def _dedupe_recent(facts: list[FundamentalFact], recent: int) -> list[FundamentalFact]:
    """Dedupe by (end date, period_type) keeping the latest-filed (restatements win), newest-first by
    end date, capped to `recent`.

    Keyed on the fact's actual reporting period (its END date), NOT the issuer-reported (fy, fp)
    labels — those are the *filing context's* fiscal markers and prove unreliable in practice (COST
    tags a May-2025-ending quarter as `fy=2026 fp=Q3`, off by a fiscal year), which collapsed genuinely
    distinct quarters and silently corrupted any TTM sum built on top. The (end, period_type) key is
    the fact's true identity: two facts covering the same period to the same end ARE the same period
    (restatement → latest filed wins); different ends are different periods."""
    best: dict[tuple[str | None, str], FundamentalFact] = {}
    for f in facts:
        key = (f.end, f.period_type)
        cur = best.get(key)
        if cur is None or (f.filed or "") > (cur.filed or ""):
            best[key] = f
    ordered = sorted(best.values(), key=lambda f: (f.end or "", f.filed or ""), reverse=True)
    return ordered[:recent]


def build_edgar_provider(recent: int = 6) -> EdgarProvider:
    return EdgarProvider(recent=recent)
