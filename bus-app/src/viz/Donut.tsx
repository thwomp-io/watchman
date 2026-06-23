// Interactive donut bank — the `pies` type ({title, subtitle, pies:[{label,
// caption?, slices:[{label, value}]}]}). Legend click toggles a slice out of the layout
// (arcs recompute); hover → value + recomputed share.

import { useState } from "react";
import * as d3 from "d3";
import { COLORS, fmtNum, type Tip } from "./common";

interface Slice { label: string; value: number; color?: string }
interface Pie { label: string; caption?: string; slices: Slice[] }
interface PiesData { title?: string; subtitle?: string; pies: Pie[] }

function OneDonut({ pie }: { pie: Pie }) {
  const [off, setOff] = useState<Set<string>>(new Set());
  const [tip, setTip] = useState<Tip | null>(null);

  const R = 130, W = R * 2 + 20, H = R * 2 + 20;
  const color = d3.scaleOrdinal<string, string>()
    .domain(pie.slices.map((s) => s.label)).range(COLORS);
  const live = pie.slices.filter((s) => !off.has(s.label));
  const total = d3.sum(live, (s) => s.value);
  const arcs = d3.pie<Slice>().value((s) => s.value).sort(null)(live);
  const arc = d3.arc<d3.PieArcDatum<Slice>>().innerRadius(R * 0.62).outerRadius(R);
  const arcHover = d3.arc<d3.PieArcDatum<Slice>>().innerRadius(R * 0.62).outerRadius(R + 5);
  const [hover, setHover] = useState<string | null>(null);

  return (
    <div className="donut">
      <div className="donut-stage">
        <svg viewBox={`${-W / 2} ${-H / 2} ${W} ${H}`} className="viz-svg"
             onMouseLeave={() => { setTip(null); setHover(null); }}>
          {arcs.map((a) => (
            <path key={a.data.label}
                  d={(hover === a.data.label ? arcHover : arc)(a) ?? ""}
                  fill={a.data.color ?? color(a.data.label)}
                  fillOpacity={0.3} stroke={a.data.color ?? color(a.data.label)}
                  strokeOpacity={0.8}
                  onMouseMove={(e) => {
                    const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                    setHover(a.data.label);
                    setTip({
                      x: e.clientX - r.left, y: e.clientY - r.top,
                      text: `${a.data.label} · ${fmtNum(a.data.value)} · ${((a.data.value / total) * 100).toFixed(1)}%`,
                    });
                  }} />
          ))}
          <text className="donut-center" textAnchor="middle" dy="0.35em">{pie.label}</text>
        </svg>
        {tip && <div className="viz-tip" style={{ left: tip.x + 14, top: tip.y + 10 }}>{tip.text}</div>}
      </div>
      <ul className="viz-legend">
        {pie.slices.map((s) => (
          <li key={s.label} className={off.has(s.label) ? "off" : ""}
              onClick={() => setOff((o) => {
                const n = new Set(o);
                if (n.has(s.label)) n.delete(s.label); else n.add(s.label);
                return n;
              })}>
            <span className="swatch" style={{ background: s.color ?? color(s.label) }} />
            {s.label}
            <span className="legend-val">{fmtNum(s.value)}</span>
          </li>
        ))}
      </ul>
      {pie.caption && <p className="donut-caption">{pie.caption}</p>}
    </div>
  );
}

export default function Donuts({ data }: { data: PiesData }) {
  return (
    <div className="viz-canvas donut-bank">
      {data.pies.map((p) => <OneDonut key={p.label} pie={p} />)}
      <p className="viz-hint donut-hint">CLICK A LEGEND ITEM TO TOGGLE IT OUT OF THE LAYOUT</p>
    </div>
  );
}
