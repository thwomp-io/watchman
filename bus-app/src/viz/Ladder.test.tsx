// The trap-map ladder twin: renders every laddered symbol; a mover's rungs and the
// honest-unquotable state both render; the empty slate gets a calm empty state (the demo-seal /
// fresh-clone rule: an empty state must read as calm, never broken).
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Ladder from "./Ladder";

const fixture = {
  as_of: "2026-01-15T20:00:00",
  committed: 12000.0,
  symbols: [
    {
      // fictional symbol + numbers — shipped fixtures must never carry real holdings
      // (the release scan enforces this)
      symbol: "ACME", price: 105.57, prev_close: 108.84, day_change_pct: -3.0,
      rungs: [{ side: "buy", qty: 3, limit: 95.5, value: 286.5, distance_pct: 9.5,
                expires: "2027-01-01", note: "example rung" }],
      supports: [
        { level: 100.0, touches: 3, last_touch: "2026-07-01", distance_pct: -5.1 },  // clear of the rung → labeled
        { level: 95.8, touches: 1, last_touch: "2026-06-20", distance_pct: -9.0 },   // <10px from the rung → line only
      ],
      lo: 90.0, hi: 112.0,
    },
    {
      symbol: "OTCX", price: null, prev_close: null, day_change_pct: null,
      rungs: [{ side: "buy", qty: 10, limit: 20.0, value: 200.0, distance_pct: null }],
      supports: [], lo: 19.0, hi: 21.0,
    },
  ],
  notes: [],
};

describe("Ladder", () => {
  it("renders a ladder per symbol with rungs, shelves, price, and committed total", () => {
    const { container } = render(<Ladder data={fixture} />);
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText(/committed by resting buys/)).toBeInTheDocument();
    expect(screen.getByText("3 @ 95.50")).toBeInTheDocument();    // in-bar label (v2: widest rung = map max)
    expect(screen.getByText(/\$100 ×3/)).toBeInTheDocument();      // the clear shelf is labeled
    expect(screen.queryByText(/\$95.80/)).toBeNull();               // the crowded shelf: line only (de-clutter)
    expect(container.querySelectorAll(".ladder-shelf").length).toBe(2); // both LINES render regardless
    expect(container.querySelectorAll(".ladder-price").length).toBe(1); // only the quotable symbol
    expect(screen.getByText("unquotable")).toBeInTheDocument();    // honest degradation
  });

  it("an empty slate renders calm, not broken", () => {
    render(<Ladder data={{ as_of: "x", symbols: [] }} />);
    expect(screen.getByText(/SLATE IS EMPTY/)).toBeInTheDocument();
  });
});
