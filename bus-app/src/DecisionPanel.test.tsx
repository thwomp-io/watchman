// DecisionPanel — names its trip, lists the verb's ranking, a row hover brings that trace forward,
// and the panel renders nothing when no decision is live.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DecisionPanel, { type DecisionData, hasDecision } from "./DecisionPanel";

const navigate = vi.fn();
vi.mock("./nav", () => ({ useNav: () => ({ navigate }) }));

const DATA: DecisionData = {
  title: "Fixture decision",
  axes: ["Warmth", "Food", "Value"],
  max: 5,
  candidates: [
    { label: "Pine Ridge", values: [2, 5, 3] },
    { label: "Sample Cove", values: [5, 4, 4] },
  ],
  trip: "2027-02-sample-cove",
  trip_name: "Sample Cove weekend",
  trip_kind: "trip",
  window: "Fri 1/8 → Mon 1/11",
  ranked: [
    { label: "Sample Cove", total: 13, strongest: "Warmth", weakest: "Food" },
    { label: "Pine Ridge", total: 10, strongest: "Food", weakest: "Warmth" },
  ],
};

describe("DecisionPanel", () => {
  it("names the trip and window and lists the ranking in the verb's order", () => {
    render(<DecisionPanel data={DATA} />);
    expect(screen.getByText("Sample Cove weekend — Fri 1/8 → Mon 1/11")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector(".decision-label")?.textContent)).toEqual(["Sample Cove", "Pine Ridge"]);
    expect(rows[0]).toHaveTextContent("13");
  });

  it("hovering a row highlights its trace and dims the others", () => {
    const { container } = render(<DecisionPanel data={DATA} />);
    fireEvent.mouseEnter(screen.getAllByRole("listitem")[1]);
    expect(container.querySelectorAll(".radar-hot")).toHaveLength(1);
    expect(container.querySelectorAll(".radar-dim")).toHaveLength(1);
    fireEvent.mouseLeave(container.querySelector(".decision-rank")!);
    expect(container.querySelectorAll(".radar-dim")).toHaveLength(0);
  });

  it("open the trip deep-links to the trip on its tab", () => {
    render(<DecisionPanel data={DATA} />);
    fireEvent.click(screen.getByText(/open the trip/));
    expect(navigate).toHaveBeenCalledWith({ zone: "dash", follows: "trip", pick: "2027-02-sample-cove" });
  });

  it("renders nothing without a live decision", () => {
    const empty = { ...DATA, candidates: [], ranked: [] };
    expect(hasDecision(empty)).toBe(false);
    expect(hasDecision(null)).toBe(false);
    const { container } = render(<DecisionPanel data={empty} />);
    expect(container).toBeEmptyDOMElement();
  });
});
