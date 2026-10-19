// NudgeCards — the Planning tab's proactive-nudge band: card render, the portal
// tooltip on hover (component breakdown + hook), click→DocPopup on the destination doc, the
// coarse-pointer two-tap (tap 1 = card, tap 2 = doc), and the empty state. DocPopup's readDoc is
// mocked at the api layer (the ScorecardBook/ProjectionTiles pattern).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NudgeCards, { type Nudge, type NudgeData } from "./NudgeCards";

vi.mock("./api", async (importOriginal) => {
  const mod = await importOriginal<Record<string, unknown>>();
  return { ...mod, readDoc: vi.fn().mockResolvedValue("# The destination doc\n\nbody") };
});
vi.mock("./nav", () => ({
  useNav: () => ({ navigate: vi.fn() }),
}));

function nudge(over: Partial<Nudge>): Nudge {
  return {
    slug: "sample-cove",
    display: "Sample Cove",
    city: "Sample Cove",
    window: { start: "2026-10-02", end: "2026-10-04", kind: "weekend" },
    score: 47,
    components: [
      { name: "season", points: 20, fact: "peak October season there" },
      { name: "feasibility", points: -12, fact: "~6h flight (long for a plain weekend)" },
    ],
    reason: "peak October season there",
    events: [],
    doc: "travel/destinations/region/beach/sample-cove/sample-cove.md",
    line: "You could be in Sample Cove Fri Oct 2 → Sun Oct 4 — peak October season there",
    ...over,
  };
}

const DATA: NudgeData = { nudges: [nudge({})], skipped: [], note: "" };

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

afterEach(() => vi.unstubAllGlobals());

describe("NudgeCards", () => {
  it("renders a card with destination, window and reason", () => {
    stubPointer(false);
    render(<NudgeCards data={DATA} />);
    expect(screen.getByText("Sample Cove")).toBeInTheDocument();
    expect(screen.getByText(/Oct 2/)).toBeInTheDocument();
    expect(screen.getByText("peak October season there")).toBeInTheDocument();
    expect(screen.getByText("47")).toBeInTheDocument();
  });

  it("hover shows the portal tooltip with the component breakdown", () => {
    stubPointer(false);
    render(<NudgeCards data={DATA} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Sample Cove/ }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.getByText("+20")).toBeInTheDocument();
    expect(screen.getByText("-12")).toBeInTheDocument();
    expect(screen.getByText(/6h flight/)).toBeInTheDocument();
  });

  it("hook and long-weekend chip render when present", () => {
    stubPointer(false);
    render(
      <NudgeCards
        data={{
          nudges: [
            nudge({
              window: { start: "2026-09-04", end: "2026-09-07", kind: "long-weekend", anchor: "Fixture Day" },
              hook: "beach by Saturday lunch",
            }),
          ],
        }}
      />,
    );
    expect(screen.getByText("3-day")).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByRole("button", { name: /Sample Cove/ }));
    expect(screen.getByText(/beach by Saturday lunch/)).toBeInTheDocument();
    expect(screen.getByText(/Fixture Day/)).toBeInTheDocument();
  });

  it("groups cards by lane in order with a header each; family cards carry no score", () => {
    stubPointer(false);
    render(
      <NudgeCards
        data={{
          nudges: [
            nudge({ slug: "far-isle", display: "Far Isle", lane: "horizon", score: 41,
              window: { start: "2026-11-01", end: "2027-02-28", kind: "season", title: "best Nov–Feb" },
              reason: "never on your calendar and screen-calibrated (geological) — the exposure-building pick" }),
            nudge({ slug: "family-2026-11-26", display: "Turkey Day", lane: "family", score: 0, doc: "",
              window: { start: "2026-11-26", end: "2026-11-29", kind: "family", anchor: "Turkey Day" },
              reason: "book the flights home early" }),
            nudge({}), // weekend (no lane field → weekend)
            nudge({ slug: "deep-cove", display: "Deep Cove", lane: "long-lead", score: 54,
              window: { start: "2026-12-19", end: "2026-12-27", kind: "week", anchor: "Holiday", pto_days: 4 } }),
          ],
        }}
      />,
    );
    const headers = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(headers).toEqual(["Weekends", "Long-lead · 3–12 months", "Horizon · worth a look", "Family windows"]);
    expect(screen.getByText("best Nov–Feb")).toBeInTheDocument(); // the season title IS the window line
    expect(screen.getByText("8 nights · 4 PTO")).toBeInTheDocument(); // the block chip
    expect(screen.queryByText("0")).not.toBeInTheDocument(); // family cards show no score
    expect(screen.getByRole("button", { name: /Turkey Day .* family window/ })).toBeInTheDocument();
  });

  it("click opens DocPopup on the destination doc (fine pointer)", async () => {
    stubPointer(false);
    render(<NudgeCards data={DATA} />);
    fireEvent.click(screen.getByRole("button", { name: /Sample Cove/ }));
    await waitFor(() => expect(screen.getByText("The destination doc")).toBeInTheDocument());
  });

  it("coarse pointer: first tap arms the tooltip, second opens the doc", async () => {
    stubPointer(true);
    render(<NudgeCards data={DATA} />);
    const card = screen.getByRole("button", { name: /Sample Cove/ });
    fireEvent.click(card);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.queryByText("The destination doc")).not.toBeInTheDocument();
    fireEvent.click(card);
    await waitFor(() => expect(screen.getByText("The destination doc")).toBeInTheDocument());
  });

  it("empty data renders the honest empty state", () => {
    stubPointer(false);
    render(<NudgeCards data={{ nudges: [] }} />);
    expect(screen.getByText(/No nudges cleared the bar/)).toBeInTheDocument();
  });
});

describe("NudgeCards — Planning refinements", () => {
  it("why toggles the component rows inline", () => {
    render(<NudgeCards data={DATA} />);
    const why = screen.getByRole("button", { name: "why" });
    expect(why).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(why);
    expect(screen.getByRole("button", { name: "hide why" })).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector(".nudge-why-rows")).toHaveTextContent("~6h flight");
  });

  it("flags a nudge the open decision is weighing, and lists the skipped windows", () => {
    render(<NudgeCards data={{
      nudges: [nudge({ in_decision: true }), nudge({ slug: "pine", display: "Pine Ridge", window: { start: "2030-05-03", end: "2030-05-05", kind: "weekend" } })],
      skipped: ["Fri May 10 → Sun May 12 — Visit already on the calendar"],
    }} />);
    expect(screen.getAllByText("in decision")).toHaveLength(1);
    expect(screen.getByText(/Visit already on the calendar/)).toBeInTheDocument();
  });

  it("family cards carry no why toggle", () => {
    render(<NudgeCards data={{ nudges: [nudge({ lane: "family", display: "Turkey Day" })] }} />);
    expect(screen.queryByRole("button", { name: "why" })).toBeNull();
  });
});

describe("NudgeCards — the GAME WEEKENDS lane", () => {
  it("renders between weekends and long-lead under its own heading, the game leading the line", () => {
    render(<NudgeCards data={{ nudges: [
      nudge({ lane: "long-lead", slug: "far", display: "Far Away" }),
      nudge({ lane: "games", slug: "bay", display: "Bay City", reason: "Waves vs. Comets · Fri 10/16 7:00p — peak October season there" }),
      nudge({}),
    ] }} />);
    const titles = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(titles).toEqual(["Weekends", "Game weekends", "Long-lead · 3–12 months"]);
    expect(screen.getByText(/^Waves vs\. Comets · Fri 10\/16 7:00p/)).toBeInTheDocument();
  });
});
