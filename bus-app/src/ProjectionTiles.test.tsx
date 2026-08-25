// ProjectionTiles — the Projections tab component:
// rarity-classed tiles, the portal item-tooltip on hover/focus (fixed-position — the
// clipping fix), Held/ilvl anatomy, click→DocPopup provenance. DocPopup's readDoc is
// mocked at the api layer (same pattern as ScorecardBook.test.tsx).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ProjectionTiles, { type Projection, type ProjectionData } from "./ProjectionTiles";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The research artifact\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function proj(over: Partial<Projection>): Projection {
  return {
    // synthetic round numbers — a fixture must never mirror a real params entry
    symbol: "AAA",
    price: 100,
    price_source: "live",
    fwd_eps: 5,
    eps_basis: "non-GAAP annualized",
    fwd_multiple_now: 20.0,
    growth_pct_low: 10,
    growth_pct_high: 20,
    mult_low: 15,
    mult_mid: 20,
    mult_high: 25,
    as_of: "2026-01-01",
    provenance: "finance/research/candidates/AAA/card.md",
    notes: null,
    screen_note: null,
    stale: false,
    held: false,
    item_level: 8,
    rarity: "rare",
    grid: [
      {
        horizon: "12mo",
        years: 1,
        tier: "estimate",
        price: { low: 82.5, mid: 108, high: 150 },
        return_pct: { low: -17.5, mid: 8.0, high: 50.0 },
      },
      {
        horizon: "24mo",
        years: 2,
        tier: "sketch",
        price: { low: 90, mid: 126, high: 180 },
        return_pct: { low: -10.0, mid: 26.0, high: 80.0 },
      },
    ],
    ...over,
  };
}

function data(projections: Projection[]): ProjectionData {
  const by_rarity: Record<string, number> = {};
  for (const p of projections) by_rarity[p.rarity] = (by_rarity[p.rarity] ?? 0) + 1;
  return {
    projections,
    summary: {
      total: projections.length,
      held: projections.filter((p) => p.held).length,
      stale: projections.filter((p) => p.stale).length,
      by_rarity,
    },
    disclaimer: "Scenario grids — never forecasts.",
  };
}

