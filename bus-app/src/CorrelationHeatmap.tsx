// CorrelationHeatmap — the `hn finance correlate --json` matrix as a heatmap (Observable Plot).
// Warm = moves together (less diversifying), cool = moves apart (real ballast), the same reading
// the CLI table colors; each cell prints its r. Plot builds SVG from D3 scales, so a standard chart
// is a few lines instead of hand-built axes.

import { useEffect, useRef, useState } from "react";
import * as Plot from "@observablehq/plot";
import type { CorrelationReport } from "./generated/contracts";

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export interface Cell { a: string; b: string; r: number }

export function toCells(rep: CorrelationReport): Cell[] {
  return rep.symbols.flatMap((a, i) => rep.symbols.map((b, j) => ({ a, b, r: rep.matrix[i]?.[j] ?? 0 })));
}

export default function CorrelationHeatmap({ data }: { data: CorrelationReport }) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (!host.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(host.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!host.current || !width || !data?.symbols?.length) return;
    const n = data.symbols.length;
    const pos = cssVar("--pos", "#157a3a");
    const neg = cssVar("--neg", "#c03018");
    const ink = cssVar("--ink", "#222");
    const size = Math.min(width, 120 + n * 46);
    const chart = Plot.plot({
      width: size,
      height: size - 40,
      marginLeft: 60,
      marginTop: 60,
      padding: 0.04,
      style: { background: "transparent", color: ink, fontFamily: cssVar("--mono", "monospace"), fontSize: "10px" },
      x: { domain: data.symbols, axis: "top", label: null, tickRotate: -45 },
      y: { domain: data.symbols, label: null },
      color: { type: "linear", domain: [-1, 0, 1], range: [pos, "rgba(128,128,128,0.08)", neg] },
      marks: [
        Plot.cell(toCells(data), { x: "b", y: "a", fill: "r", title: (d: Cell) => `${d.a} × ${d.b}: r ${d.r.toFixed(2)}` }),
        Plot.text(toCells(data), {
          x: "b", y: "a", text: (d: Cell) => (d.a === d.b ? "" : d.r.toFixed(2)),
          fill: (d: Cell) => (Math.abs(d.r) >= 0.55 ? "white" : ink), fontSize: n > 14 ? 8 : 10,
        }),
      ],
    });
    host.current.replaceChildren(chart);
    return () => chart.remove();
  }, [data, width]);

  if (!data?.symbols?.length) return <p className="empty">{data?.notes?.join(" · ") || "NO DATA"}</p>;
  return (
    <div className="corr-heatmap">
      <div className="price-chart-meta">
        {data.n_obs} trading days ({data.start} → {data.end}) · daily-return Pearson
        {data.notes.length ? ` · ${data.notes.join(" · ")}` : ""}
      </div>
      <div ref={host} className="corr-heatmap-canvas" />
    </div>
  );
}
