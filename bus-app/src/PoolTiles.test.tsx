// PoolTiles — the Career ▸ Pool tab: rarity-classed tiles from the persisted survey,
// the In-play / Screen-gated panes, the portal item card with ENCHANTMENTS GREEN / DEBUFFS RED
// lines + the set-bonus + capped lines + the constitutional footer, click → DocPopup on the research
// entry (or the levels page when none). Mirrors ProjectionTiles.test.tsx.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import PoolTiles, { type PoolData, type PoolRow } from "./PoolTiles";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The research entry\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function row(over: Partial<PoolRow>): PoolRow {
  return {
    // synthetic — never a real company's ladder
    rank: 1,
    company: "Acme Cloud",
    slug: "acme-cloud",
    label: "E6",
    median_tc: 600000,
    base: 300000,
    stock: 280000,
    bonus: 20000,
    on_watchlist: true,
    ticker: "ACME",
    bench: true,
    screen_flag: "none",
    screened_out: false,
    base_ilvl: 20,
    base_rarity: "epic",
    modifiers: [
      { key: "remote_first", name: "Remote-first", value: 3, family: "presence", how: "machine" },
      { key: "onsite", name: "Five days on-site", value: -3, family: "presence", how: "agent" },
    ],
    set_bonuses: [{ family: "fit", name: "Strong-fit set", count: 3, have: 3, bonus: 2 }],
    modifier_sum: 2,
    final_ilvl: 22,
    rarity: "epic",
    capped: false,
    capped_at: null,
    agency: "unknown until the loop",
    doc: "role-hunt/companies/acme-cloud.md",
    ...over,
  };
}

function data(rows: PoolRow[], gated: PoolRow[] = []): PoolData {
  return {
    cut: { family: "Software-Engineer", level: "Staff-Engineer", kind: "country", geo: "United-States",
           url: "https://example.com/lb", key: "k" },
    as_of: "2026-09-09",
    anchor: 500000,
    rows,
    screen_gated: gated,
    attribution: "Data source: Levels.fyi (https://www.levels.fyi)",
    constitution: "a lens, never sole reasoning — comp base + named modifiers; fit is interview-evaluated.",
    diff: null,
  };
}

