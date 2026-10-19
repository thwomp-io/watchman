// BankTiles — the Visits tab's idea-bank tiles: the outings/restaurants switch, group chips, the
// rain-safe filter, the hover quick card, click → the detail popup with link buttons, tap-opens on
// touch, lazy images, and the empty state. readImage/readDoc are mocked at the api layer.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BankTiles, { driveText, Gallery, hoursOn, orderedLinks, step, visibleItems } from "./BankTiles";
import { readImage } from "./api";
import type { BankItem, VisitBank } from "./generated/contracts";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return {
    ...mod,
    readDoc: vi.fn().mockResolvedValue("# The visit note\n\nbody"),
    readImage: vi.fn().mockResolvedValue("data:image/jpeg;base64,AAAA"),
  };
});
vi.mock("./nav", () => ({ useNav: () => ({ navigate: vi.fn() }) }));

function item(over: Partial<BankItem>): BankItem {
  return {
    id: "x", kind: "outing", label: "X", group: "history", group_label: "History",
    lane: "history", area: null, drive_min: null, indoor: null, why: null, note: null, image: null,
    rating: null, reviews: null, yelp_rating: null, price: null, cuisine: null, meals: [],
    status: null, confirm: null, season: null, hours: [], links: {}, extra: {},
    images: over.image ? [over.image] : [], gallery: true, gallery_from: null, lat: null, lon: null, place_id: null, phone: null,
    ...over,
  };
}

const ITEMS: BankItem[] = [
  item({
    id: "museum", label: "Air museum", area: "Southside", drive_min: 40, indoor: true,
    why: "Planes for the aviation fan.", image: "travel/visits/v/assets/museum.jpg",
    links: { website: "https://air.example", maps: "https://maps.example/a", tickets: "https://t.example" },
  }),
  item({ id: "falls", label: "Waterfall", group: "daytrip", group_label: "Day trips", drive_min: 75, indoor: false, season: "Oct" }),
  item({
    id: "cafe", kind: "restaurant", label: "Cafe Uno", group: "sweets", group_label: "Sweets", lane: null,
    rating: 4.7, reviews: 812, price: "$", cuisine: "Bakery", status: "new", hours: ["7a-3p"],
    links: { maps: "https://maps.example/c" }, extra: { fit: "mild" },
  }),
];

const DATA: VisitBank = {
  visit: "v", title: "Family visit", doc: "travel/visits/v/v.md", start: "2030-10-10", end: "2030-10-17",
  outings_title: "Idea bank", restaurants_title: "Food bank", notes: ["updated 5/1"],
  groups: [
    { key: "history", label: "History", kind: "outing", count: 1 },
    { key: "daytrip", label: "Day trips", kind: "outing", count: 1 },
    { key: "sweets", label: "Sweets", kind: "restaurant", count: 1 },
  ],
  items: ITEMS,
};

