"""SEC EDGAR fundamentals provider — KEYLESS (data.sec.gov), UA-only.

Reported GAAP/XBRL financials for a holding via the `companyconcept` API. **Keyless**: SEC requires
only a descriptive User-Agent, which the shared `harness/<version>` UA already satisfies (see _http) —
so this is a read-only public-data GET with **no key and no new library** (the API-over-library opsec
posture — no key, no new library).

Source: the per-company **`companyfacts`** document first (one request, every tag, cached
per provider instance), the per-tag `companyconcept` API as the fallback. The per-tag store proved
INCOMPLETE for some filers — it served some filers a hollow document (`units.USD = {}`) while
`companyfacts` carried the full history, so a whole name read "not reported" on every tag.

Tag choice: every tag in a fallback chain is read and the one whose NEWEST fact is latest wins (chain
order breaks ties). "First tag with any facts" served stale series — a filer that switched tags years
ago kept its old one (Mastercard's 2010 share count, fundamentals rows from 2013-2018).

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
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
STALE_DAYS = 450  # a concept whose newest fact is older than this is flagged stale

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
    ("Net income", ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss")),
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
        self._facts_cache: dict[str, dict[str, Any] | None] = {}

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
        include_ytd: bool = False,
    ) -> tuple[list[FundamentalFact], str]:
        """Fetch one concept; parse `units[unit]` into facts, newest-first, deduped. ([], "") if
        not reported.

        Flow concepts (income/cash-flow, with a start+end span) are classified quarter/annual and
        YTD/partial spans are dropped. Balance-sheet INSTANT concepts (debt/cash/shares — end only,
        no start) are skipped by default (the fundamentals command wants spans) and surfaced as
        `period_type="instant"` only when `include_instant=True` (the multiples command needs them).
        """
        raw = self._concept_doc(cik, tag, taxonomy)
        if not raw:
            return [], ""
        entity = str(raw.get("entityName", ""))
        rows = (raw.get("units") or {}).get(unit) or []
        if not isinstance(rows, list):
            rows = []  # a hollow document (`units.USD = {}`) — seen live on the per-tag store
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
                if pt is None:  # partial span — skip
                    continue
                if pt == "ytd" and not include_ytd:
                    continue  # year-to-date: kept only for callers that difference it (TTM)
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

    def _company_facts(self, cik: str) -> dict[str, Any] | None:
        if cik not in self._facts_cache:
            try:
                self._facts_cache[cik] = self._raw_get(_FACTS_URL.format(cik=cik))
            except ProviderError:
                self._facts_cache[cik] = None
        return self._facts_cache[cik]

    def _concept_doc(self, cik: str, tag: str, taxonomy: str) -> dict[str, Any] | None:
        """One concept's document: from `companyfacts` when that document has the tag, else the
        per-tag endpoint (see the module docstring for why that order)."""
        company = self._company_facts(cik)
        if company:
            node = ((company.get("facts") or {}).get(taxonomy) or {}).get(tag)
            if isinstance(node, dict):
                return {"entityName": company.get("entityName", ""), "units": node.get("units") or {}}
            if (company.get("facts") or {}).get(taxonomy):
                return None  # companyfacts is complete: the filer does not report this tag
        return self._raw_get(_CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, tag=tag))

    def concept_facts(
        self,
        cik: str,
        tags: tuple[str, ...],
        *,
        taxonomy: str = "us-gaap",
        unit: str = "USD",
        include_instant: bool = False,
        include_ytd: bool = False,
    ) -> tuple[list[FundamentalFact], str, str]:
        """Read every tag in a fallback chain and return (facts, entity_name, resolved_tag) for the tag
        whose NEWEST fact is latest (chain order breaks ties). ([], "", "") if none is reported. The
        multiples engine's building block; the resolved tag rides back to label the source."""
        best: tuple[list[FundamentalFact], str, str] = ([], "", "")
        best_end = ""
        for tag in tags:
            facts, entity = self._facts_for_tag(
                cik, tag, taxonomy=taxonomy, unit=unit, include_instant=include_instant,
                include_ytd=include_ytd,
            )
            end = facts[0].end or "" if facts else ""
            if facts and end > best_end:
                best, best_end = (facts, entity, tag), end
        return best

    def chain_facts(
        self,
        cik: str,
        tags: tuple[str, ...],
        *,
        taxonomy: str = "us-gaap",
        unit: str = "USD",
        include_instant: bool = False,
        include_ytd: bool = False,
    ) -> dict[str, list[FundamentalFact]]:
        """Every tag in the chain that has facts → its facts (newest-first). For callers that
        combine tags (revenue picks the larger of two tags reporting the same period)."""
        out: dict[str, list[FundamentalFact]] = {}
        for tag in tags:
            facts, _ = self._facts_for_tag(
                cik, tag, taxonomy=taxonomy, unit=unit, include_instant=include_instant,
                include_ytd=include_ytd,
            )
            if facts:
                out[tag] = facts
        return out

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
        stale: list[str] = []
        for label, tags in concepts:
            facts, entity, tag = self.concept_facts(cik, tags)
            if entity and not resolved_name:
                resolved_name = entity
            if facts:
                resolved = ConceptSeries(label=label, tag=tag, facts=facts)
                if _age_days(facts[0].end) > STALE_DAYS:
                    resolved.note = f"STALE — the newest reported fact ends {facts[0].end}"
                    stale.append(label)
            else:
                resolved = ConceptSeries(
                    label=label, note=f"not reported under tried tags: {', '.join(tags)}"
                )
            series.append(resolved)
        notes_extra = (
            [f"STALE series: {', '.join(stale)} — the filer stopped reporting under every tag tried; "
             "read the newest 10-Q/10-K directly (`hn finance filing`)."] if stale else []
        )
        return Fundamentals(
            symbol=symbol.upper(),
            cik=cik,
            entity_name=resolved_name,
            concepts=series,
            notes=notes_extra + [
                "Reported GAAP/XBRL figures from SEC EDGAR — they LAG real-time (newest = most recent "
                "10-Q/10-K filing). Read-only observation; the assistant surfaces, the user judges.",
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
    if 170 <= span <= 290:
        return "ytd"  # a six- or nine-month year-to-date span
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
    first: dict[tuple[str | None, str], FundamentalFact] = {}
    for f in facts:
        key = (f.end, f.period_type)
        cur = best.get(key)
        if cur is None or (f.filed or "") > (cur.filed or ""):
            best[key] = f
        orig = first.get(key)
        if orig is None or (f.filed or "9") < (orig.filed or "9"):
            first[key] = f
    # The VALUE comes from the latest filing (restatements win) but the fiscal LABEL comes from the
    # period's ORIGINAL filing: a later 10-Q re-reports last year's quarter as a comparative under
    # its own fy/fp context, which printed prior-year figures under the current year's label.
    for key, f in best.items():
        orig = first[key]
        if orig is not f:
            best[key] = f.model_copy(update={"fiscal_year": orig.fiscal_year,
                                             "fiscal_period": orig.fiscal_period})
    ordered = sorted(best.values(), key=lambda f: (f.end or "", f.filed or ""), reverse=True)
    return ordered[:recent]


def _age_days(end: str | None) -> int:
    if not end:
        return 10**6
    try:
        return (date.today() - date.fromisoformat(end)).days
    except ValueError:
        return 10**6


def build_edgar_provider(recent: int = 6) -> EdgarProvider:
    return EdgarProvider(recent=recent)
