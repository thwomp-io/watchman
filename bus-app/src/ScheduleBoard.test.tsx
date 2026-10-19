// ScheduleBoard — the Visits timeline: time parsing, lane colors, chip filters, the day columns
// (weather chip with the wet badge, availability, markers, items), linked items opening the bank
// popup, unlinked items opening the item card, chips opening the popup, and the empty state.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ScheduleBoard, { chipItems, laneColors, minutes, titleLines } from "./ScheduleBoard";
import type { BankItem, ScheduleDay, VisitSchedule } from "./generated/contracts";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# Visit note"), readImage: vi.fn().mockResolvedValue("data:,") };
});
vi.mock("./nav", () => ({ useNav: () => ({ navigate: vi.fn() }) }));

function bank(over: Partial<BankItem>): BankItem {
  return {
    id: "x", kind: "outing", label: "X", group: "history", group_label: "History", lane: "history",
    area: null, drive_min: null, indoor: null, why: null, note: null, image: null, images: [], gallery: true, gallery_from: null, lat: null, lon: null, place_id: null, phone: null,
    rating: null, reviews: null, yelp_rating: null, price: null, cuisine: null, meals: [], status: null,
    confirm: null, season: null, hours: [], links: {}, extra: {}, ...over,
  };
}

const BANK = [
  bank({ id: "museum", label: "Air museum", drive_min: 40, indoor: true, why: "Planes." }),
  bank({ id: "falls", label: "Waterfall", group: "daytrip", group_label: "Day trips", lane: "daytrip", drive_min: 70, indoor: false }),
  bank({ id: "cafe", kind: "restaurant", label: "Cafe Uno", group: "sweets", group_label: "Sweets", lane: null, drive_min: 10 }),
];

function day(over: Partial<ScheduleDay>): ScheduleDay {
  return { date: "2030-10-12", weekday: "Sat", availability: [], items: [], markers: [], weather: null, ...over };
}

const DATA: VisitSchedule = {
  visit: "v", title: "Family visit", subtitle: "", doc: "travel/visits/v/v.md", start: "2030-10-12", end: "2030-10-13",
  day_start: "08:00", day_end: "22:00", evening_start: "17:30",
  availability_groups: [{ key: "all", label: "everyone", color: "#86efac" }],
  marker_label: "what's on", marker_cats: [{ key: "sports", label: "Sports", color: "#6366f1" }],
  weather_place: "Home", weather_note: "forecast through 2030-10-12", precip_unit: "inch", temp_unit: "°F",
  days: [
    day({
      availability: ["all"],
      weather: { condition: "Rain", high: 56, low: 45, precip_prob: 90, precip_sum: 0.42, precip_hours: 9, wet: true },
      items: [
        { date: "2030-10-12", start: "10:00", end: "15:00", label: "✎ Air museum day", lane: "history", note: "rain day",
          penciled: true, bank_id: "museum", bank_match: "label" },
        { date: "2030-10-12", start: "17:30", end: "20:00", label: "Welcome dinner", lane: "dining", note: "all four",
          penciled: false, bank_id: null, bank_match: null },
      ],
      markers: [{ date: "2030-10-12", time: "1:25p", label: "The game", cat: "sports" }],
    }),
    day({ date: "2030-10-13", weekday: "Sun" }),
  ],
  bank: { visit: "v", title: "Family visit", doc: "travel/visits/v/v.md", start: null, end: null,
          outings_title: "Ideas", restaurants_title: "Food", notes: [],
          groups: [
            { key: "history", label: "History", kind: "outing", count: 1 },
            { key: "daytrip", label: "Day trips", kind: "outing", count: 1 },
            { key: "sweets", label: "Sweets", kind: "restaurant", count: 1 },
          ], items: BANK },
};

