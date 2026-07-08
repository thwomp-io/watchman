// Interactive rank-bar — the `rank-bar` type ({title, subtitle?, note?, max?,
// segments?:[{key,label}], rows:[{label, parts:[{key,value}], total?}]}): a ranked, component-
// stacked horizontal bar. The interactive twin of render.js's renderRankBar — flex/CSS bars (no
// SVG sizing, like Matrix's table approach), so it fills its tile responsively. Hover a segment
// for its value; multi-segment rows show a legend.

import { useState } from "react";
import { useCatColors, type Tip } from "./common";

interface Part {
  key: string;
  value: number;
}
interface BarRow {
  label: string;
  parts?: Part[];
  total?: number;
}
interface BarData {
  title?: string;
  subtitle?: string;
  note?: string;
  max?: number;
  segments?: { key: string; label: string }[];
  rows: BarRow[];
}

export default function BarChart({ data }: { data: BarData }) {
  const COLORS = useCatColors(); // theme-aware categorical set (re-renders on toggle)
  const [tip, setTip] = useState<Tip | null>(null);
  const rows = (data.rows || [])
    .map((r) => ({
      ...r,
      total: r.total ?? (r.parts || []).reduce((s, p) => s + (p.value || 0), 0),
    }))
    .sort((a, b) => b.total - a.total);
  const max = data.max ?? Math.max(1, ...rows.map((r) => r.total));
  const segKeys = data.segments
    ? data.segments.map((s) => s.key)
    : Array.from(new Set(rows.flatMap((r) => (r.parts || []).map((p) => p.key))));
  const colorOf = (k: string) => COLORS[Math.max(0, segKeys.indexOf(k)) % COLORS.length];
  const segLabel = new Map((data.segments || []).map((s) => [s.key, s.label]));
  const multi = segKeys.length > 1;

  return (
    <div className="viz-canvas barchart" onMouseLeave={() => setTip(null)}>
      <div className="bars">
        {rows.map((r) => {
          const parts = r.parts && r.parts.length ? r.parts : [{ key: segKeys[0] || "v", value: r.total }];
          return (
            <div className="bar-row" key={r.label}>
              <span className="bar-label" title={r.label}>{r.label}</span>
              <div className="bar-track">
                {parts.map((p, i) => (
                  <div
                    key={i}
                    className="bar-seg"
                    style={{ width: `${(p.value / max) * 100}%`, background: colorOf(p.key) }}
                    onMouseMove={(e) => {
                      const w = e.currentTarget.closest(".barchart") as HTMLElement;
                      const rect = w.getBoundingClientRect();
                      setTip({
                        x: e.clientX - rect.left,
                        y: e.clientY - rect.top,
                        text: `${r.label}${multi ? ` · ${segLabel.get(p.key) || p.key}` : ""}: ${p.value}`,
                      });
                    }}
                  />
                ))}
              </div>
              <span className="bar-val">{r.total}</span>
            </div>
          );
        })}
        {rows.length === 0 && <p className="empty">NO ROWS IN THIS DATA</p>}
      </div>
      {multi && (
        <div className="bar-legend">
          {segKeys.map((k) => (
            <span key={k}>
              <i style={{ background: colorOf(k) }} />
              {segLabel.get(k) || k}
            </span>
          ))}
        </div>
      )}
      {tip && <div className="viz-tip" style={{ left: tip.x + 14, top: tip.y + 10 }}>{tip.text}</div>}
    </div>
  );
}
