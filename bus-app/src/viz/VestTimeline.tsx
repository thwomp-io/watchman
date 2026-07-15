// Vest-planning timeline — the one new viz type for the Unwind tab. The
// `vest_timeline` contract ({today, domain, vests:[{date,units,value,future}], windows:[{start,
// end,kind}]}). Months-scale axis with month labels ALONG THE TOP; vests as $-sized lollipop
// markers with their dates; wash-poison windows shaded amber (no loss-sales), clean windows green
// (the harvest gaps); a TODAY cursor; a legend. Windows are computed deterministically upstream —
// this renders dumb.

import { useState } from "react";
import * as d3 from "d3";
import { fmtNum, useMeasure } from "./common";
import { useTheme, type Theme } from "../theme";

interface Vest { date: string; units: number; value: number; future: boolean }
interface Win { start: string; end: string; kind: "poison" | "clean" }
interface VestTimelineData {
  title?: string; subtitle?: string; today: string;
  domain: [string, string]; vests: Vest[]; windows: Win[];
}

const M = { top: 44, right: 26, bottom: 56, left: 26 };
// instrument palette: caution-amber poison, harvest-green clean, amber vest markers. SVG fills
// are attributes, so the tones are picked per-theme here — each set mirrors its App.css tokens
// (--sig-warn / --pos / --amber / --ink-dim; keep in sync).
const TONES: Record<Theme, { poison: string; clean: string; vest: string; vestPast: string }> = {
  dark: { poison: "#ffb454", clean: "#7dd6a0", vest: "#e8a33d", vestPast: "#79827f" },
  paper: { poison: "#875600", clean: "#1f663c", vest: "#7f5606", vestPast: "#5d5644" },
  bright: { poison: "#8f7500", clean: "#157a3a", vest: "#c8102e", vestPast: "#8b929b" },
  phosphor: { poison: "#e0b83d", clean: "#4ade80", vest: "#3ddc84", vestPast: "#2e5c44" },
  redwatch: { poison: "#ff9a4d", clean: "#74c69d", vest: "#ff4f58", vestPast: "#5c3236" },
  fjord: { poison: "#ebcb8b", clean: "#a3be8c", vest: "#88c0d0", vestPast: "#545d6e" },
  outrun: { poison: "#ffd319", clean: "#00ffa3", vest: "#ff2d95", vestPast: "#5b4a80" },
  abyss: { poison: "#f0b429", clean: "#34d399", vest: "#2dd4bf", vestPast: "#3c5866" },
  dusk: { poison: "#d19a66", clean: "#82c99a", vest: "#b794d4", vestPast: "#574d6b" },
  solar: { poison: "#a16207", clean: "#3f6212", vest: "#b45309", vestPast: "#a89574" },
  mono: { poison: "#8f6400", clean: "#1a7a3a", vest: "#1a1a1a", vestPast: "#8c8c8c" },
};

