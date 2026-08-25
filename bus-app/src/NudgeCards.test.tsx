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
