// HoldingsGrid — the Holdings tab component: position-size-ordered holding cards with the
// two-level appraisal read (ilvl now · entry · edge), honest unappraised rows, the shared portal
// item-tooltip on hover/focus, click→DocPopup provenance. Mirrors ProjectionTiles.test.tsx
// (one interaction pattern, one test shape).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import HoldingsGrid, { type Holding, type HoldingsData } from "./HoldingsGrid";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The research artifact\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function holding(over: Partial<Holding>): Holding {
  return {
    symbol: "AAA",
    name: "AAA Corp",
    account: "brokerage",
    valuation: "live",
    shares: 10,
    avg_cost: 120,
    cost_basis: 1200,
    price: 160,
    value: 1600,
    weight_pct: 12.5,
    unrealized_gl: 400,
    unrealized_gl_pct: 33.3,
    appraised: true,
    item_level: 20,
    rarity: "epic",
    entry_item_level: 60,
    entry_rarity: "legendary",
    entry_edge: 40,
    paid_multiple: 30,
    fwd_multiple_now: 40,
    stale: false,
    screen_note: null,
    eps_basis: "non-GAAP annualized (fixture)",
    as_of: "2026-01-01",
    provenance: "finance/research/candidates/AAA/card.md",
    grid: [
      {
        horizon: "12mo",
        years: 1,
        tier: "estimate",
        price: { low: 150, mid: 192, high: 230 },
        return_pct: { low: -6.3, mid: 20.0, high: 43.8 },
      },
    ],
    ...over,
  };
}

function data(holdings: Holding[]): HoldingsData {
  return {
    holdings,
    summary: {
      invested_total: holdings.reduce((a, h) => a + h.value, 0),
      count: holdings.length,
      appraised: holdings.filter((h) => h.appraised).length,
      unrealized_gl_total: 400,
      best_entry: { symbol: "AAA", entry_item_level: 60 },
      weakest_entry: { symbol: "AAA", entry_item_level: 60 },
    },
    disclaimer: "Present-knowledge lens — never a verdict.",
  };
}

describe("HoldingsGrid", () => {
  it("renders position-size-ordered cards with the two-level appraisal read", () => {
    render(<HoldingsGrid data={data([holding({})])} />);
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    // The appraisal line: ilvl now + entry + edge
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("(+40)")).toBeInTheDocument();
  });

  it("renders unappraised rows honest with the reason named", () => {
    render(
      <HoldingsGrid
        data={data([
          holding({}),
          holding({
            symbol: "FUNDX",
            appraised: false,
            unappraised_reason: "no params-registry entry",
            item_level: undefined,
            rarity: undefined,
            entry_item_level: undefined,
            provenance: undefined,
          }),
        ])}
      />,
    );
    expect(screen.getByText("no params-registry entry")).toBeInTheDocument();
    const card = screen.getByRole("button", { name: /FUNDX.*unappraised/ });
    expect(card.className).toContain("unappraised");
  });

  it("shows the item tooltip on hover with entry appraisal + grid", async () => {
    render(<HoldingsGrid data={data([holding({})])} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /AAA/ }));
    await waitFor(() => {
      expect(screen.getByRole("tooltip")).toBeInTheDocument();
    });
    expect(screen.getByText("Item Level 20")).toBeInTheDocument();
    expect(screen.getByText(/Entry appraisal \+60/)).toBeInTheDocument();
    expect(screen.getByText(/Paid 30x forward/)).toBeInTheDocument();
    expect(screen.getByText("12mo")).toBeInTheDocument();
  });

  it("opens the research artifact via DocPopup on click", async () => {
    render(<HoldingsGrid data={data([holding({})])} />);
    fireEvent.click(screen.getByRole("button", { name: /AAA/ }));
    await waitFor(() => {
      expect(screen.getByText("The research artifact")).toBeInTheDocument();
    });
  });

  it("two-taps on coarse pointers: first tap arms the card, second opens the doc", async () => {
    // jsdom ships no matchMedia — stub a coarse-pointer environment (the ProjectionTiles pattern).
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: q === "(pointer: coarse)",
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    try {
      render(<HoldingsGrid data={data([holding({})])} />);
      const card = screen.getByRole("button", { name: /AAA/ });
      fireEvent.click(card); // tap 1: arms + shows the item card
      await waitFor(() => expect(screen.getByRole("tooltip")).toBeInTheDocument());
      expect(screen.queryByText("The research artifact")).not.toBeInTheDocument();
      fireEvent.click(card); // tap 2: opens the doc
      await waitFor(() => expect(screen.getByText("The research artifact")).toBeInTheDocument());
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("renders the summary strip + disclaimer", () => {
    render(<HoldingsGrid data={data([holding({})])} />);
    expect(screen.getByText(/invested/)).toBeInTheDocument();
    expect(screen.getByText(/best entry/)).toBeInTheDocument();
    expect(screen.getByText("Present-knowledge lens — never a verdict.")).toBeInTheDocument();
  });

  it("renders the empty state when no holdings", () => {
    render(<HoldingsGrid data={{ holdings: [] }} />);
    expect(screen.getByText(/No holdings on record/)).toBeInTheDocument();
  });
});
