// CalendarCards — one card per live trip/visit with its countdown + chips, a click deep-links to the
// subtab that follows that kind's picker with the card picked, and parked ideas ride as "someday".

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CalendarCards, { countdown } from "./CalendarCards";
import type { CalendarCard, PlanningCalendar } from "./generated/contracts";

const navigate = vi.fn();
vi.mock("./nav", () => ({ useNav: () => ({ navigate }) }));
vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readImage: vi.fn().mockResolvedValue("data:,") };
});

function card(over: Partial<CalendarCard>): CalendarCard {
  return {
    slug: "2030-06-14-sample-visit", kind: "visit", name: "Sample visit", title: "Sample visit in town", anchor: "",
    destination: "Home", who: ["traveler-a", "traveler-b"], status: "planning", start: "2030-06-14", end: "2030-06-21",
    window: "Fri 6/14 → Fri 6/21", days: 6, when: "in 6d", cover: null, doc: "travel/visits/x/x.md",
    decision: false, ...over,
  };
}

const DATA: PlanningCalendar = {
  as_of: "2030-10-04",
  upcoming: [
    card({}),
    card({ slug: "2031-01-sample-cove", kind: "trip", name: "Sample Cove weekend", days: 96, when: "in 3mo",
           window: "Wed 1/8 → Sat 1/11", who: ["traveler-a", "traveler-c"], decision: true }),
  ],
  someday: [card({ slug: "someday-island", kind: "trip", name: "Someday island", days: null, when: "someday", window: "fall/winter",
                   status: "idea", start: null, end: null })],
};

describe("CalendarCards", () => {
  it("countdown reads days, weeks, months, today and under way", () => {
    expect(countdown(card({ days: 6 }))).toEqual({ big: "6", unit: "days" });
    expect(countdown(card({ days: 1 }))).toEqual({ big: "1", unit: "day" });
    expect(countdown(card({ days: 63 }))).toEqual({ big: "9", unit: "weeks" });
    expect(countdown(card({ days: 96 }))).toEqual({ big: "3", unit: "months" });
    expect(countdown(card({ days: 0 })).big).toBe("TODAY");
    expect(countdown(card({ days: -2 })).big).toBe("NOW");
    expect(countdown(card({ days: null, when: "someday" }))).toEqual({ big: "—", unit: "someday" });
  });

  it("renders a card per live item with its chips, and the someday group", () => {
    render(<CalendarCards data={DATA} />);
    expect(screen.getByText("Sample visit")).toBeInTheDocument();
    expect(screen.getByText("traveler-a, traveler-b")).toBeInTheDocument();
    expect(screen.getAllByText("open decision")).toHaveLength(1);
    expect(screen.getByLabelText("Someday")).toHaveTextContent("Someday island");
  });

  it("a click lands on the subtab that follows the card's kind, with the card picked", () => {
    render(<CalendarCards data={DATA} />);
    fireEvent.click(screen.getByText("Sample Cove weekend").closest("button")!);
    expect(navigate).toHaveBeenCalledWith({ zone: "dash", follows: "trip", pick: "2031-01-sample-cove" });
    fireEvent.click(screen.getByText("Someday island").closest("button")!);
    expect(navigate).toHaveBeenLastCalledWith({ zone: "dash", follows: "trip", pick: "someday-island" });
  });

  it("no someday group when nothing is parked; an honest empty state when nothing is live", () => {
    const { rerender } = render(<CalendarCards data={{ ...DATA, someday: [] }} />);
    expect(screen.queryByLabelText("Someday")).toBeNull();
    rerender(<CalendarCards data={{ as_of: "x", upcoming: [], someday: [] }} />);
    expect(screen.getByText(/Nothing on the calendar/)).toBeInTheDocument();
  });
});
