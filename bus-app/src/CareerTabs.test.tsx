// Career redesign — the console side: Openings (role items: default filters new + seats you can take,
// fit panes, the item card's modifier lines, the company chip that clears the `company` selection),
// Campaign (in-flight cards with drift + stale, the calendar countdowns), the Pool → Openings chip,
// and the subtab order within a group. Synthetic fixtures only.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CampaignBoard, { countdown } from "./CampaignBoard";
import { orderDashboards } from "./Dash";
import PoolTiles, { type PoolData, type PoolRow } from "./PoolTiles";
import RoleTiles from "./RoleTiles";
import type { CampaignReport, RoleItem, RolesReport } from "./generated/contracts";
import { getSelection, resetSelections, setSelection } from "./selection";
import type { Dashboard } from "./types";

const navigate = vi.fn();
vi.mock("./nav", () => ({ useNav: () => ({ navigate }) }));
vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# note") };
});

beforeEach(() => { navigate.mockClear(); resetSelections(); });

function role(over: Partial<RoleItem>): RoleItem {
  return {
    company: "Quiet Co", slug: "quiet-co", title: "Principal Site Reliability Engineer", url: "https://x/q1",
    location: "Remote, United States", location_class: "remote_us", salary: "", updated: "2026-10-10",
    days_old: 6, first_seen: "2026-10-15", new: true, shape: "SRE / Reliability", lane: "in", level: "in_band",
    fit: "on-profile", company_ilvl: null, company_rarity: null,
    comp: null, base_ilvl: null, base_source: "", company_mods: 0, company_median_tc: null,
    modifiers: [
      { key: "level_in_band", name: "Staff+ seat", value: 8, family: "role_level", how: "machine", source: "title: Principal SRE" },
      { key: "loc_outside_us", name: "Outside the US", value: -15, family: "role_presence", how: "machine", source: "location: London" },
    ],
    modifier_sum: -7, final_ilvl: null, rarity: "unranked", screened_out: false, screen_flag: "none",
    doc: null, profile: "role-hunt/companies/quiet-co.md", ...over,
  };
}

function roles(rows: RoleItem[], company = "auto"): RolesReport {
  return {
    as_of: "2026-10-15", anchor: 300000, bands: { common: -22, rare: 0, epic: 20, legendary: 60 },
    filters: { new: false, location: "all", company },
    counts: { total: rows.length, shown: rows.length, new: rows.filter((r) => r.new).length,
              fit: { "on-profile": 1, adjacent: 0, "off-lane": 1 } },
    rows, screen_gated: [], companies: [], constitution: "a lens, never sole reasoning",
  };
}

const ROWS = [
  role({}),
  role({ url: "https://x/m1", company: "Mega AI", slug: "mega-ai", title: "Critical Facilities Lead",
         shape: "Data center / Facilities", lane: "off", level: "none", fit: "off-lane", final_ilvl: 91,
         rarity: "legendary", company_ilvl: 110, company_rarity: "legendary" }),
  role({ url: "https://x/o1", company: "Old Co", slug: "old-co", title: "Staff SRE (seen before)", new: false }),
  role({ url: "https://x/l1", company: "Far Co", slug: "far-co", title: "Staff SRE, London",
         location: "London, UK", location_class: "outside_us", fit: "off-lane" }),
];

describe("Openings — role items", () => {
  it("defaults to new + seats you can take, grouped by fit", () => {
    render(<RoleTiles lane="career" data={roles(ROWS)} />);
    expect(screen.getByText("Principal Site Reliability Engineer")).toBeInTheDocument();
    expect(screen.getByText("Critical Facilities Lead")).toBeInTheDocument();
    expect(screen.queryByText("Staff SRE (seen before)")).toBeNull(); // not new
    expect(screen.queryByText("Staff SRE, London")).toBeNull(); // outside the home + remote US default
    const panes = document.querySelectorAll(".role-pane");
    expect([...panes].map((p) => p.className)).toEqual([
      expect.stringContaining("fit-on-profile"), expect.stringContaining("fit-off-lane")]);
    fireEvent.click(screen.getByRole("button", { name: /show everything/ }));
    expect(screen.getByText("Staff SRE (seen before)")).toBeInTheDocument();
    expect(screen.getByText("Staff SRE, London")).toBeInTheDocument();
  });

  it("the item card names every modifier with its source, green and red", () => {
    render(<RoleTiles lane="career" data={roles(ROWS)} />);
    fireEvent.mouseEnter(screen.getByText("Principal Site Reliability Engineer").closest(".role-tile")!);
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText(/Unranked/)).toBeInTheDocument();
    const lines = tip.querySelectorAll(".pool-mods li");
    expect(lines[0]).toHaveClass("enchant");
    expect(lines[0].textContent).toContain("title: Principal SRE");
    expect(lines[1]).toHaveClass("debuff");
  });

  it("the item card states the expected comp for the likely level, with its neighbors and basis", () => {
    const priced = role({
      url: "https://x/p1", title: "Principal SRE (priced)", company_ilvl: 50, base_ilvl: 60,
      base_source: "level ladder", company_mods: 2, final_ilvl: 66, rarity: "epic",
      comp: { level: "L7", band: "principal", basis: "mapped", median_tc: 720000, low_level: "L6",
              low_tc: 560000, high_level: "L8", high_tc: 950000, ilvl: 58, note: "",
              location: "united-states", attribution: "Data source: Levels.fyi" },
    });
    render(<RoleTiles lane="career" data={roles([priced])} />);
    const tile = screen.getByText("Principal SRE (priced)").closest(".role-tile")!;
    expect(tile.textContent).toContain("~$720k"); // no posted range → the rung median on the tile
    fireEvent.mouseEnter(tile);
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText(/Expected comp · L7/)).toBeInTheDocument();
    expect(tip.textContent).toContain("$720k");
    expect(tip.textContent).toContain("L6 $560k");
    expect(tip.textContent).toContain("L8 $950k");
    expect(tip.textContent).toContain("basis mapped");
    expect(tip.textContent).toContain("Data source: Levels.fyi");
  });

  it("a company pick shows that company's openings (new or not) and its chip clears the pick", () => {
    setSelection("career", "company", "old-co");
    render(<RoleTiles lane="career" data={roles([ROWS[2]], "old-co")} />);
    expect(screen.getByText("Staff SRE (seen before)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /company: old-co/ }));
    expect(getSelection("career", "company")).toBe("auto");
  });
});