describe("PoolTiles", () => {
  it("renders a rarity-classed tile with ilvl, chips and the base/mods line", () => {
    render(<PoolTiles data={data([row({})])} />);
    expect(screen.getByText("Acme Cloud")).toBeInTheDocument();
    expect(screen.getByText("ilvl 22")).toBeInTheDocument();
    expect(screen.getByText("WATCHLIST")).toBeInTheDocument();
    expect(screen.getByText("BENCH ACME")).toBeInTheDocument();
    const tile = screen.getByRole("button", { name: /Acme Cloud — item level 22, epic/ });
    expect(tile.className).toContain("rarity-epic");
    expect(screen.getByText("anchor $500,000")).toBeInTheDocument();
  });

  it("mounts the item card through the portal with green enchantments, red debuffs, the set line", () => {
    render(<PoolTiles data={data([row({})])} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Acme Cloud/ }));
    const tip = screen.getByRole("tooltip");
    expect(tip.parentElement).toBe(document.body);
    expect(screen.getByText("Item Level 22")).toBeInTheDocument();
    const ench = screen.getByText("Remote-first").closest("li");
    const deb = screen.getByText("Five days on-site").closest("li");
    expect(ench?.className).toBe("enchant");
    expect(deb?.className).toBe("debuff");
    expect(screen.getByText(/Strong-fit set \(3\/3\)/)).toBeInTheDocument();
    expect(screen.getByText(/base ilvl \+20 vs the anchor/)).toBeInTheDocument();
    expect(screen.getByText(/Agency: unknown until the loop/)).toBeInTheDocument();
    expect(screen.getAllByText(/a lens, never sole reasoning/).length).toBe(2); // the card footer + the tab footer
  });

  it("shows the capped line when the clamp fired and gates screened-out rows in their own pane", () => {
    const gated = row({ company: "Gamma Defense", slug: "gamma-defense", screen_flag: "defense",
                        screened_out: true, base_ilvl: null, base_rarity: null, rarity: null, final_ilvl: null,
                        modifiers: [], set_bonuses: [], modifier_sum: 0, doc: null, on_watchlist: false, bench: false });
    render(<PoolTiles data={data([row({ capped: true, capped_at: "legendary", final_ilvl: 59 })], [gated])} />);
    expect(screen.getByText("ilvl 59 ⌈")).toBeInTheDocument();
    expect(screen.getByText(/Screen-gated — visible, marked, no arithmetic/)).toBeInTheDocument();
    const g = screen.getByRole("button", { name: /Gamma Defense — screen-gated \(defense\)/ });
    expect(g.className).toContain("gated");
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Acme Cloud/ }));
    expect(screen.getByText(/Capped at legendary/)).toBeInTheDocument();
  });

  it("click opens the research entry in a DocPopup when the engine found one", async () => {
    render(<PoolTiles data={data([row({})])} />);
    fireEvent.click(screen.getByRole("button", { name: /Acme Cloud/ }));
    expect(await screen.findByText("The research entry")).toBeInTheDocument();
  });

  it("a merged pool shows the SOURCE chip, the source line, and filters over every appearance", () => {
    const lb = row({ source: "leaderboard", cut: "staff/us", basis: "leaderboard staff", appearances: [
      { source: "leaderboard", cut: "us", level: "staff", label: "E6", median_tc: 600000, rank: 1 },
      { source: "ladder", cut: "us", level: "staff", label: "E6", median_tc: 590000, basis: "mapped" },
    ] });
    const lad = row({ company: "Thin Co", slug: "thin-co", source: "ladder", cut: "staff/us", basis: "ladder staff (yoe)",
      appearances: [{ source: "ladder", cut: "us", level: "staff", label: "L7", median_tc: 500000, basis: "yoe" }] });
    const d = { ...data([lb, lad]), merged: true, sources: { surveys: ["a.json"], ladders: 2, band: "staff" } };
    render(<PoolTiles data={d} />);
    expect(screen.getByText(/the whole pool · 1 cuts \+ 2 ladders/)).toBeInTheDocument();
    expect(screen.getByText("LB · staff × us")).toBeInTheDocument();
    expect(screen.getByText("LADDER · staff × us")).toBeInTheDocument();
    // the source filter: "ladder" keeps Acme too — it has a ladder appearance
    fireEvent.click(screen.getByRole("button", { name: "ladder" }));
    expect(screen.getByText("Acme Cloud")).toBeInTheDocument();
    expect(screen.getByText("Thin Co")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "leaderboard" }));
    expect(screen.queryByText("Thin Co")).not.toBeInTheDocument();
    // the card names the source and the other appearance
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Acme Cloud/ }));
    expect(screen.getByText(/Source: leaderboard staff · E6/)).toBeInTheDocument();
    expect(screen.getByText(/also: ladder staff × us E6 \$590,000/)).toBeInTheDocument();
  });

  it("an unranked item (no band row) gets its own chip, no ilvl, a neutral tile, and is not poor", () => {
    const unranked = row({
      company: "Zeta Cloud", slug: "zeta-cloud", label: "", median_tc: 277000, on_watchlist: false,
      base_ilvl: null, base_rarity: null, modifiers: [], set_bonuses: [], modifier_sum: 0,
      final_ilvl: null, rarity: "unranked", doc: null, source: "ladder", cut: "all/us",
      basis: "ladder all (ladder-median) — not the staff band",
    });
    const poor = row({ company: "Omega Corp", slug: "omega-corp", median_tc: 320000, base_ilvl: -36,
      base_rarity: "poor", modifiers: [], set_bonuses: [], modifier_sum: 0, final_ilvl: -36, rarity: "poor" });
    render(<PoolTiles data={data([row({}), poor, unranked])} />);
    expect(screen.getByRole("button", { name: /^unranked/ })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /^poor/ })).toHaveTextContent("1");
    const tile = screen.getByRole("button", { name: /Zeta Cloud — unranked \(no band row\)/ });
    expect(tile.className).toContain("rarity-unranked");
    expect(tile).not.toHaveTextContent("ilvl");
    expect(tile).toHaveTextContent("no band row");
    expect(tile).toHaveTextContent("$277,000");
    fireEvent.click(screen.getByRole("button", { name: /^poor/ }));
    expect(screen.queryByRole("button", { name: /Zeta Cloud/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Omega Corp/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^poor/ }));
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Zeta Cloud/ }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Unranked — no band row, no item level");
  });

  it("renders the empty state when no survey is persisted", () => {
    render(<PoolTiles data={data([])} />);
    expect(screen.getByText(/No pool survey yet/)).toBeInTheDocument();
  });
});
