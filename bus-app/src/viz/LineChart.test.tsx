import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import LineChart, { timeTicks } from "./LineChart";

const mk = (pts: [string, number][]) => ({
  title: "Net worth", yPrefix: "$",
  series: [{ label: "Net worth", points: pts.map(([x, y]) => ({ x, y })) }],
});

// Two personas log net worth on the SAME dates, different magnitudes — the line must redraw to the new
// values (the axes do).
const DATES = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01", "2024-07-01"];
const series = (ys: number[]) => mk(DATES.map((d, i) => [d, ys[i]]));
const INVESTOR = series([100000, 105000, 110000, 108000, 115000, 120000, 125000]);
const GROWTH = series([40000, 42000, 41000, 45000, 48000, 52000, 60000]);

const linePath = (c: HTMLElement) => c.querySelector("svg.line-chart path")?.getAttribute("d") ?? "";
const pointCount = (c: HTMLElement) => c.querySelectorAll("svg.line-chart circle").length;

describe("LineChart — redraw on data change (pack swap)", () => {
  it("repositions the line path when the series values change (same dates)", () => {
    const { container, rerender } = render(<LineChart data={INVESTOR} />);
    const a = linePath(container);
    expect(a).not.toBe("");
    rerender(<LineChart data={GROWTH} />);
    expect(linePath(container)).not.toBe(a);
  });

  // THE REGRESSION: xDom (the zoom window) is seeded once from the data on
  // mount and was never reconciled. Swapping from a NARROW-range series (e.g. a few recent real points)
  // to a WIDER one (a demo persona spanning months) left the window pinned, so the new line was clipped
  // to the old window — only a remount (switching to a structurally different dashboard) cleared it.
  it("spans the full new series after swapping to a wider date range (not clipped to the old window)", () => {
    const NARROW = mk([["2024-06-01", 100], ["2024-07-01", 110]]); // 2 recent points
    const { container, rerender } = render(<LineChart data={NARROW} />);
    expect(pointCount(container)).toBe(2);

    rerender(<LineChart data={INVESTOR} />); // 7 points over a much wider range
    // without the extent-reset the window stays [06-01, 07-01] → only 2 of 7 points show.
    expect(pointCount(container)).toBe(7);
  });

  // THE REGRESSION: an EMPTY series (the offline/keyless position-chart, which
  // emits `series:[{points:[]}]`) made d3.extent yield [undefined, undefined] → xDom[0].getTime() threw →
  // the ErrorBoundary showed "RENDER FAULT" on every pack. An empty chart must render a clean note instead.
  it("renders a note (not a crash) when the series is empty", () => {
    const empty = { title: "AAPL — price history", subtitle: "needs a live key", series: [{ label: "AAPL", points: [] }] };
    const { container, getByText } = render(<LineChart data={empty} />);
    expect(container.querySelector("svg.line-chart")).toBeNull(); // no chart drawn
    expect(getByText("needs a live key")).toBeTruthy();           // the subtitle note shows
  });
});

const D = (iso: string) => new Date(`${iso}T00:00:00Z`);
const labels = (dom: [string, string], w: number) => timeTicks([D(dom[0]), D(dom[1])], w).map((t) => t.label);

describe("LineChart — x-axis time ticks", () => {
  it("a ~4-month series on a phone-width plot still gets every month", () => {
    expect(labels(["2026-05-29", "2026-10-08"], 250)).toEqual(["Jun '26", "Jul", "Aug", "Sep", "Oct"]);
  });

  it("a ~4-month series reads in months on the 1st, year on the first tick", () => {
    const ticks = timeTicks([D("2026-05-29"), D("2026-10-08")], 900);
    expect(ticks.map((t) => t.label)).toEqual(["Jun '26", "Jul", "Aug", "Sep", "Oct"]);
    // placed by real date (the 1st of each month), not by point index
    expect(ticks.map((t) => t.t.toISOString().slice(0, 10))).toEqual(
      ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"]);
  });

  it("a year boundary carries the new year; a narrow plot steps months so labels fit", () => {
    expect(labels(["2026-10-01", "2027-03-15"], 900)).toEqual(["Oct '26", "Nov", "Dec", "Jan '27", "Feb", "Mar"]);
    const narrow = labels(["2026-05-29", "2027-05-29"], 260); // ~4 labels fit
    expect(narrow.length).toBeLessThanOrEqual(4);
    expect(narrow[0]).toMatch(/'26$/);
  });

  it("two years at a phone width step to quarters or halves; many years read as years", () => {
    expect(labels(["2026-05-29", "2028-05-29"], 300).length).toBeLessThanOrEqual(4);
    expect(labels(["2026-01-15", "2034-01-15"], 300)).toEqual(["2028", "2030", "2032", "2034"]);
    // four years at desktop width: quarters, each January carrying its year
    const four = labels(["2026-01-15", "2030-01-15"], 900);
    expect(four.slice(0, 5)).toEqual(["Apr '26", "Jul", "Oct", "Jan '27", "Apr"]);
    expect(four.length).toBeLessThanOrEqual(Math.floor(900 / 48));
  });

  it("a short (zoomed) window reads in days", () => {
    expect(labels(["2026-09-01", "2026-09-30"], 900).every((l) => /^[A-Z][a-z]{2} \d{2}$/.test(l))).toBe(true);
  });

  it("the rendered chart draws the month labels", () => {
    const { container } = render(<LineChart data={INVESTOR} />);
    const ticks = [...container.querySelectorAll("svg.line-chart text.x-tick")].map((t) => t.textContent);
    expect(ticks[0]).toBe("Jan '24");
    expect(ticks).toContain("Apr");
  });
});
