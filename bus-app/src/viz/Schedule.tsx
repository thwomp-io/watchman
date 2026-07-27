// Interactive schedule (v2) — the `schedule`/`schedule-bank` family
// ({title, start, end, dayStart, dayEnd, availability?, items?:[{date, start, end, label, lane,
// note?, links?:[{label,url}]}], markers?, bank?:[{group?, lane?, label, note?, links?}],
// bankTitle?}). Day columns × time rows with the static renderer's settled design ported whole:
// availability BANDS behind the grid, markers in the awareness RAIL, and — for `schedule-bank`
// data — the options-bank panel below the plot (grouped, lane-dotted chips). Hover an item or
// chip → the glance tip (note + source links, sticky via the hover-bridge); click → the full
// record with a source-links row. `links` is optional contract growth the static twin ignores.

import { useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import JsonView from "../JsonView";
import { useCatColors } from "./common";
import type { VizLink } from "./FoodBank";

interface ScheduleItem {
  date: string; start: string; end: string; label: string; lane?: string; note?: string;
  links?: VizLink[];
  [k: string]: unknown;
}
interface BankEntry {
  group?: string; lane?: string; label: string; note?: string; links?: VizLink[];
  [k: string]: unknown;
}
interface AvailSeg { until?: string; group: string }
interface Availability {
  groups?: { key: string; label: string; color: string }[];
  weekday?: AvailSeg[]; weekend?: AvailSeg[];
  dates?: Record<string, AvailSeg[]>; // per-date override beating the day-of-week rules
}
interface ScheduleData {
  title?: string; subtitle?: string;
  start: string; end: string; dayStart: string; dayEnd: string;
  availability?: Availability;
  items?: ScheduleItem[];
  markers?: { date: string; time?: string; label: string }[];
  markerLabel?: string;
  bank?: BankEntry[]; bankTitle?: string;
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

// selection is an item block OR a bank chip — the detail panel renders whichever
type Sel = { kind: "item"; it: ScheduleItem } | { kind: "bank"; b: BankEntry };
const RENDERED_ITEM = new Set(["date", "start", "end", "label", "lane", "note", "links"]);
const RENDERED_BANK = new Set(["group", "lane", "label", "note", "links"]);

export default function Schedule({ data }: { data: ScheduleData }) {
  const COLORS = useCatColors(); // theme-aware categorical set (re-renders on toggle)
  const [sel, setSel] = useState<Sel | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; head: string; sub: string; note?: string; links?: VizLink[]; color: string } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  // hover-bridge (the Scatter pattern): tips carry clickable source links → sticky
  const clearTimer = useRef<number | null>(null);
  const cancelClear = () => { if (clearTimer.current) { clearTimeout(clearTimer.current); clearTimer.current = null; } };
  const scheduleClear = () => { cancelClear(); clearTimer.current = window.setTimeout(() => setTip(null), 140); };
  const showTip = (e: React.MouseEvent, t: Omit<NonNullable<typeof tip>, "x" | "y">) => {
    cancelClear();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ x: Math.min(e.clientX - rect.left + 14, Math.max(8, rect.width - 330)), y: e.clientY - rect.top + 10, ...t });
  };

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
  const bank = data.bank ?? [];
  const lanes = Array.from(new Set([...items.map((i) => i.lane ?? "event"), ...bank.map((b) => b.lane ?? "event")]));
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

  // availability bands (the static renderer's design, ported): per day pick the per-date override
  // if one exists (a trip whose base flips on a specific date can't be expressed day-of-week),
  // else the weekday/weekend rule set; segments run prior-boundary → `until` (or dayEnd), tinted
  // by group color BEHIND the grid. Data-driven only — no availability block, no bands (the
  // default-pattern guess would paint wrong semantics on a trip).
  const avail = data.availability;
  const availColor = new Map((avail?.groups ?? []).map((g) => [g.key, g.color]));
  const bandsFor = (date: string): { y1: number; y2: number; color: string }[] => {
    if (!avail?.groups?.length) return [];
    const dow = new Date(`${date}T00:00:00`).getDay();
    const segs = avail.dates?.[date] ?? ((dow === 0 || dow === 6 ? avail.weekend : avail.weekday) ?? []);
    const out: { y1: number; y2: number; color: string }[] = [];
    let cur = t0;
    for (const s of segs) {
      const until = Math.min(mins(s.until) ?? t1, t1);
      if (until > cur) out.push({ y1: y(cur), y2: y(until), color: availColor.get(s.group) ?? "#888" });
      cur = until;
      if (cur >= t1) break;
    }
    return out;
  };

  const hourTicks: number[] = [];
  for (let t = Math.ceil(t0 / 60) * 60; t <= t1; t += 60) hourTicks.push(t);

  // bank chips group in first-seen order (the static renderer's grouping rule)
  const bankGroups = Array.from(new Set(bank.map((b) => b.group ?? "ideas")));

  const isSelItem = (it: ScheduleItem) => sel?.kind === "item" && sel.it === it;
  const isSelBank = (b: BankEntry) => sel?.kind === "bank" && sel.b === b;
  const residual = (o: Record<string, unknown>, rendered: Set<string>) =>
    Object.fromEntries(Object.entries(o).filter(([k]) => !rendered.has(k)));

  return (
    <div className="viz-canvas" ref={canvasRef}>
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" onMouseLeave={scheduleClear}>
        {days.map((d) => bandsFor(d).map((b, i) => (
          <rect key={`${d}b${i}`} x={colX(d)} y={b.y1} width={colW} height={b.y2 - b.y1}
                fill={b.color} fillOpacity={0.13} />
        )))}
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
            {days.map((d) => (markersByDay.get(d) ?? []).map((m, i) => {
              const prefix = m.time ? `${m.time} · ` : "";
              // the column budget covers prefix + label together — an uncounted time prefix
              // ran Thursday's marker into Friday's column (the eye-run)
              const cap = Math.max(6, Math.floor((colW - 16) / 5.6) - prefix.length);
              return (
                <text key={`${d}m${i}`} x={colX(d) + 8} y={M.top + 10 + i * ROW}
                      className="sched-marker-label">
                  {prefix}{m.label.slice(0, cap)}
                </text>
              );
            }))}
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
            <g key={i} className={`sched-item ${isSelItem(it) ? "selected" : ""}`}
               onClick={() => setSel(isSelItem(it) ? null : { kind: "item", it })}
               onMouseMove={(e) => showTip(e, {
                 head: it.label, sub: `${it.start}–${it.end} · ${it.lane ?? "event"}`,
                 note: it.note, links: it.links, color: c,
               })}
               onMouseLeave={scheduleClear}>
              <rect x={colX(it.date) + 3} y={top} width={colW - 6}
                    height={Math.max(10, bot - top)} rx={3}
                    fill={c} fillOpacity={isSelItem(it) ? 0.4 : 0.22}
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

      {avail?.groups?.length ? (
        <ul className="viz-legend sched-avail-legend">
          {avail.groups.map((g) => (
            <li key={g.key}><span className="swatch" style={{ background: g.color }} />{g.label}</li>
          ))}
        </ul>
      ) : null}

      {items.length === 0 && <p className="empty">NO SCHEDULED ITEMS — AVAILABILITY-ONLY SCHEDULE</p>}

      {bank.length > 0 && (
        <div className="sched-bank">
          <span className="section-label">{data.bankTitle ?? "Idea bank — swap-ins (not yet scheduled)"}</span>
          {bankGroups.map((g) => (
            <div key={g} className="sched-bank-group">
              <span className="sched-bank-cap">{g}</span>
              <div className="sched-bank-chips">
                {bank.filter((b) => (b.group ?? "ideas") === g).map((b, i) => (
                  <button key={`${g}${i}`} className={`sched-bank-chip ${isSelBank(b) ? "selected" : ""}`}
                          onClick={() => setSel(isSelBank(b) ? null : { kind: "bank", b })}
                          onMouseMove={(e) => showTip(e, {
                            head: b.label, sub: `${g} · ${b.lane ?? "event"}`,
                            note: b.note, links: b.links, color: color(b.lane ?? "event"),
                          })}
                          onMouseLeave={scheduleClear}>
                    <span className="viz-tip-dot" style={{ background: color(b.lane ?? "event"), width: 8, height: 8 }} />
                    {b.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tip && (
        <div className={`viz-tip${tip.links?.length ? " has-link" : ""}`}
             style={{ left: tip.x, top: tip.y }}
             onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
          <div className="viz-tip-head">
            <span className="viz-tip-dot" style={{ background: tip.color }} />
            <strong>{tip.head}</strong>
          </div>
          <div className="viz-tip-detail" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>{tip.sub}</div>
          {tip.note && <div className="viz-tip-detail">{tip.note}</div>}
          {(tip.links ?? []).slice(0, 2).map((l) => (
            <a key={l.url} className="viz-tip-link" href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a>
          ))}
        </div>
      )}

      {sel ? (
        <div className="viz-detail">
          {sel.kind === "item" ? (
            <>
              <span className="section-label">{sel.it.label} · {sel.it.date} {sel.it.start}–{sel.it.end}</span>
              <div className="viz-detail-rows">
                {sel.it.lane && (<><span className="k">lane</span><span className="v">{sel.it.lane}</span></>)}
                {sel.it.note && (<><span className="k">note</span><span className="v">{sel.it.note}</span></>)}
              </div>
              {(sel.it.links ?? []).length > 0 && (
                <div className="viz-detail-links">
                  {sel.it.links!.map((l) => (
                    <a key={l.url} href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a>
                  ))}
                </div>
              )}
              {Object.keys(residual(sel.it, RENDERED_ITEM)).length > 0 && <JsonView data={residual(sel.it, RENDERED_ITEM)} />}
            </>
          ) : (
            <>
              <span className="section-label">{sel.b.label} · {sel.b.group ?? "idea bank"}</span>
              <div className="viz-detail-rows">
                {sel.b.lane && (<><span className="k">lane</span><span className="v">{sel.b.lane}</span></>)}
                {sel.b.note && (<><span className="k">note</span><span className="v">{sel.b.note}</span></>)}
              </div>
              {(sel.b.links ?? []).length > 0 && (
                <div className="viz-detail-links">
                  {sel.b.links!.map((l) => (
                    <a key={l.url} href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a>
                  ))}
                </div>
              )}
              {Object.keys(residual(sel.b, RENDERED_BANK)).length > 0 && <JsonView data={residual(sel.b, RENDERED_BANK)} />}
            </>
          )}
        </div>
      ) : (
        items.length > 0 && <p className="viz-hint">HOVER FOR THE GLANCE TIP · CLICK AN ITEM OR BANK CHIP FOR ITS FULL RECORD</p>
      )}
    </div>
  );
}
