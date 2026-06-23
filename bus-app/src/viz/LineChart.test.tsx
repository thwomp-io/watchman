import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import LineChart from "./LineChart";

const mk = (pts: [string, number][]) => ({
  title: "Net worth", yPrefix: "$",
  series: [{ label: "Net worth", points: pts.map(([x, y]) => ({ x, y })) }],
});

// Two personas log net worth on the SAME dates, different magnitudes — the line must redraw to the new
// values (the axes do).
const DATES = ["2026-03-31", "2026-04-15", "2026-04-30", "2026-05-15", "2026-05-29", "2026-06-12", "2026-06-19"];
const series = (ys: number[]) => mk(DATES.map((d, i) => [d, ys[i]]));
const INVESTOR = series([1062000, 1078500, 1071200, 1094800, 1103400, 1121900, 1139029]);
const GROWTH = series([168400, 175200, 171900, 188650, 202100, 219400, 249551]);

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

  // THE REGRESSION (the maintainer's eyeball 2026-06-20): xDom (the zoom window) is seeded once from the data on
  // mount and was never reconciled. Swapping from a NARROW-range series (e.g. a few recent real points)
  // to a WIDER one (a demo persona spanning months) left the window pinned, so the new line was clipped
  // to the old window — only a remount (switching to a structurally different dashboard) cleared it.
  it("spans the full new series after swapping to a wider date range (not clipped to the old window)", () => {
    const NARROW = mk([["2026-06-12", 100], ["2026-06-19", 110]]); // 2 recent points
    const { container, rerender } = render(<LineChart data={NARROW} />);
    expect(pointCount(container)).toBe(2);

    rerender(<LineChart data={INVESTOR} />); // 7 points over a much wider range
    // without the extent-reset the window stays [06-12, 06-19] → only 2 of 7 points show.
    expect(pointCount(container)).toBe(7);
  });

  // THE REGRESSION (the maintainer's eyeball 2026-06-21): an EMPTY series (the offline/keyless position-chart, which
  // emits `series:[{points:[]}]`) made d3.extent yield [undefined, undefined] → xDom[0].getTime() threw →
  // the ErrorBoundary showed "RENDER FAULT" on every pack. An empty chart must render a clean note instead.
  it("renders a note (not a crash) when the series is empty", () => {
    const empty = { title: "AAPL — price history", subtitle: "needs a live key", series: [{ label: "AAPL", points: [] }] };
    const { container, getByText } = render(<LineChart data={empty} />);
    expect(container.querySelector("svg.line-chart")).toBeNull(); // no chart drawn
    expect(getByText("needs a live key")).toBeTruthy();           // the subtitle note shows
  });
});