const CAMPAIGN: CampaignReport = {
  as_of: "2030-03-01", stale_days: 21,
  in_flight: [
    { company: "Northwind Labs", role: "Senior Data Engineer", stage: "screening", next_step: "Intro call Mon 3/4",
      next_date: "2030-03-04", next_time: "14:00", last_touch: "2030-02-21", days_since: 8, channel: "gmail",
      screen_line: "clear", ref: "role-hunt/inbound/northwind.md", url: "", location: "", comp_signal: "",
      drift: false, stale: false },
    { company: "Drifter Systems", role: "Staff SRE", stage: "inbound", next_step: "", next_date: null,
      next_time: "", last_touch: "2030-02-25", days_since: 4, channel: "linkedin", screen_line: "gray: labor",
      ref: "role-hunt/inbound/drifter.md", url: "", location: "", comp_signal: "", drift: true, stale: false },
    { company: "Old Lead Inc", role: "Principal SRE", stage: "inbound", next_step: "decide", next_date: null,
      next_time: "", last_touch: "2029-11-19", days_since: 102, channel: "linkedin", screen_line: "clear",
      ref: "", url: "", location: "", comp_signal: "", drift: false, stale: true },
  ],
  calendar: [
    { date: "2030-03-04", label: "Northwind — recruiter screen", kind: "screen", time: "14:00", ref: "", approx: false, days: 3 },
    { date: "2030-11-26", label: "Deadline", kind: "deadline", time: "", ref: "", approx: false, days: 270 },
  ],
  calendar_notes: [], mail: [], mail_note: "",
};

describe("Campaign", () => {
  it("cards carry the next step, drift and stale; the calendar counts down", () => {
    render(<CampaignBoard data={CAMPAIGN} />);
    const cards = document.querySelectorAll(".camp-card");
    expect(cards[0].textContent).toContain("Northwind Labs");
    expect(cards[0].querySelector(".camp-next")?.textContent).toContain("in 3d");
    expect(cards[1]).toHaveClass("drift");
    expect(within(cards[1] as HTMLElement).getByText("not in the pipeline")).toBeInTheDocument();
    expect(cards[2]).toHaveClass("stale");
    expect(within(cards[2] as HTMLElement).getByText("decide or archive")).toBeInTheDocument();
    expect(document.querySelectorAll(".camp-milestone")).toHaveLength(2);
    expect(countdown(3)).toEqual({ big: "3", unit: "days" });
    expect(countdown(270)).toEqual({ big: "9", unit: "months" });
  });
});

describe("Pool → Openings", () => {
  it("the openings chip lands on the subtab that follows `company`, with the company picked", () => {
    const r = { rank: 1, company: "Quiet Co", slug: "quiet-co", label: "", median_tc: 500000, on_watchlist: true,
      bench: false, screen_flag: "none", screened_out: false, base_ilvl: 10, base_rarity: "rare", modifiers: [],
      set_bonuses: [], modifier_sum: 0, final_ilvl: 10, rarity: "rare", capped: false, agency: "",
      openings: { openings: 5, on_profile: 2, new: 1 } } as PoolRow;
    const data = { cut: { family: "f", level: "l", kind: "k", geo: "g", url: "u", key: "k" }, as_of: "x",
      anchor: 300000, rows: [r], screen_gated: [], attribution: "", constitution: "" } as PoolData;
    render(<PoolTiles data={data} />);
    fireEvent.click(screen.getByText("2 on-profile openings"));
    expect(navigate).toHaveBeenCalledWith({ zone: "dash", follows: "company", pick: "quiet-co" });
  });
});

describe("subtab order", () => {
  it("groups keep their place; within a group `order` wins, unordered tabs follow", () => {
    const d = (lane: string, group: string, order?: number) => ({ lane, title: lane, group, order, widgets: [] }) as Dashboard;
    const out = orderDashboards([
      d("career", "Career", 1), d("career-ladders", "Career", 3), d("career-pool", "Career", 2),
      d("finance", "Finance"), d("career-campaign", "Career", 0), d("career-extra", "Career"),
    ]);
    expect(out.map((x) => x.lane)).toEqual([
      "career-campaign", "career", "career-pool", "career-ladders", "career-extra", "finance"]);
  });
});
