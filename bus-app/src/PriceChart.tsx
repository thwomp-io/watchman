// PriceChart — daily candles + volume + the clustered levels from `hn finance bars --json`, on
// TradingView Lightweight Charts (Apache-2.0; canvas, crosshair, zoom/pan). Levels are drawn as
// dashed price lines: support below the close, overhead above it (a broken support) — the same
// read `bars` prints, as a picture. Colors come from the active theme's CSS variables.

import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, createChart, CrosshairMode, HistogramSeries, LineStyle,
  type IChartApi, type UTCTimestamp,
} from "lightweight-charts";
import type { BarsReport } from "./generated/contracts";

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// alpha-blend a theme color (hex or rgb) for the volume bars without a second token set
function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  return color;
}

export function toSeries(report: BarsReport) {
  const candles = report.bars.map((b) => ({
    time: Math.floor(Date.parse(b.t) / 1000) as UTCTimestamp,
    open: b.o, high: b.h, low: b.l, close: b.c,
  }));
  const volume = report.bars.map((b) => ({
    time: Math.floor(Date.parse(b.t) / 1000) as UTCTimestamp,
    value: b.v,
    up: b.c >= b.o,
  }));
  return { candles, volume };
}

export default function PriceChart({ data }: { data: BarsReport }) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!host.current || !data?.bars?.length) return;
    const pos = cssVar("--pos", "#157a3a");
    const neg = cssVar("--neg", "#c03018");
    const ink = cssVar("--ink", "#222");
    const grid = withAlpha(cssVar("--ink-dim", "#888"), 0.12);
    const chart = createChart(host.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: ink,
                fontFamily: cssVar("--mono", "monospace"), fontSize: 11 },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    chartRef.current = chart;
    const { candles, volume } = toSeries(data);
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: pos, downColor: neg, borderUpColor: pos, borderDownColor: neg,
      wickUpColor: pos, wickDownColor: neg,
    });
    candleSeries.setData(candles);
    for (const lv of data.levels) {
      candleSeries.createPriceLine({
        price: lv.level,
        color: lv.kind === "overhead" ? neg : pos,
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
        title: `${lv.kind === "overhead" ? "overhead" : "support"} ×${lv.touches}`,
      });
    }
    const volSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" } }, 1);
    volSeries.setData(volume.map((v) => ({ time: v.time, value: v.value,
                                          color: withAlpha(v.up ? pos : neg, 0.45) })));
    chart.panes()[1]?.setHeight(70);
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  if (!data?.bars?.length) {
    return <p className="empty">{data?.notes?.length ? data.notes.join(" · ") : "NO BARS"}</p>;
  }
  return (
    <div className="price-chart">
      <div className="price-chart-meta">
        {data.symbol} · last {data.last_close?.toFixed(2) ?? "—"} ({data.as_of ?? "—"}) · {data.sessions} sessions
      </div>
      <div className="price-chart-canvas" ref={host} />
    </div>
  );
}
