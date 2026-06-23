// Interactive positioning scatter — the `scatter` type ({title, subtitle, xLabel,
// yLabel, xLog?, points:[{label, x, y, group, r?}]}). Two continuous axes (log-or-linear x),
// categorical color by group, dashed median crosshairs → quadrant guide, marker-aware label
// de-clutter. Hover a point for detail; click a legend group to toggle it. Mirrors viz/render.js
// renderScatter — keep the data contract in sync.

import { useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { COLORS, useMeasure } from "./common";
import { resolveRef, useNav, type Ref } from "../nav";

interface Pt { label: string; x: number; y: number; group?: string; r?: number; detail?: string; ref?: Ref }
interface ScatterData {
  title?: string; subtitle?: string; xLabel?: string; yLabel?: string; xLog?: boolean; points: Pt[];
}

const M = { top: 26, right: 26, bottom: 54, left: 54 };
type Box = { x1: number; x2: number; y1: number; y2: number };

export default function Scatter({ data }: { data: ScatterData }) {
  const nav = useNav();
  const { ref, width: W, height: H } = useMeasure(0.5); // fallback only; .scatter-plot flex-fills the stage
  const pts = useMemo(() => (data.points ?? []).filter((p) => p.x > 0 && p.y != null), [data]);
  const groups = useMemo(() => Array.from(new Set(pts.map((p) => p.group ?? "—"))), [pts]);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<{ px: number; py: number; p: Pt } | null>(null);

  // hover-bridge: a point with a `ref` shows an in-tooltip link; the tooltip is interactive
  // (pointer-events:auto when it has a link), so moving cursor dot→tooltip must NOT dismiss it.
  // A short grace timer on leave, cancelled when the tooltip itself is entered, bridges the gap.
  const clearTimer = useRef<number | null>(null);
  const cancelClear = () => { if (clearTimer.current) { clearTimeout(clearTimer.current); clearTimer.current = null; } };
  const showHover = (h: { px: number; py: number; p: Pt }) => { cancelClear(); setHover(h); };
  const scheduleClear = () => { cancelClear(); clearTimer.current = window.setTimeout(() => setHover(null), 140); };
  const openRef = (r: Ref) => void resolveRef(r).then(nav.navigate);

  const colorOf = (g: string) => COLORS[Math.max(0, groups.indexOf(g)) % COLORS.length];
  const shown = useMemo(() => pts.filter((p) => !off.has(p.group ?? "—")), [pts, off]);

  // short metric names for the tooltip rows: strip the parenthetical + take the segment after an em-dash
  // ("Valuation — EV / EBITDA (cheap → rich, log)" → "EV / EBITDA"; "Quality (agent-scored)" → "Quality")
  const shortLabel = (s: string | undefined, fallback: string) =>
    (s ?? fallback).replace(/\s*\(.*$/, "").split("—").pop()!.trim() || fallback;
  const shortX = shortLabel(data.xLabel, "X");
  const shortY = shortLabel(data.yLabel, "Quality");

  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const [xLo, xHi] = [Math.min(...xs), Math.max(...xs)];
  const [yLo, yHi] = [Math.min(...ys), Math.max(...ys)];
  const x = (data.xLog ? d3.scaleLog() : d3.scaleLinear())
    .domain(data.xLog ? [xLo * 0.8, xHi * 1.15] : [xLo, xHi]).range([M.left, W - M.right]);
  const yPad = (yHi - yLo) * 0.18 || 1;
  const y = d3.scaleLinear().domain([yLo - yPad, yHi + yPad]).nice().range([H - M.bottom, M.top]);

  const median = (a: number[]) => {
    const s = [...a].sort((m, n) => m - n); const h = Math.floor(s.length / 2);
    return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
  };
  const mx = median(xs), my = median(ys);

  const xticks = data.xLog
    ? [1, 1.5, 2, 3, 5, 8, 12, 20, 30, 50, 80].filter((v) => v >= x.domain()[0] && v <= x.domain()[1])
    : x.ticks(7);

  // greedy de-clutter — labels dodge ALL markers + already-placed labels; leader when nudged off.
  const labels = useMemo(() => {
    const placed: Box[] = shown.map((p) => {
      const r = p.r ?? 6; return { x1: x(p.x) - r, x2: x(p.x) + r, y1: y(p.y) - r, y2: y(p.y) + r };
    });
    const hits = (a: Box, b: Box) => !(a.x2 < b.x1 || a.x1 > b.x2 || a.y2 < b.y1 || a.y1 > b.y2);
    const out: { p: Pt; lx: number; ly: number; px: number; py: number; lead: boolean }[] = [];
    [...shown].map((p) => ({ p, px: x(p.x), py: y(p.y) })).sort((a, b) => a.px - b.px).forEach(({ p, px, py }) => {
      const r = p.r ?? 6, w = p.label.length * 6 + 4;
      const rightEdge = px > W - M.right - 52;
      const lx = px + (rightEdge ? -(r + 3) - w : r + 3);
      let ly = py + 3.5, tries = 0;
      let box: Box = { x1: lx, x2: lx + w, y1: ly - 9, y2: ly + 3 };
      while (placed.some((q) => hits(box, q)) && tries < 14) { ly += 12; box = { x1: lx, x2: lx + w, y1: ly - 9, y2: ly + 3 }; tries++; }
      placed.push(box);
      out.push({ p, lx, ly, px, py, lead: ly - (py + 3.5) > 6 });
    });
    return out;
  }, [shown, W, H]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (g: string) => setOff((o) => { const n = new Set(o); n.has(g) ? n.delete(g) : n.add(g); return n; });

  return (
    <div className="viz-canvas scatter-canvas">
      <div className="scatter-plot" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg scatter-chart">
        {/* y grid + ticks */}
        {y.ticks(6).map((t) => (
          <g key={`y${t}`}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={M.left - 8} y={y(t)} dy="0.35em" textAnchor="end" className="axis-label">{t}</text>
          </g>
        ))}
        {/* x grid + ticks */}
        {xticks.map((t) => (
          <g key={`x${t}`}>
            <line x1={x(t)} x2={x(t)} y1={M.top} y2={H - M.bottom} className="grid-line" />
            <text x={x(t)} y={H - M.bottom + 16} textAnchor="middle" className="axis-label">
              {data.xLog ? `${t}×` : t}
            </text>
          </g>
        ))}
        {/* median crosshairs → quadrant guide (reuse the dashed-amber ref-level idiom) */}
        <line x1={x(mx)} x2={x(mx)} y1={M.top} y2={H - M.bottom} className="ref-level" />
        <line x1={M.left} x2={W - M.right} y1={y(my)} y2={y(my)} className="ref-level" />
        <text x={M.left + 6} y={M.top + 12} className="scatter-corner">CHEAP + HIGH-QUALITY</text>
        {/* axis labels */}
        <text x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle" className="axis-label">
          {data.xLabel ?? "Valuation (cheap → rich)"}
        </text>
        <text transform={`translate(14,${(M.top + H - M.bottom) / 2}) rotate(-90)`} textAnchor="middle"
              className="axis-label">{data.yLabel ?? "Quality →"}</text>
        {/* leaders */}
        {labels.filter((l) => l.lead).map((l, i) => (
          <line key={`ld${i}`} x1={l.px} y1={l.py} x2={l.lx < l.px ? l.lx + (l.p.label.length * 6 + 4) : l.lx}
                y2={l.ly - 3} className="scatter-lead" />
        ))}
        {/* markers */}
        {shown.map((p) => {
          const r = p.r ?? 6, hot = hover?.p === p;
          return (
            <circle key={p.label} cx={x(p.x)} cy={y(p.y)} r={hot ? r + 2 : r}
                    fill={colorOf(p.group ?? "—")} fillOpacity={hot ? 1 : 0.85}
                    stroke="var(--graphite-0)" strokeWidth={1}
                    style={{ cursor: p.ref ? "pointer" : "default" }}
                    onMouseEnter={() => showHover({ px: x(p.x), py: y(p.y), p })}
                    onMouseLeave={scheduleClear}
                    onClick={() => { if (p.ref) openRef(p.ref); }} />
          );
        })}
        {/* labels */}
        {labels.map((l, i) => (
          <text key={`lb${i}`} x={l.lx} y={l.ly} textAnchor="start" className="scatter-pt-label">{l.p.label}</text>
        ))}
      </svg>

      {hover && (
        <div className={`viz-tip${hover.p.ref ? " has-link" : ""}`}
             style={{ left: Math.min(hover.px + 16, Math.max(8, W - 330)), top: Math.max(8, hover.py - 24) }}
             onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
          <div className="viz-tip-head">
            <span className="viz-tip-dot" style={{ background: colorOf(hover.p.group ?? "—") }} />
            <strong>{hover.p.label}</strong>
          </div>
          <div className="viz-tip-rows">
            <span className="k">{shortX}</span>
            <span className="v">{data.xLog ? `${hover.p.x}×` : hover.p.x}</span>
            <span className="k">{shortY}</span>
            <span className="v">{hover.p.y}</span>
            {hover.p.r != null && (<><span className="k">conviction</span><span className="v">{hover.p.r}</span></>)}
            {hover.p.group && (<><span className="k">group</span><span className="v">{hover.p.group}</span></>)}
          </div>
          {hover.p.detail && <div className="viz-tip-detail">{hover.p.detail}</div>}
          {hover.p.ref && (
            <button className="viz-tip-link" onClick={() => openRef(hover.p.ref!)}>
              open research report →
            </button>
          )}
        </div>
      )}
      </div>

      <ul className="viz-legend">
        {groups.map((g) => (
          <li key={g} className={off.has(g) ? "off" : ""} onClick={() => toggle(g)} style={{ cursor: "pointer" }}>
            <span className="swatch" style={{ background: colorOf(g) }} />{g}
          </li>
        ))}
      </ul>
      <p className="viz-hint">HOVER A POINT FOR DETAIL · CLICK A GROUP TO TOGGLE · UPPER-LEFT = CHEAP + HIGH-QUALITY</p>
    </div>
  );
}
