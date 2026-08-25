// NudgeCards — the Planning tab's proactive-nudge band (the Travel Watchman v2 core
// on its console surface). One card per engine nudge ("You could be in X {window} — because Z"),
// data contract = `hn travel nudge --json`: the deterministic windows × destinations scorer whose
// component breakdown ships IN the payload (explainable by construction — the UI never re-derives).
//
// Interaction mirrors ProjectionTiles (the component family this reuses): hover/focus a card → a
// portal-rendered item tooltip with the full component breakdown + in-window events + forecast +
// authored hook; click → DocPopup on the destination's corpus folder-note (`doc`, tracker-relative).
// Coarse pointers get the two-tap treatment (tap 1 = card, tap 2 = doc) — the touch rule:
// hover-born UI needs a touch story, not just a desktop screenshot.

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";

export interface NudgeComponentRow {
  name: string;
  points: number;
  fact: string;
}

export interface NudgeEvent {
  name: string;
  local_date: string;
  local_time?: string | null;
  venue?: string;
  url?: string;
}

export interface Nudge {
  slug: string;
  display: string;
  city?: string;
  window: { start: string; end: string; kind: string; anchor?: string; personal?: string };
  score: number;
  components: NudgeComponentRow[];
  reason: string;
  hook?: string;
  events: NudgeEvent[];
  weather_note?: string;
  doc?: string;
  line: string;
}

export interface NudgeData {
  nudges: Nudge[];
  skipped?: string[];
  note?: string;
}

interface TipPos {
  left: number;
  top: number;
}

function tipPosition(rect: DOMRect): TipPos {
  const TIP_W = 320;
  const pad = 10;
  let left = rect.right + pad;
  if (left + TIP_W > window.innerWidth - pad) left = Math.max(pad, rect.left - TIP_W - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - 340));
  return { left, top };
}

// "2026-10-02" → "Fri Oct 2" (the engine's `line` carries the label too; this keeps table rows terse)
function shortDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function windowLabel(w: Nudge["window"]): string {
  return `${shortDate(w.start)} → ${shortDate(w.end)}`;
}

const signed = (v: number) => `${v >= 0 ? "+" : ""}${Math.round(v * 10) / 10}`;

// Ticketing suffix cruft ("Game | Official Hotel Packages") trimmed for DISPLAY; TM pads the pipe
// with a non-breaking space on some listings (mirrors the engine's reason-line trim).
const shortEvent = (name: string) => name.replace(/\u00a0/g, " ").split(" | ")[0].trim();

// TM often lists the same game twice (with/without the package suffix) — display-dedupe on the
// trimmed name + date; the payload keeps the raw list (this is a view concern only).
function dedupeEvents(events: NudgeEvent[]): NudgeEvent[] {
  const seen = new Set<string>();
  return events.filter((e) => {
    const k = `${shortEvent(e.name)}|${e.local_date}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function NudgeTooltip({ n, pos }: { n: Nudge; pos: TipPos }) {
  return createPortal(
    <div className="item-tip nudge-tip" style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{n.display}</div>
      <div className="nudge-tip-meta">
        {windowLabel(n.window)} · {n.window.kind === "long-weekend" ? "3-day weekend" : "weekend"} ·
        score {n.score}
      </div>
      {n.window.anchor && <div className="item-tip-stat">🗓 {n.window.anchor}</div>}
      <table className="item-tip-grid">
        <tbody>
          {n.components.map((c, i) => (
            <tr key={`${c.name}-${i}`}>
              <td>{c.fact || c.name}</td>
              <td className={c.points >= 0 ? "pos" : "neg"}>{signed(c.points)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {dedupeEvents(n.events).slice(0, 4).map((e) => (
        <div className="item-tip-stat nudge-tip-event" key={`${e.name}-${e.local_date}`}>
          • {shortEvent(e.name)} <span className="item-tip-slot">{shortDate(e.local_date)}</span>
        </div>
      ))}
      {n.weather_note && <div className="item-tip-stat">☂ {n.weather_note}</div>}
      {n.hook && <div className="item-tip-flavor">"{n.hook}"</div>}
      <div className="item-tip-source">{n.doc ? "click for the destination doc" : n.slug}</div>
    </div>,
    document.body,
  );
}

function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}

export default function NudgeCards({ data }: { data: NudgeData }) {
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ slug: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const nudges = data?.nudges ?? [];

  if (nudges.length === 0) {
    return (
      <p className="nudges-empty">
        No nudges cleared the bar in this horizon — or every upcoming window is already booked.
        {data?.note ? ` (${data.note})` : ""}
      </p>
    );
  }

  return (
    <div className="nudges">
      <div className="nudges-grid">
        {nudges.map((n) => {
          const show = (el: HTMLElement) =>
            setHover({ slug: n.slug, pos: tipPosition(el.getBoundingClientRect()) });
          return (
            <button
              key={n.slug + n.window.start}
              className="nudge-card"
              onClick={(e) => {
                if (coarsePointer() && armed !== n.slug) {
                  setArmed(n.slug);
                  show(e.currentTarget);
                  return;
                }
                if (n.doc) setOpenDoc(n.doc);
              }}
              onMouseEnter={(e) => show(e.currentTarget)}
              onMouseLeave={() => setHover(null)}
              onFocus={(e) => show(e.currentTarget)}
              onBlur={() => {
                setHover(null);
                setArmed(null);
              }}
              aria-label={`${n.display} ${windowLabel(n.window)} — score ${n.score}; click to open the destination doc`}
            >
              <div className="nudge-head">
                <strong className="nudge-dest">{n.display}</strong>
                <span className="nudge-score">{n.score}</span>
              </div>
              <div className="nudge-window">
                {windowLabel(n.window)}
                {n.window.kind === "long-weekend" && <em className="nudge-lw">3-day</em>}
              </div>
              <div className="nudge-reason">{n.reason}</div>
            </button>
          );
        })}
      </div>
      {data?.note && <p className="nudges-note">{data.note}</p>}
      {hover &&
        (() => {
          const n = nudges.find((x) => x.slug === hover.slug);
          return n ? <NudgeTooltip n={n} pos={hover.pos} /> : null;
        })()}
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
