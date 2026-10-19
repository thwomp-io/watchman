import { expect, it } from "vitest";
import { toCells } from "./CorrelationHeatmap";
import type { CorrelationReport } from "./generated/contracts";

it("flattens the matrix into one cell per symbol pair, row-major", () => {
  const rep = { symbols: ["A", "B"], matrix: [[1, 0.4], [0.4, 1]] } as unknown as CorrelationReport;
  expect(toCells(rep)).toEqual([
    { a: "A", b: "A", r: 1 }, { a: "A", b: "B", r: 0.4 }, { a: "B", b: "A", r: 0.4 }, { a: "B", b: "B", r: 1 },
  ]);
});