function stubPointer(coarse: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((q: string) => ({
    matches: q.includes("coarse") ? coarse : false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
}
beforeEach(() => stubPointer(false));
afterEach(() => vi.unstubAllGlobals());

describe("ScheduleBoard helpers", () => {
  it("parses 24h and am/pm times", () => {
    expect(minutes("08:00")).toBe(480);
    expect(minutes("1:25p")).toBe(13 * 60 + 25);
    expect(minutes("7:00p")).toBe(19 * 60);
    expect(minutes("12:30a")).toBe(30);
    expect(minutes("12:15p")).toBe(12 * 60 + 15);
    expect(minutes("soon")).toBeNull();
    expect(minutes(null)).toBeNull();
  });
  it("colors lanes in bank order, then item-only lanes", () => {
    const c = laneColors(DATA);
    expect(Object.keys(c)).toEqual(["history", "daytrip", "dining"]);
    expect(new Set(Object.values(c)).size).toBe(3);
  });
  it("filters chips by kind, group, indoor and drive band", () => {
    expect(chipItems(BANK, "outing", null, false, "any").map((b) => b.id)).toEqual(["museum", "falls"]);
    expect(chipItems(BANK, "outing", null, true, "any").map((b) => b.id)).toEqual(["museum"]);
    expect(chipItems(BANK, "outing", null, false, "60").map((b) => b.id)).toEqual(["museum"]);
    expect(chipItems(BANK, "outing", "daytrip", false, "any").map((b) => b.id)).toEqual(["falls"]);
    expect(chipItems(BANK, "restaurant", null, false, "15").map((b) => b.id)).toEqual(["cafe"]);
  });
});

describe("ScheduleBoard", () => {
  it("renders a column per day with weather, markers and items", () => {
    const { container } = render(<ScheduleBoard data={DATA} />);
    const grid = container.querySelector(".vsched-grid") as HTMLElement;
    expect(within(grid).getByText("Sat 10/12")).toBeInTheDocument();
    expect(within(grid).getByText("Sun 10/13")).toBeInTheDocument();
    const wet = container.querySelector(".vsched-dayhead.wet .vsched-wx.wet");
    expect(wet?.textContent).toContain("56°/45°");
    expect(wet?.textContent).toContain('0.42"');
    // a timed marker sits in the header with its time and ticks the grid at that time
    expect(within(grid).getByText("The game").closest(".vsched-dayhead")).not.toBeNull();
    expect(grid.querySelectorAll(".vsched-tick")).toHaveLength(1);
    expect(within(grid).getByText("✎ Air museum day").closest(".vsched-item")).toHaveClass("penciled");
    expect(screen.getByText("forecast through 2030-10-12")).toBeInTheDocument();
    // the phone card list carries the same days (CSS shows one or the other)
    expect(within(container.querySelector(".vsched-list") as HTMLElement).getByText("open day")).toBeInTheDocument();
  });

  it("places an item by its time on the day axis", () => {
    const { container } = render(<ScheduleBoard data={DATA} />);
    const block = container.querySelector(".vsched-grid .vsched-item") as HTMLElement;
    // 10:00 is 2h after the 08:00 day start at 30px/h; 10:00–15:00 is 5h tall
    expect(block.style.top).toBe("60px");
    expect(block.style.height).toBe("150px");
  });

  it("a linked item opens its bank popup; an unlinked one opens the item card", () => {
    const { container } = render(<ScheduleBoard data={DATA} />);
    const grid = container.querySelector(".vsched-grid") as HTMLElement;
    fireEvent.click(within(grid).getByText("✎ Air museum day"));
    expect(screen.getByRole("dialog", { name: "Air museum" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(within(grid).getByText("Welcome dinner"));
    const card = screen.getByRole("dialog", { name: "Welcome dinner" });
    expect(within(card).getByText("17:30–20:00")).toBeInTheDocument();
    expect(within(card).getByText("all four")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("hovering a linked item shows the bank quick card", () => {
    const { container } = render(<ScheduleBoard data={DATA} />);
    fireEvent.mouseEnter(within(container.querySelector(".vsched-grid") as HTMLElement).getByText("✎ Air museum day").closest(".vsched-item")!);
    expect(within(screen.getByRole("tooltip")).getByText("Planes.")).toBeInTheDocument();
  });

  it("chips filter, mark planned items, and open the popup", () => {
    render(<ScheduleBoard data={DATA} />);
    const museum = screen.getByRole("button", { name: /Air museum.*planned/ });
    expect(museum).toHaveClass("planned");
    fireEvent.click(screen.getByRole("button", { name: /indoor only/ }));
    expect(screen.queryByRole("button", { name: /Waterfall/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restaurants" }));
    fireEvent.click(screen.getByRole("button", { name: /Cafe Uno/ }));
    expect(screen.getByRole("dialog", { name: "Cafe Uno" })).toBeInTheDocument();
  });

  it("renders an honest empty state", () => {
    render(<ScheduleBoard data={{ ...DATA, days: [], title: "No live visit carries a schedule yet" }} />);
    expect(screen.getByText(/No live visit carries a schedule yet/)).toBeInTheDocument();
  });
});

describe("ScheduleBoard short items", () => {
  it("renders a block too short for two lines in the one-line compact form", () => {
    const data: VisitSchedule = {
      ...DATA,
      days: [day({ items: [
        { date: "2030-10-12", start: "09:15", end: "09:50", label: "Coffee stop", lane: "dining", note: null,
          penciled: false, bank_id: null, bank_match: null },
        { date: "2030-10-12", start: "10:00", end: "14:00", label: "Museum", lane: "history", note: null,
          penciled: false, bank_id: null, bank_match: null },
      ] })],
    };
    const { container } = render(<ScheduleBoard data={data} />);
    const grid = container.querySelector(".vsched-grid") as HTMLElement;
    const short = within(grid).getByText("Coffee stop").closest(".vsched-item") as HTMLElement;
    const long = within(grid).getByText("Museum").closest(".vsched-item") as HTMLElement;
    expect(short).toHaveClass("compact");
    expect(within(short).getByText("09:15–09:50")).toBeInTheDocument();
    expect(long).not.toHaveClass("compact");
    // the 4-hour block (120px) clamps its title to what fits; the compact one carries no clamp
    expect((within(long).getByText("Museum") as HTMLElement).style.webkitLineClamp).toBe(String(titleLines(120)));
    expect((within(short).getByText("Coffee stop") as HTMLElement).style.webkitLineClamp).toBe("");
  });
  it("titleLines: two-line minimum block holds one title line, taller blocks more", () => {
    expect(titleLines(34)).toBe(1);
    expect(titleLines(37.5)).toBe(1);  // a 75-minute block: time + one title line
    expect(titleLines(48)).toBe(2);
  });
});
