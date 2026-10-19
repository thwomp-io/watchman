import { describe, expect, it } from "vitest";
import { toSeries } from "./PriceChart";
import type { BarsReport } from "./generated/contracts";

const report: BarsReport = {
  symbol: "X", days: 90, last_close: 11, as_of: "2026-09-30", sessions: 2,
  levels: [], notes: [],
  bars: [
    { t: "2026-09-29T04:00:00Z", o: 10, h: 12, l: 9, c: 11, v: 100 },
    { t: "2026-09-30T04:00:00Z", o: 11, h: 11.5, l: 10, c: 10.5, v: 80 },
  ],
};

describe("PriceChart.toSeries", () => {
  it("maps the wire bars (t/o/h/l/c/v) to candles and up/down volume", () => {
    const { candles, volume } = toSeries(report);
    expect(candles[0]).toEqual({ time: Date.parse("2026-09-29T04:00:00Z") / 1000, open: 10, high: 12, low: 9, close: 11 });
    expect(volume.map((v) => v.up)).toEqual([true, false]);
  });
});
