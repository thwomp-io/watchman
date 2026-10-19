import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fmtLevel, indexRows, sessionText, TickerStrip, type RailRow } from "./TickerStrip";

const row = (o: Partial<RailRow>): RailRow => ({
  symbol: "X", name: "X", price: 1, change: 0, change_pct: 0, market_state: "", market_time: 1790834992,
  contract: null, contract_month: null, roll_day: false, rolled_from: null, session_date: null,
  live: false, session_label: "", session_note: "", ...o,
});

const DATA = {
  rails: {
    futures: [row({ symbol: "^TNX", name: "US 10-yr yield", price: 5.29, change: 0.0294, change_pct: 0.56 })],
    rates: [row({ symbol: "^TNX", name: "dup — first rail wins", price: 9 })],
    oil: [
      row({ symbol: "CL=F", name: "WTI crude · Nov 26", price: 89.69, change: -0.73, change_pct: -0.807,
            contract_month: "Nov 26", live: true, session_label: "live" }),
      row({ symbol: "HO=F", name: "ULSD diesel · Nov 26", price: 4.6534, change: -0.0347, change_pct: -0.74,
            contract_month: "Nov 26", live: false, session_label: "Fri 09-25 close" }),
    ],
  },
};

describe("TickerStrip", () => {
  it("formats levels by magnitude: four places under 10, grouped above", () => {
    expect(fmtLevel(2.976)).toBe("2.976");
    expect(fmtLevel(4.6534)).toBe("4.6534");
    expect(fmtLevel(1.17)).toBe("1.17");
    expect(fmtLevel(68929.99)).toBe("68,929.99");
  });

  it("indexes rows by symbol, first rail wins", () => {
    expect(indexRows(DATA).get("^TNX")?.price).toBe(5.29);
  });

  it("names the session: a live time or the closed day", () => {
    expect(sessionText(DATA.rails.oil[0])).toMatch(/^LIVE /);
    expect(sessionText(DATA.rails.oil[1])).toBe("CLOSED Fri 09-25");
  });

  it("renders level, move and contract month; yields in % with bp; unknown symbols say so", () => {
    const { container } = render(<TickerStrip data={DATA} tickers={["CL=F", "^TNX", "HO=F", "NOPE"]} />);
    const cells = container.querySelectorAll(".ticker-cell");
    expect(cells).toHaveLength(4);
    expect(cells[0].className).toContain("neg");
    expect(cells[0].textContent).toContain("WTI crude");
    expect(cells[0].textContent).toContain("Nov 26");
    expect(cells[0].textContent).toContain("89.69");
    expect(cells[0].textContent).toContain("-0.81%");
    expect(cells[1].textContent).toContain("5.29%");
    expect(cells[1].textContent).toContain("+3bp");
    expect(cells[1].textContent).not.toContain("0.56%");
    expect(cells[2].textContent).toContain("-0.0347");
    expect(screen.getByText("not on the feed")).toBeTruthy();
  });
});
