// LaddersGrid — the Career ▸ Ladders tab, redesigned through the Pool's rarity primitive:
// rung bars in engine rarity colors with the anchor line, band-row outlines, the
// card tint from the band row, the percentile strip, the rung card on hover, the rarity sort
// toggle, attribution per card, click → DocPopup.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LaddersGrid, { type Ladder, type LaddersData } from "./LaddersGrid";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The research entry\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function ladder(over: Partial<Ladder>): Ladder {
  return {
    company: "Acme Cloud",
    slug: "acme-cloud",
    family: "software-engineer",
    location: "united-states",
    currency: "USD",
    url: "https://www.levels.fyi/companies/acme-cloud/salaries/software-engineer/locations/united-states.md",
    last_updated: "September 9, 2026",
    median_tc: 240000,
    high_tc: 700000,
    levels: [
      { level: "L4", median_tc: 160000, yoe: "4–5", band: false, vs_anchor_pct: -47, ilvl: -47, rarity: "poor" },
      { level: "L5", median_tc: 240000, yoe: "8–9", title: "Senior Software Engineer", band: false, vs_anchor_pct: -20, ilvl: -20, rarity: "common" },
      { level: "L6", median_tc: 320000, yoe: null, band: true, band_name: "staff", band_basis: "mapped", vs_anchor_pct: 7, ilvl: 7, rarity: "rare" },
      { level: "L7", median_tc: 460000, yoe: "14–15", band: true, band_name: "principal", band_basis: "mapped", vs_anchor_pct: 53, ilvl: 53, rarity: "epic" },
    ],
    mix: { base: 130000, stock: 95000, bonus: 15000 },
    note: null,
    fetched_at: "2026-09-09T05:00:00+00:00",
    attribution: "Data source: Levels.fyi (https://www.levels.fyi)",
    stale: false,
    on_watchlist: true,
    tier: "core",
    ticker: "ACME",
    bench: true,
    doc: "role-hunt/companies/acme-cloud.md",
    card_rarity: "epic",
    card_ilvl: 53,
    card_level: "L7",
    band_basis: "mapped",
    bands: { staff: { level: "L6", basis: "mapped" }, principal: { level: "L7", basis: "mapped" } },
    ...over,
  };
}

function data(ladders: Ladder[], report: LaddersData["report"] = null): LaddersData {
  return {
    ladders, report, anchor: 300000, band_words: ["staff", "principal"], count: ladders.length,
    bands: { common: -22, rare: 0, epic: 20, legendary: 60 },
    stale: ladders.filter((l) => l.stale).length, attribution: "Data source: Levels.fyi (https://www.levels.fyi)",
  };
}

const REPORT: LaddersData["report"] = {
  year: 2025,
  anchor_read: ["Anchor $300,000 vs Lakeport Metro Area, US: above the p90 ($260,000) — past every listed percentile"],
  metro: { location: "Lakeport Metro Area, US", median: 150000, p25: 110000, p75: 200000, p90: 260000 },
  us: { median: 195000, p25: 137500, p75: 280000, p90: 387000 },
  levels: { "Staff Engineer": { current: 290000 }, "Principal Engineer": { current: 380000 } },
};