function stubPointer(coarse: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((q: string) => ({
      matches: q.includes("coarse") ? coarse : false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

beforeEach(() => stubPointer(false));
afterEach(() => vi.unstubAllGlobals());

describe("BankTiles helpers", () => {
  it("formats drive time", () => {
    expect(driveText(null)).toBeNull();
    expect(driveText(0)).toBe("at home");
    expect(driveText(40)).toBe("40m drive");
    expect(driveText(60)).toBe("1h drive");
    expect(driveText(75)).toBe("1h 15m drive");
  });
  it("puts Reserve first, then Maps", () => {
    expect(orderedLinks(ITEMS[0].links).map(([k]) => k)).toEqual(["maps", "tickets", "website"]);
    expect(orderedLinks({ website: "w", maps: "m", reservations: "r", order: "o" }).map(([k]) => k))
      .toEqual(["reservations", "maps", "website", "order"]);
  });
  it("finds today's hours line", () => {
    const hours = ["Mon 11 AM–9 PM", "Sat 10 AM–10 PM"];
    expect(hoursOn(hours, new Date(2026, 9, 5))).toBe("11 AM–9 PM"); // a Monday
    expect(hoursOn(hours, new Date(2026, 9, 6))).toBeNull(); // Tuesday: no line
    expect(hoursOn(["7a-3p"], new Date(2026, 9, 5))).toBeNull(); // free text stays free text
  });
  it("steps photos with wraparound", () => {
    expect(step(0, 1, 3)).toBe(1);
    expect(step(2, 1, 3)).toBe(0);
    expect(step(0, -1, 3)).toBe(2);
    expect(step(0, 1, 0)).toBe(0);
  });
  it("rain-safe hides only outdoor items", () => {
    expect(visibleItems(ITEMS, "outing", null, true).map((i) => i.id)).toEqual(["museum"]);
    expect(visibleItems(ITEMS, "restaurant", null, true).map((i) => i.id)).toEqual(["cafe"]);
  });
});

describe("BankTiles", () => {
  it("shows the outings first, with switch counts", () => {
    render(<BankTiles data={DATA} />);
    expect(screen.getByRole("button", { name: /Outings 2/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Air museum")).toBeInTheDocument();
    expect(screen.getByText("Waterfall")).toBeInTheDocument();
    expect(screen.queryByText("Cafe Uno")).not.toBeInTheDocument();
    expect(screen.getByText("no photo")).toBeInTheDocument(); // the Waterfall placeholder
  });

  it("switches to restaurants and shows their notes", () => {
    render(<BankTiles data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Restaurants 1/ }));
    expect(screen.getByText("Cafe Uno")).toBeInTheDocument();
    expect(screen.getByText("4.7★ (812) · $ · Bakery")).toBeInTheDocument();
    expect(screen.getByText("updated 5/1")).toBeInTheDocument();
    expect(screen.queryByText("Air museum")).not.toBeInTheDocument();
  });

  it("filters by group chip and the rain-safe toggle", () => {
    render(<BankTiles data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Day trips 1/ }));
    expect(screen.queryByText("Air museum")).not.toBeInTheDocument();
    expect(screen.getByText("Waterfall")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /rain-safe/ }));
    expect(screen.queryByText("Waterfall")).not.toBeInTheDocument();
    expect(screen.getByText("Nothing in this filter.")).toBeInTheDocument();
  });

  it("loads tile images through the vault image read", async () => {
    // A path no other test renders: the component's image cache is module-level, so a path an
    // earlier test already loaded is a cache hit and never reaches readImage.
    const path = "travel/visits/v/assets/hangar.jpg";
    const data = { ...DATA, items: [item({ id: "hangar", label: "Hangar", image: path })] };
    render(<BankTiles data={data} />);
    await waitFor(() => expect(readImage).toHaveBeenCalledWith(path));
    await waitFor(() => expect(document.querySelector(".bank-tile img")).not.toBeNull());
  });

  it("hover shows the quick card", () => {
    render(<BankTiles data={DATA} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Air museum/ }));
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText("Southside · 40m drive · indoor")).toBeInTheDocument();
    expect(within(tip).getByText("Planes for the aviation fan.")).toBeInTheDocument();
  });

  it("click opens the detail popup with link buttons in a new tab", () => {
    render(<BankTiles data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Restaurants/ }));
    fireEvent.click(screen.getByRole("button", { name: /Cafe Uno/ }));
    const dialog = screen.getByRole("dialog", { name: "Cafe Uno" });
    const maps = within(dialog).getByRole("link", { name: /Google Maps/ });
    expect(maps).toHaveAttribute("href", "https://maps.example/c");
    expect(maps).toHaveAttribute("target", "_blank");
    expect(within(dialog).getByText("7a-3p")).toBeInTheDocument();
    expect(within(dialog).getByText("mild")).toBeInTheDocument(); // passthrough field
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a tap on touch opens the popup directly, with no hover card", () => {
    stubPointer(true);
    render(<BankTiles data={DATA} />);
    const tile = screen.getByRole("button", { name: /Air museum/ });
    fireEvent.mouseEnter(tile);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.click(tile);
    expect(screen.getByRole("dialog", { name: "Air museum" })).toBeInTheDocument();
  });

  it("the visit link opens the folder-note", async () => {
    render(<BankTiles data={DATA} />);
    fireEvent.click(screen.getByText("Family visit ↗"));
    await waitFor(() => expect(screen.getByText("The visit note")).toBeInTheDocument());
  });

  it("renders an honest empty state", () => {
    render(<BankTiles data={{ ...DATA, visit: "", title: "No live visit carries a bank yet", items: [], groups: [] }} />);
    expect(screen.getByText(/No live visit carries a bank yet/)).toBeInTheDocument();
  });

  it("tile arrows page the gallery without opening the popup", async () => {
    const gallery = ["a/cover.jpg", "a/gallery/x/01.jpg", "a/gallery/x/02.jpg"];
    const data = { ...DATA, items: [{ ...ITEMS[0], images: gallery }, ...ITEMS.slice(1)] };
    render(<BankTiles data={data} />);
    const tile = screen.getByRole("button", { name: /Air museum/ });
    expect(within(tile).getByText("1/3")).toBeInTheDocument();
    fireEvent.click(within(tile).getByRole("button", { name: "Next photo" }));
    expect(within(tile).getByText("2/3")).toBeInTheDocument();
    await waitFor(() => expect(readImage).toHaveBeenCalledWith("a/gallery/x/01.jpg"));
    fireEvent.click(within(tile).getByRole("button", { name: "Previous photo" }));
    fireEvent.click(within(tile).getByRole("button", { name: "Previous photo" }));
    expect(within(tile).getByText("3/3")).toBeInTheDocument(); // wraps
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a single photo shows no arrows", () => {
    render(<BankTiles data={DATA} />);
    const tile = screen.getByRole("button", { name: /Air museum/ });
    expect(within(tile).queryByRole("button", { name: "Next photo" })).not.toBeInTheDocument();
  });

  it("Enter on a focused tile opens the popup", () => {
    render(<BankTiles data={DATA} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /Air museum/ }), { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Air museum" })).toBeInTheDocument();
  });
});

