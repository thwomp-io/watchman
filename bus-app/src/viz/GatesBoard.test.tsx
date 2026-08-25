// The plan-gates board twin: rows render ranked with countdowns; a hot gate
// (≤2d) gets the hot treatment; an estimated print carries the honest (est) chip; the empty
// board reads calm, never broken (demo-seal / fresh-clone rule).
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GatesBoard from "./GatesBoard";

const fixture = {
  as_of: "2026-01-15T20:00:00",
  horizon_days: 30,
  // fictional symbols + events — shipped fixtures must never carry real holdings
  gates: [
    { kind: "print", symbol: "ACME", label: "ACME print", date: "2026-01-16", days: 1, confirmed: true },
    { kind: "macro", symbol: null, label: "CPI (Dec data)", date: "2026-01-20", days: 5, confirmed: true },
    { kind: "print", symbol: "OTCX", label: "OTCX print (est)", date: "2026-01-29", days: 14, confirmed: false },
  ],
  notes: [],
};

describe("GatesBoard", () => {
  it("renders every gate ranked, with countdowns and dates", () => {
    render(<GatesBoard data={fixture} />);
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("1d")).toBeInTheDocument();
    expect(screen.getByText("5d")).toBeInTheDocument();
    expect(screen.getByText("14d")).toBeInTheDocument();
    expect(screen.getByText("CPI (Dec data)")).toBeInTheDocument();
  });

  it("marks a ≤2d gate hot and an estimated print with the est chip", () => {
    const { container } = render(<GatesBoard data={fixture} />);
    expect(container.querySelectorAll(".gates-row.hot").length).toBe(1);
    expect(screen.getByText("est")).toBeInTheDocument();
  });

  it("renders TODAY for day-zero gates", () => {
    render(<GatesBoard data={{ ...fixture, gates: [{ kind: "macro", label: "FOMC decision", date: "2026-01-15", days: 0, confirmed: true }] }} />);
    expect(screen.getByText("TODAY")).toBeInTheDocument();
  });

  it("renders a calm empty state", () => {
    render(<GatesBoard data={{ ...fixture, gates: [] }} />);
    expect(screen.getByText(/clear runway/)).toBeInTheDocument();
  });
});