describe("LaddersGrid", () => {
  it("draws every rung as a bar in its engine rarity, outlines the band rows, tints the card", () => {
    render(<LaddersGrid data={data([ladder({})])} />);
    const card = screen.getByRole("button", { name: /Acme Cloud — epic, band item level 53/ });
    expect(card.className).toContain("rarity-epic");
    const l7 = screen.getByRole("button", { name: /L7 — median \$460,000, item level 53, epic, your band/ });
    expect(l7.className).toContain("rarity-epic");
    expect(l7.className).toContain("band");
    const l4 = screen.getByRole("button", { name: /L4 — median \$160,000, item level -47, poor/ });
    expect(l4.className).toContain("rarity-poor");
    expect(l4.className).not.toContain("band");
    // the bars are sqrt-scaled within the card: the widest rung is the top one
    // the bar width is `calc(<pct>% * var(--bar-share))` — read the percentage inside the calc
    const w = (el: HTMLElement) => parseFloat((el.querySelector(".ladder-rung-bar") as HTMLElement).style.width);
    expect(w(l7)).toBeGreaterThan(w(l4));
    expect(screen.getAllByText("anchor").length).toBeGreaterThanOrEqual(1); // the card's anchor line label
    expect(screen.getByText("CORE")).toBeInTheDocument();
    expect(screen.getByText("BENCH ACME")).toBeInTheDocument();
    expect(screen.getAllByText(/Data source: Levels.fyi/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/band L7: mapped · principal L7: mapped/)).toBeInTheDocument();
  });

  it("hover a rung mounts the rung card through the portal with the dollar gap to the anchor", () => {
    render(<LaddersGrid data={data([ladder({})])} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /L6 — median/ }));
    const tip = screen.getByRole("tooltip");
    expect(tip.parentElement).toBe(document.body);
    expect(screen.getByText("Acme Cloud · L6")).toBeInTheDocument();
    expect(screen.getByText("Item Level 7 · rare")).toBeInTheDocument();
    expect(screen.getByText("Your staff band · mapped")).toBeInTheDocument();
    expect(screen.getByText(/\+\$20,000/)).toBeInTheDocument(); // 320,000 − 300,000
    fireEvent.mouseLeave(screen.getByRole("button", { name: /L6 — median/ }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("renders the percentile strip with the anchor marker and the level ticks; bullets sit in the expander", () => {
    render(<LaddersGrid data={data([ladder({})], REPORT)} />);
    expect(screen.getByText(/Pay report 2025/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Lakeport Metro Area: p25 \$110k · median \$150k · p75 \$200k · p90 \$260k/)).toBeInTheDocument();
    expect(screen.getByLabelText(/United States: p25 \$138k/)).toBeInTheDocument();
    expect(screen.getAllByText("Staff").length).toBe(2); // one tick per row
    expect(screen.getAllByText("Principal").length).toBe(2);
    expect(screen.getAllByText("anchor").length).toBeGreaterThanOrEqual(3); // two strip markers + the card line
    expect(screen.getByText("the anchor read, in words")).toBeInTheDocument();
    expect(screen.getByText(/past every listed percentile/)).toBeInTheDocument();
  });

  it("the sort toggle reorders by card rarity; watchlist-first is the default", () => {
    const low = ladder({ company: "Thin Co", slug: "thin-co", card_rarity: "common", card_ilvl: -10, on_watchlist: false, tier: "" });
    const high = ladder({ company: "Beta Systems", slug: "beta-systems", card_rarity: "legendary", card_ilvl: 80, on_watchlist: false, tier: "" });
    render(<LaddersGrid data={data([low, high])} />);
    const names = () => screen.getAllByRole("button", { name: /— (common|legendary)/ }).map((b) => b.textContent?.slice(0, 6));
    expect(names()[0]).toContain("Thin");
    fireEvent.click(screen.getByRole("button", { name: /watchlist first/ }));
    expect(names()[0]).toContain("Beta");
    expect(screen.getByText("sorted by rarity")).toBeInTheDocument();
  });

  it("click opens the research entry; a thin ladder shows its note; the empty state names the verb", async () => {
    render(<LaddersGrid data={data([ladder({})])} />);
    fireEvent.click(screen.getByRole("button", { name: /Acme Cloud — epic/ }));
    expect(await screen.findByText("The research entry")).toBeInTheDocument();
    render(<LaddersGrid data={data([ladder({ company: "Thin Co", slug: "thin-co", levels: [], note: "no per-level data — a thin page", mix: null, card_rarity: null, card_ilvl: null })])} />);
    expect(screen.getByText(/no per-level data/)).toBeInTheDocument();
    render(<LaddersGrid data={data([])} />);
    expect(screen.getByText(/No cached ladders/)).toBeInTheDocument();
  });
});