describe("Gallery", () => {
  it("the large gallery pages with the arrow keys", () => {
    render(<Gallery paths={["p/1.jpg", "p/2.jpg"]} alt="x" large />);
    expect(screen.getByText("1/2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("2/2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("1/2")).toBeInTheDocument();
  });

  it("a horizontal swipe pages on touch", () => {
    const { container } = render(<Gallery paths={["p/1.jpg", "p/2.jpg", "p/3.jpg"]} alt="x" />);
    const el = container.querySelector(".bank-img")!;
    fireEvent.touchStart(el, { touches: [{ clientX: 200 }] });
    fireEvent.touchEnd(el, { changedTouches: [{ clientX: 100 }] });
    expect(screen.getByText("2/3")).toBeInTheDocument();
    fireEvent.touchStart(el, { touches: [{ clientX: 100 }] });
    fireEvent.touchEnd(el, { changedTouches: [{ clientX: 110 }] }); // too short: no page
    expect(screen.getByText("2/3")).toBeInTheDocument();
  });
});

describe("BankTiles enrichment", () => {
  it("shows Reserve, the week of hours and a phone link in the popup", () => {
    const today = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date().getDay()];
    const it0 = { ...ITEMS[2], phone: "555-0100",
      hours: ["Mon 11 AM–9 PM", "Tue 11 AM–9 PM", "Wed 11 AM–9 PM", "Thu 11 AM–9 PM", "Fri 11 AM–10 PM",
              "Sat 10 AM–10 PM", "Sun 10 AM–9 PM"],
      links: { maps: "https://maps.example/c", reservations: "https://www.opentable.com/r/1" } };
    render(<BankTiles data={{ ...DATA, items: [it0] }} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Cafe Uno/ }));
    expect(within(screen.getByRole("tooltip")).getByText(/^Today: /)).toBeInTheDocument();
    expect(within(screen.getByRole("tooltip")).getByText("Reservable ✓")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Cafe Uno/ }));
    const dialog = screen.getByRole("dialog", { name: "Cafe Uno" });
    const row = dialog.querySelector(".bank-links") as HTMLElement;
    expect(within(row).getAllByRole("link").map((a) => a.textContent)).toEqual(["Reserve ↗", "Google Maps ↗"]);
    expect(within(dialog).getByText(new RegExp(`^${today} `))).toHaveClass("today");
    expect(within(dialog).getByRole("link", { name: "555-0100" })).toHaveAttribute("href", "tel:5550100");
  });
});
