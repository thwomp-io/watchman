// ScorecardBook — the Prints scorecard book: every graded print as a grade-bannered
// block, paginated newest-first with page-turn chrome in the footer. Click a block
// → DocPopup renders the full card markdown (the print-prep/grade doc) without leaving the tab —
// the Ops-tab quick-look pattern applied to the print history.
//
// Data contract = `hn finance scorecards --json` (deterministic registry read; the agent appends
// entries at grade time — no model anywhere in this render path). Grade enum is a two-surface
// contract with harness.finance.scorecards.GRADES — the python test pins it; GRADE_CLASS here
// must cover exactly that set.

import { useState } from "react";
import DocPopup from "./DocPopup";

export interface Scorecard {
  symbol: string;
  period: string;
  print_date: string;
  grade: string;
  headline: string;
  card_doc: string;
  price_reaction: number | null;
  held: boolean;
  key_facts: string[];
  take_doc: string | null;
}

export interface ScorecardData {
  scorecards: Scorecard[];
  summary?: { total: number; by_grade: Record<string, number> };
}

const PAGE_SIZE = 8;

const GRADE_CLASS: Record<string, string> = {
  GREAT: "grade-great",
  GOOD: "grade-good",
  OK: "grade-ok",
  BAD: "grade-bad",
  DISASTER: "grade-disaster",
  PENDING: "grade-pending",
};

const GRADE_ORDER = ["GREAT", "GOOD", "OK", "BAD", "DISASTER", "PENDING"];

export default function ScorecardBook({ data }: { data: ScorecardData }) {
  const [page, setPage] = useState(0);
  const [openCard, setOpenCard] = useState<string | null>(null);
  const cards = data?.scorecards ?? [];
  const pages = Math.max(1, Math.ceil(cards.length / PAGE_SIZE));
  const clamped = Math.min(page, pages - 1);
  const slice = cards.slice(clamped * PAGE_SIZE, clamped * PAGE_SIZE + PAGE_SIZE);
  const byGrade = data?.summary?.by_grade ?? {};

  if (cards.length === 0) {
    return (
      <p className="scorebook-empty">
        No graded prints yet — entries accrete in{" "}
        <code>finance/reference/print-scorecards.yaml</code> as prints are graded.
      </p>
    );
  }

  return (
    <div className="scorebook">
      <div className="scorebook-chips">
        {GRADE_ORDER.filter((g) => byGrade[g]).map((g) => (
          <span key={g} className={`scorebook-chip ${GRADE_CLASS[g]}`}>
            {g} <b>{byGrade[g]}</b>
          </span>
        ))}
      </div>
      <div className="scorebook-grid">
        {slice.map((c) => (
          <button
            key={`${c.symbol}-${c.print_date}`}
            className={`scorecard ${GRADE_CLASS[c.grade] ?? "grade-pending"}`}
            onClick={() => setOpenCard(c.card_doc)}
            title={`Open the full ${c.symbol} card`}
          >
            <div className="scorecard-banner">{c.grade}</div>
            <div className="scorecard-head">
              <strong className="scorecard-sym">{c.symbol}</strong>
              <span className="scorecard-period">{c.period}</span>
              {c.held && <span className="scorecard-held">HELD</span>}
              <span className="scorecard-date">{c.print_date}</span>
              {c.price_reaction != null && (
                <span
                  className={`scorecard-reaction ${c.price_reaction >= 0 ? "pos" : "neg"}`}
                >
                  {c.price_reaction >= 0 ? "+" : ""}
                  {c.price_reaction.toFixed(1)}%
                </span>
              )}
            </div>
            <p className="scorecard-headline">{c.headline}</p>
            {c.key_facts.length > 0 && (
              <ul className="scorecard-facts">
                {c.key_facts.slice(0, 3).map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            )}
          </button>
        ))}
      </div>
      {pages > 1 && (
        <footer className="scorebook-pager">
          <button
            className="scorebook-turn"
            disabled={clamped === 0}
            onClick={() => setPage(clamped - 1)}
            title="Newer prints"
          >
            ◀
          </button>
          <span className="scorebook-pageno">
            Page {clamped + 1} / {pages}
          </span>
          <button
            className="scorebook-turn"
            disabled={clamped >= pages - 1}
            onClick={() => setPage(clamped + 1)}
            title="Older prints"
          >
            ▶
          </button>
        </footer>
      )}
      {openCard && <DocPopup doc={openCard} onClose={() => setOpenCard(null)} />}
    </div>
  );
}
