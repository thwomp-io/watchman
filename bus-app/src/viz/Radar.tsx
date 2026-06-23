// Interactive radar — the `compare` type ({title, subtitle, max, axes[],
// candidates:[{label, values[]}]}). Legend click toggles a candidate's polygon; hover a vertex
// for its exact score.

import { useState } from "react";
import { COLORS, type Tip } from "./common";

interface Candidate { label: string; values: number[] }
interface RadarData { title?: string; subtitle?: string; max?: number; axes: string[]; candidates: Candidate[] }

const R = 165, CX = 0, CY = 0, PAD = 110;

export default function Radar({ data }: { data: RadarData }) {
  const [off, setOff] = useState<Set<string>>(new Set());
  const [tip, setTip] = useState<Tip | null>(null);
  const max = data.max ?? Math.max(...data.candidates.flatMap((c) => c.values), 1);
  const n = data.axes.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, v: number): [number, number] => [
    CX + Math.cos(angle(i)) * (v / max) * R,
    CY + Math.sin(angle(i)) * (v / max) * R,
  ];
  const W = (R + PAD) * 2, H = (R + 60) * 2;

  return (
    <div className="viz-canvas">
      <div className="radar-stage">
        <svg viewBox={`${-W / 2} ${-H / 2} ${W} ${H}`} className="viz-svg"
             onMouseLeave={() => setTip(null)}>
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <polygon key={f} className="radar-ring"
                     points={data.axes.map((_, i) => pt(i, max * f).join(",")).join(" ")} />
          ))}
          {data.axes.map((a, i) => {
            const [ax, ay] = pt(i, max * 1.16);
            return (
              <g key={a}>
                <line x1={CX} y1={CY} x2={pt(i, max)[0]} y2={pt(i, max)[1]} className="radar-ring" />
                <text x={ax} y={ay} textAnchor="middle" dy="0.35em" className="radar-axis">{a}</text>
              </g>
            );
          })}
          {data.candidates.map((c, ci) => off.has(c.label) ? null : (
            <g key={c.label}>
              <polygon points={c.values.map((v, i) => pt(i, v).join(",")).join(" ")}
                       fill={COLORS[ci % COLORS.length]} fillOpacity={0.12}
                       stroke={COLORS[ci % COLORS.length]} strokeWidth={1.8} />
              {c.values.map((v, i) => {
                const [vx, vy] = pt(i, v);
                return (
                  <circle key={i} cx={vx} cy={vy} r={4} fill={COLORS[ci % COLORS.length]}
                          className="radar-dot"
                          onMouseMove={(e) => {
                            const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                            const sx = ((e.clientX - r.left) / r.width) * W;
                            const sy = ((e.clientY - r.top) / r.height) * H;
                            setTip({ x: sx, y: sy, text: `${c.label} · ${data.axes[i]} · ${v}/${max}` });
                          }} />
                );
              })}
            </g>
          ))}
        </svg>
        {tip && <div className="viz-tip" style={{ left: tip.x + 10, top: tip.y + 4 }}>{tip.text}</div>}
      </div>
      <ul className="viz-legend radar-legend">
        {data.candidates.map((c, ci) => (
          <li key={c.label} className={off.has(c.label) ? "off" : ""}
              onClick={() => setOff((o) => {
                const nn = new Set(o);
                if (nn.has(c.label)) nn.delete(c.label); else nn.add(c.label);
                return nn;
              })}>
            <span className="swatch" style={{ background: COLORS[ci % COLORS.length] }} />
            {c.label}
          </li>
        ))}
      </ul>
      <p className="viz-hint">CLICK A LEGEND ITEM TO TOGGLE ITS TRACE · HOVER A VERTEX FOR ITS SCORE</p>
    </div>
  );
}