describe("ProjectionTiles", () => {
  it("renders a rarity-classed tile with ilvl and the 12mo band headline", () => {
    render(<ProjectionTiles data={data([proj({})])} />);
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("ilvl 8")).toBeInTheDocument();
    expect(screen.getByText("+8.0%")).toBeInTheDocument(); // 12mo mid (tooltip not mounted yet)
    const tile = screen.getByRole("button", { name: /AAA — item level 8, rare/ });
    expect(tile.className).toContain("rarity-rare");
    // rarity chip row
    expect(screen.getByText("rare")).toBeInTheDocument();
  });

  it("mounts the item tooltip through the portal on hover, with anatomy + sketch tiers", () => {
    render(<ProjectionTiles data={data([proj({})])} />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByRole("button", { name: /AAA/ }));
    const tip = screen.getByRole("tooltip");
    expect(tip.className).toContain("rarity-rare");
    expect(tip.parentElement).toBe(document.body); // the portal — never clipped by the widget
    expect(screen.getByText("Item Level 8")).toBeInTheDocument();
    expect(screen.getByText("Status: Watchlist")).toBeInTheDocument();
    expect(screen.getByText(/\+10–20% Earnings Growth/)).toBeInTheDocument();
    expect(screen.getByText("sketch")).toBeInTheDocument();
    expect(screen.getByText(/"non-GAAP annualized"/)).toBeInTheDocument(); // gold flavor line
    fireEvent.mouseLeave(screen.getByRole("button", { name: /AAA/ }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("held names read Status: Held with basis; stale params warn in the tooltip", () => {
    render(
      <ProjectionTiles
        data={data([
          proj({
            symbol: "HHH",
            held: true,
            basis: 80,
            basis_return_pct: 25.0,
            stale: true,
            rarity: "epic",
            item_level: 21,
          }),
        ])}
      />,
    );
    expect(screen.getByText("HELD")).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByRole("button", { name: /HHH/ }));
    expect(screen.getByText("Status: Held")).toBeInTheDocument();
    expect(screen.getByText(/\(\+25.0%\)/)).toBeInTheDocument();
    expect(screen.getByText(/Params stale — re-anchor/)).toBeInTheDocument();
  });

  it("marks values-screened names with the chip and the tooltip line", () => {
    render(
      <ProjectionTiles
        data={data([proj({ symbol: "SSS", screen_note: "VALUES-SCREENED OUT (test axis)" })])}
      />,
    );
    expect(screen.getByTitle("VALUES-SCREENED OUT (test axis)")).toBeInTheDocument(); // tile chip
    fireEvent.mouseEnter(screen.getByRole("button", { name: /SSS/ }));
    expect(screen.getByText(/⊘ VALUES-SCREENED OUT \(test axis\)/)).toBeInTheDocument();
  });

  it("opens the provenance artifact in DocPopup on click", async () => {
    render(<ProjectionTiles data={data([proj({})])} />);
    fireEvent.click(screen.getByRole("button", { name: /AAA/ }));
    await waitFor(() => expect(screen.getByText("The research artifact")).toBeInTheDocument());
  });

  it("splits in-play targets from screen-gated names into two panes", () => {
    render(
      <ProjectionTiles
        data={data([
          proj({ symbol: "LIVE1" }),
          proj({ symbol: "GATED1", screen_note: "VALUES-SCREENED OUT (test)" }),
        ])}
      />,
    );
    expect(screen.getByText(/^In-play targets/)).toBeInTheDocument();
    expect(screen.getByText(/Screen-gated/)).toBeInTheDocument();
    const panes = document.querySelectorAll(".projections-pane");
    expect(panes).toHaveLength(2);
    expect(panes[0].textContent).toContain("LIVE1");
    expect(panes[0].textContent).not.toContain("GATED1");
    expect(panes[1].textContent).toContain("GATED1");
  });

  it("renders the honest empty state and ships the disclaimer from the data", () => {
    const { rerender } = render(<ProjectionTiles data={data([])} />);
    expect(screen.getByText(/No projection params yet/)).toBeInTheDocument();
    rerender(<ProjectionTiles data={data([proj({})])} />);
    expect(screen.getByText("Scenario grids — never forecasts.")).toBeInTheDocument();
  });

  it("filters both panes by rarity chip; the active chip toggles back to All", () => {
    render(
      <ProjectionTiles
        data={data([
          proj({ symbol: "AAA", rarity: "epic", item_level: 15 }),
          proj({ symbol: "BBB", rarity: "rare", item_level: 6 }),
          proj({ symbol: "CCC", rarity: "epic", item_level: 12, screen_note: "VALUES-SCREENED OUT" }),
        ])}
      />,
    );
    // all three tiles render initially, and the All chip carries the total
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("BBB")).toBeInTheDocument();
    expect(screen.getByText("CCC")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^all 3$/i })).toHaveAttribute("aria-pressed", "true");

    // click the epic chip: rare tile disappears, BOTH panes keep their epic members
    fireEvent.click(screen.getByRole("button", { name: /^epic 2$/i }));
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("CCC")).toBeInTheDocument(); // screen-gated pane still filters correctly
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^epic 2$/i })).toHaveAttribute("aria-pressed", "true");

    // clicking the ACTIVE chip toggles back to All
    fireEvent.click(screen.getByRole("button", { name: /^epic 2$/i }));
    expect(screen.getByText("BBB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^all 3$/i })).toHaveAttribute("aria-pressed", "true");
  });

  it("restores the full set via the All chip after filtering", () => {
    render(
      <ProjectionTiles
        data={data([
          proj({ symbol: "AAA", rarity: "legendary", item_level: 40 }),
          proj({ symbol: "BBB", rarity: "poor", item_level: -2 }),
        ])}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^legendary 1$/i }));
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^all 2$/i }));
    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.getByText("BBB")).toBeInTheDocument();
  });

  it("two-tap on coarse pointers: first tap shows the item card, second opens the doc", async () => {
    // jsdom ships no matchMedia — stub a coarse-pointer environment (a phone/tablet).
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: q === "(pointer: coarse)",
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    try {
      render(<ProjectionTiles data={data([proj({ symbol: "AAA" })])} />);
      const tile = screen.getByRole("button", { name: /AAA — item level/i });
      fireEvent.click(tile); // tap 1: arms + shows the item card, does NOT open the doc
      expect(screen.getByRole("tooltip")).toBeInTheDocument();
      expect(document.querySelector(".doc-popup-backdrop")).toBeNull();
      fireEvent.click(tile); // tap 2 on the armed tile: opens the research doc
      await waitFor(() => expect(document.querySelector(".doc-popup-backdrop")).not.toBeNull());
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("splits into Held / In-play / Screen-gated panes in scan order", () => {
    render(
      <ProjectionTiles
        data={data([
          proj({ symbol: "HHH", rarity: "epic", held: true }),
          proj({ symbol: "III", rarity: "rare", held: false }),
          proj({ symbol: "JJJ", rarity: "rare", held: false, screen_note: "VALUES-SCREENED OUT" }),
        ])}
      />,
    );
    const titles = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(titles[0]).toMatch(/^Held positions/);
    expect(titles[1]).toMatch(/^In-play targets/);
    expect(titles[2]).toMatch(/^Screen-gated/);
    // membership: held name only in pane 1's grid, etc. (verify by pane DOM containment)
    const panes = document.querySelectorAll(".projections-pane");
    expect(panes[0].textContent).toContain("HHH");
    expect(panes[0].textContent).not.toContain("III");
    expect(panes[1].textContent).toContain("III");
    expect(panes[1].textContent).not.toContain("JJJ");
    expect(panes[2].textContent).toContain("JJJ");
  });
});
