// ScorecardBook — the Prints scorecard-book component: grade banners,
// pagination, click→DocPopup. DocPopup's readDoc is mocked at the api layer (the popup itself
// is exercised enough to prove the click wiring; its full behavior has its own coverage).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ScorecardBook, { type Scorecard, type ScorecardData } from "./ScorecardBook";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The full card\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function card(over: Partial<Scorecard>): Scorecard {
  return {
    symbol: "AAA",
    period: "Q1 2026",
    print_date: "2026-04-14",
    grade: "GOOD",
    headline: "beat + raise",
    card_doc: "finance/research/positions/AAA/card.md",
    price_reaction: -4.2,
    held: false,
    key_facts: ["fact one", "fact two"],
    take_doc: null,
    ...over,
  };
}

function data(cards: Scorecard[]): ScorecardData {
  const by_grade: Record<string, number> = {};
  for (const c of cards) by_grade[c.grade] = (by_grade[c.grade] ?? 0) + 1;
  return { scorecards: cards, summary: { total: cards.length, by_grade } };
}

describe("ScorecardBook", () => {
  it("renders grade banners, reaction, facts, and the held chip", () => {
    render(<ScorecardBook data={data([card({ held: true })])} />);
    expect(screen.getAllByText("GOOD").length).toBeGreaterThanOrEqual(2); // chip + banner
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("-4.2%")).toBeInTheDocument();
    expect(screen.getByText("HELD")).toBeInTheDocument();
    expect(screen.getByText("fact one")).toBeInTheDocument();
    const block = screen.getByTitle("Open the full AAA card");
    expect(block.className).toContain("grade-good");
  });

  it("maps every grade to its banner class (the enum contract)", () => {
    const grades = ["GREAT", "GOOD", "OK", "BAD", "DISASTER", "PENDING"];
    render(
      <ScorecardBook
        data={data(grades.map((g, i) => card({ symbol: `S${i}`, grade: g, print_date: `2026-07-${10 + i}` })))}
      />
    );
    for (const g of grades) {
      const block = screen.getByTitle(`Open the full S${grades.indexOf(g)} card`);
      expect(block.className).toContain(`grade-${g.toLowerCase()}`);
    }
  });

  it("paginates 8 per page with page-turn chrome, newest page first", () => {
    const cards = Array.from({ length: 11 }, (_, i) =>
      card({ symbol: `P${String(i).padStart(2, "0")}`, print_date: `2026-07-${28 - i}`, held: false })
    );
    render(<ScorecardBook data={data(cards)} />);
    expect(screen.getByText("Page 1 / 2")).toBeInTheDocument();
    expect(screen.getByText("P00")).toBeInTheDocument();
    expect(screen.queryByText("P08")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Older prints"));
    expect(screen.getByText("Page 2 / 2")).toBeInTheDocument();
    expect(screen.getByText("P08")).toBeInTheDocument();
    expect(screen.queryByText("P00")).not.toBeInTheDocument();
    // the back turn returns; at page 1 the newer-turn disables
    fireEvent.click(screen.getByTitle("Newer prints"));
    expect(screen.getByText("Page 1 / 2")).toBeInTheDocument();
    expect(screen.getByTitle("Newer prints")).toBeDisabled();
  });

  it("opens the DocPopup with the block's card_doc on click", async () => {
    render(<ScorecardBook data={data([card({})])} />);
    fireEvent.click(screen.getByTitle("Open the full AAA card"));
    await waitFor(() => expect(screen.getByText("The full card")).toBeInTheDocument());
    // and Esc closes it (DocPopup's own handler — proves the wiring end-to-end)
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("The full card")).not.toBeInTheDocument());
  });

  it("renders the honest empty state with no cards", () => {
    render(<ScorecardBook data={{ scorecards: [] }} />);
    expect(screen.getByText(/No graded prints yet/)).toBeInTheDocument();
  });
});
