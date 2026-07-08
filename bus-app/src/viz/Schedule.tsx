// Interactive schedule — the `schedule`/`schedule-bank` family ({title, start,
// end, dayStart, dayEnd, availability?, items?:[{date, start, end, label, lane, note?}],
// markers?}). Day columns × time rows; click an item block → its full record. Availability-only
// schedules (no items) render the empty grid honestly.

import { useMemo, useState } from "react";
import * as d3 from "d3";
import JsonView from "../JsonView";
import { useCatColors, type Tip } from "./common";

interface ScheduleItem {
  date: string; start: string; end: string; label: string; lane?: string; note?: string;
  [k: string]: unknown;
}
interface ScheduleData {
  title?: string; subtitle?: string;
  start: string; end: string; dayStart: string; dayEnd: string;
  items?: ScheduleItem[];
  markers?: { date: string; time?: string; label: string }[];
  markerLabel?: string;
}

// "10:45" | "5:00p" | "11a" → minutes, or null when unparseable (callers skip nulls — a
// label-only marker with no time at all once crashed this)
const mins = (raw: string | undefined): number | null => {
  if (!raw) return null;
  const m = /^(\d{1,2})(?::(\d{2}))?\s*([ap])?m?$/i.exec(raw.trim());
  if (!m) return null;
  let h = Number(m[1]);
  const suffix = m[3]?.toLowerCase();
  if (suffix === "p" && h < 12) h += 12;
  if (suffix === "a" && h === 12) h = 0;
  return h * 60 + Number(m[2] ?? 0);
};

export default function Schedule({ data }: { data: ScheduleData }) {
  const COLORS = useCatColors(); // theme-aware categorical set (re-renders on toggle)
  const [sel, setSel] = useState<ScheduleItem | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);

  const days = useMemo(() => {
    const out: string[] = [];
    const d = new Date(`${data.start}T00:00:00`);
    const end = new Date(`${data.end}T00:00:00`);
    while (d <= end && out.length < 14) {
      out.push(d.toISOString().slice(0, 10));
      d.setDate(d.getDate() + 1);
    }
    return out;
  }, [data]);

  const items = data.items ?? [];
  const lanes = Array.from(new Set(items.map((i) => i.lane ?? "event")));
  const color = d3.scaleOrdinal<string, string>().domain(lanes).range(COLORS);

  // markers live in an AWARENESS RAIL between the day header and the plot (the static
  // renderer's design, ported) — never as lines across the committed item blocks
  const markersByDay = new Map<string, { time?: string; label: string }[]>();
  for (const m of data.markers ?? []) {
    if (!days.includes(m.date)) continue;
    const arr = markersByDay.get(m.date) ?? [];
    arr.push(m);
    markersByDay.set(m.date, arr);
  }
  for (const arr of markersByDay.values()) {
    arr.sort((a, b) => (mins(a.time) ?? 0) - (mins(b.time) ?? 0));
  }
  const maxMarkers = Math.max(0, ...Array.from(markersByDay.values(), (a) => a.length));
  const ROW = 15;
  const railH = maxMarkers ? maxMarkers * ROW + 14 : 0;

  const W = 860, plotH = 380, M = { top: 34, left: 52, right: 10, bottom: 8 };
  const H = M.top + railH + plotH + M.bottom;
  const t0 = mins(data.dayStart) ?? 8 * 60, t1raw = mins(data.dayEnd) ?? 22 * 60;
  const t1 = t1raw > t0 ? t1raw : t0 + 60;
  const y = (t: number) => M.top + railH + ((t - t0) / (t1 - t0)) * plotH;
  const colW = (W - M.left - M.right) / days.length;
  const colX = (date: string) => M.left + days.indexOf(date) * colW;

  const hourTicks: number[] = [];
  for (let t = Math.ceil(t0 / 60) * 60; t <= t1; t += 60) hourTicks.push(t);

  return (
    <div className="viz-canvas">
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" onMouseLeave={() => setTip(null)}>
        {hourTicks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={M.left - 8} y={y(t)} dy="0.35em" textAnchor="end" className="axis-label">
              {`${Math.floor(t / 60)}`.padStart(2, "0")}:00
            </text>
          </g>
        ))}
        {days.map((d, i) => (
          <g key={d}>
            <line x1={M.left + i * colW} x2={M.left + i * colW} y1={M.top - 4} y2={H - M.bottom}
                  className="grid-line" />
            <text x={M.left + i * colW + colW / 2} y={18} textAnchor="middle" className="sched-day">
              {new Date(`${d}T00:00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}
            </text>
          </g>
        ))}
        {railH > 0 && (
          <g>
            <text x={M.left - 8} y={M.top + 10} textAnchor="end" className="sched-rail-cap">
              {(data.markerLabel ?? "MARKERS").toUpperCase().slice(0, 9)}
            </text>
            {days.map((d) => (markersByDay.get(d) ?? []).map((m, i) => (
              <text key={`${d}m${i}`} x={colX(d) + 8} y={M.top + 10 + i * ROW}
                    className="sched-marker-label">
                {m.time ? `${m.time} · ` : ""}{m.label.slice(0, Math.floor((colW - 16) / 5.6))}
              </text>
            )))}
            <line x1={M.left} x2={W - M.right} y1={M.top + railH - 6} y2={M.top + railH - 6}
                  className="sched-rail-rule" />
          </g>
        )}
        {items.map((it, i) => {
          const ts = mins(it.start), te = mins(it.end);
          if (!days.includes(it.date) || ts === null || te === null) return null;
          const top = y(ts), bot = y(te);
          const c = color(it.lane ?? "event");
          return (
            <g key={i} className={`sched-item ${sel === it ? "selected" : ""}`}
               onClick={() => setSel(sel === it ? null : it)}
               onMouseMove={(e) => {
                 const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                 setTip({
                   x: ((e.clientX - r.left) / r.width) * W,
                   y: ((e.clientY - r.top) / r.height) * H,
                   text: `${it.label} · ${it.start}–${it.end}`,
                 });
               }}>
              <rect x={colX(it.date) + 3} y={top} width={colW - 6}
                    height={Math.max(10, bot - top)} rx={3}
                    fill={c} fillOpacity={sel === it ? 0.4 : 0.22}
                    stroke={c} strokeOpacity={0.7} />
              {bot - top > 16 && (
                <text x={colX(it.date) + 9} y={top + 13} className="sched-label">
                  {it.label.length > Math.floor(colW / 7) ? `${it.label.slice(0, Math.floor(colW / 7) - 1)}…` : it.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {tip && <div className="viz-tip" style={{ left: tip.x + 12, top: tip.y + 8 }}>{tip.text}</div>}
      {items.length === 0 && <p className="empty">NO SCHEDULED ITEMS — AVAILABILITY-ONLY SCHEDULE</p>}
      {sel ? (
        <div className="viz-detail">
          <span className="section-label">{sel.label} · {sel.date} {sel.start}–{sel.end}</span>
          <JsonView data={Object.fromEntries(Object.entries(sel).filter(([k]) => k !== "label"))} />
        </div>
      ) : (
        items.length > 0 && <p className="viz-hint">CLICK AN ITEM BLOCK FOR ITS FULL RECORD</p>
      )}
    </div>
  );
}