export default function VestTimeline({ data }: { data: VestTimelineData }) {
  const { poison: POISON, clean: CLEAN, vest: VEST, vestPast: VEST_PAST } = TONES[useTheme()];
  const { ref, width: W, height: H } = useMeasure(0.28);
  const parse = d3.utcParse("%Y-%m-%d");
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  // empty-data guard (the demo-seal rule, AFTER the hooks): a profile with no vest calendar must
  // read CALM — without it, the windows-only render paints a full-canvas "clean window" that
  // reads broken
  if (!data.vests?.length || !data.domain) {
    return (
      <div className="viz-canvas">
        <p className="viz-hint">NO VESTS ON THE CALENDAR — nothing scheduled to plan around</p>
      </div>
    );
  }

  const dom: [Date, Date] = [parse(data.domain[0]) as Date, parse(data.domain[1]) as Date];
  const x = d3.scaleUtc().domain(dom).range([M.left, W - M.right]);
  const baseline = H - M.bottom;
  const maxUnits = d3.max(data.vests, (v) => v.units) ?? 1;
  const stem = d3.scaleLinear().domain([0, maxUnits]).range([0, baseline - M.top - 34]);
  const today = parse(data.today) as Date;
  const months = x.ticks(d3.utcMonth.every(1) as d3.TimeInterval);
  const monthLabel = (t: Date) =>
    t.getUTCMonth() === 0 ? `${d3.utcFormat("%b")(t)} '${d3.utcFormat("%y")(t)}` : d3.utcFormat("%b")(t);

  const legend = [
    { kind: "rect", color: POISON, label: "WASH-POISON · NO LOSS-SALES" },
    { kind: "rect", color: CLEAN, label: "CLEAN · HARVEST WINDOW" },
    { kind: "dot", color: VEST, label: "RSU VEST (sized by $)" },
  ];

  return (
    <div className="viz-canvas" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg vest-timeline"
           onMouseLeave={() => setTip(null)}>
        {/* wash windows — drawn behind everything */}
        {data.windows.map((wd, i) => {
          const x0 = x(parse(wd.start) as Date);
          const x1 = x(parse(wd.end) as Date);
          const c = wd.kind === "poison" ? POISON : CLEAN;
          return (
            <rect key={`w${i}`} x={x0} y={M.top} width={Math.max(0, x1 - x0)} height={baseline - M.top}
                  fill={c} fillOpacity={wd.kind === "poison" ? 0.1 : 0.16}
                  onMouseMove={() => setTip({
                    x: (x0 + x1) / 2, y: M.top + 6,
                    text: wd.kind === "poison"
                      ? `wash-poison ${wd.start} → ${wd.end} · no loss-sales`
                      : `CLEAN harvest window ${wd.start} → ${wd.end}`,
                  })}
                  onMouseLeave={() => setTip(null)} />
          );
        })}

        {/* month axis ALONG THE TOP + gridlines down to the baseline */}
        {months.map((t, i) => (
          <g key={`m${i}`}>
            <line x1={x(t)} x2={x(t)} y1={M.top} y2={baseline} className="grid-line" />
            <text x={x(t)} y={M.top - 14} textAnchor="middle" className="axis-label vt-month">
              {monthLabel(t)}
            </text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={baseline} y2={baseline} className="grid-line" />

        {/* today cursor */}
        <line x1={x(today)} x2={x(today)} y1={M.top} y2={baseline} className="crosshair" />
        <text x={x(today)} y={M.top - 30} textAnchor="middle" className="ref-level-label">TODAY</text>

        {/* vest markers — lollipops sized by $, labeled with their date + amount */}
        {data.vests.map((v, i) => {
          const px = x(parse(v.date) as Date);
          const top = baseline - stem(v.units);
          const c = v.future ? VEST : VEST_PAST;
          const d = parse(v.date) as Date;
          return (
            <g key={`v${i}`}
               onMouseMove={() => setTip({
                 x: px, y: top - 8,
                 text: `${v.date} · ${v.units} units · $${fmtNum(v.value)} · ${v.future ? "upcoming" : "vested"}`,
               })}
               onMouseLeave={() => setTip(null)}>
              <line x1={px} x2={px} y1={baseline} y2={top} stroke={c} strokeWidth={2.5} />
              <circle cx={px} cy={top} r={6} fill={c} />
              <text x={px} y={top - 11} textAnchor="middle" className="vt-amount" fill={c}>
                ${fmtNum(v.value)}
              </text>
              <text x={px} y={baseline + 16} textAnchor="middle" className="axis-label vt-date">
                {d3.utcFormat("%b %-d")(d)}
              </text>
            </g>
          );
        })}

        {/* legend */}
        <g transform={`translate(${M.left}, ${H - 18})`}>
          {(() => {
            let cx = 0;
            return legend.map((item, i) => {
              const node = (
                <g key={i} transform={`translate(${cx},0)`}>
                  {item.kind === "dot"
                    ? <circle cx={6} cy={-4} r={5} fill={item.color} />
                    : <rect x={0} y={-9} width={12} height={11} rx={2} fill={item.color}
                            fillOpacity={0.3} stroke={item.color} strokeOpacity={0.8} />}
                  <text x={17} y={0} className="vt-legend-label">{item.label}</text>
                </g>
              );
              cx += 17 + item.label.length * 6.0 + 28;
              return node;
            });
          })()}
        </g>
      </svg>
      {tip && (
        <div className="viz-tip" style={{ left: Math.min(tip.x + 14, W - 230), top: tip.y }}>
          {tip.text}
        </div>
      )}
    </div>
  );
}
