// TripPicker — tiles live-then-past, the default highlighted until a pick, a click sets the tab's
// shared selection (what the schedule/bank widgets follow), and empty entries read honestly.

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPicker, { dates, summary } from "./TripPicker";
import type { TripBanks, TripEntry } from "./generated/contracts";
import { getSelection, resetSelections } from "./selection";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readImage: vi.fn().mockResolvedValue("data:,"), readDoc: vi.fn().mockResolvedValue("# note") };
});
vi.mock("./nav", () => ({ useNav: () => ({ navigate: vi.fn() }) }));

function entry(over: Partial<TripEntry>): TripEntry {
  return {
    slug: "x", kind: "visit", title: "X", status: "planning", when: "in 7d", date: "2030-10-10",
    start: "2030-10-10", end: "2030-10-17", destination: "Home (hosting)", doc: "travel/visits/x/x.md",
    cover: null, outings: 0, restaurants: 0, photos: 0, scheduled: 0, live: true, phase: "live", ...over,
  };
}

const DATA: TripBanks = {
  scope: "visit", default: "oct",
  entries: [
    entry({ slug: "oct", title: "Sample visit", outings: 32, restaurants: 40, scheduled: 6, cover: "a.jpg" }),
    entry({ slug: "dec", title: "Game day", when: "in 9w", start: "2030-12-07", end: "2030-12-07" }),
    entry({ slug: "may", title: "Spring visit", when: "completed", status: "completed", live: false, phase: "past", outings: 22, restaurants: 46 }),
  ],
};

beforeEach(() => {
  resetSelections();
  try { window.localStorage.clear(); } catch { /* jsdom */ }
});

describe("TripPicker helpers", () => {
  it("summarizes what an entry carries", () => {
    expect(summary(DATA.entries[0])).toBe("32 ideas · 40 restaurants · 6 planned");
    expect(summary(DATA.entries[1])).toBe("nothing planned yet");
  });
  it("formats a date range and a single day", () => {
    expect(dates(DATA.entries[0])).toMatch(/Oct 10 – Oct 17/);
    expect(dates(DATA.entries[1])).toMatch(/^Dec 7$/);
    expect(dates(entry({ start: null, date: "fall/winter" }))).toBe("fall/winter");
  });
});

describe("TripPicker", () => {
  it("lists upcoming then past, and highlights the default", () => {
    render(<TripPicker lane="travel-visits" data={DATA} />);
    const tiles = screen.getAllByRole("button", { pressed: undefined }).filter((b) => b.classList.contains("picker-tile"));
    expect(tiles.map((t) => t.getAttribute("title"))).toEqual(["Sample visit", "Game day", "Spring visit"]);
    expect(screen.getByText("past")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sample visit/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Game day/ })).toHaveClass("empty");
    expect(screen.getByRole("button", { name: /Spring visit/ })).toHaveClass("past");
  });

  it("a click sets the tab's shared selection", () => {
    render(<TripPicker lane="travel-visits" data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Spring visit/ }));
    expect(getSelection("travel-visits", "visit")).toBe("may");
    expect(screen.getByRole("button", { name: /Spring visit/ })).toHaveAttribute("aria-pressed", "true");
    expect(getSelection("travel-trips", "visit")).toBeNull(); // per lane
  });

  it("falls back to the default when the remembered pick no longer exists", () => {
    render(<TripPicker lane="travel-visits" data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Spring visit/ }));
    const { container } = render(<TripPicker lane="travel-visits" data={{ ...DATA, entries: DATA.entries.slice(0, 2) }} />);
    expect(container.querySelector(".picker-tile.active")?.getAttribute("title")).toBe("Sample visit");
  });

  it("opens the selected entry's note", () => {
    render(<TripPicker lane="travel-visits" data={DATA} />);
    fireEvent.click(screen.getByText(/open the visit note/));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
