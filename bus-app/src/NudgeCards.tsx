// NudgeCards — the Planning tab's proactive-nudge band (the travel nudge engine
// on its console surface). One card per engine nudge ("You could be in X {window} — because Z"),
// data contract = `hn travel nudge --json`: the deterministic windows × destinations scorer whose
// component breakdown ships IN the payload (explainable by construction — the UI never re-derives).
//
// Interaction mirrors ProjectionTiles (the component family this reuses): hover/focus a card → a
// portal-rendered item tooltip with the full component breakdown + in-window events + forecast +
// authored hook; click → DocPopup on the destination's corpus folder-note (`doc`, tracker-relative).
// Coarse pointers get the two-tap treatment (tap 1 = card, tap 2 = doc) — the touch rule:
// hover-born UI needs a touch story, not just a desktop screenshot. The "why" toggle opens the same
// component breakdown inline (the touch-first read); the footer lists the windows the engine
// skipped (family time, trips already on the calendar) so its restraint is visible.

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";
import { Cover } from "./TripPicker";

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
  window: {
    start: string; end: string; kind: string; anchor?: string; personal?: string;
    pto_days?: number; title?: string;
  };
  score: number;
  lane?: string; // weekend | games | long-lead | horizon | family (absent on pre-lane payloads → weekend)
  components: NudgeComponentRow[];
  reason: string;
  hook?: string;
  events: NudgeEvent[];
  weather_note?: string;
  doc?: string;
  image?: string; // the destination note's first embedded image — the card thumb
  in_decision?: boolean; // also a candidate on the open trip decision's radar
  line: string;
}

export interface NudgeData {
  nudges: Nudge[];
  skipped?: string[];
  note?: string;
  lanes?: Record<string, number>;
  decision_trip?: string;
}

// The horizon axis: cards group by lane in this order, each under a small header.
// Utilitarian names on the shipped surface (the horizon lane shows as
// "Horizon — worth a look"). Unknown lanes fall to the end, never dropped.
const LANES: { key: string; title: string }[] = [
  { key: "weekend", title: "Weekends" },
  { key: "games", title: "Game weekends" },
  { key: "long-lead", title: "Long-lead · 3–12 months" },
  { key: "horizon", title: "Horizon · worth a look" },
  { key: "family", title: "Family windows" },
];
const laneOf = (n: Nudge) => n.lane || "weekend";

// The window's shape chip: "3-day" for a holiday weekend, "8 nights · 4 PTO" for a block,
// nothing for a plain weekend or a season (the season's title IS the window line).
function windowChip(w: Nudge["window"]): string {
  if (w.kind === "long-weekend") return "3-day";
  if (w.kind === "week" || w.kind === "family") {
    const nights = Math.round((new Date(`${w.end}T12:00:00`).getTime() - new Date(`${w.start}T12:00:00`).getTime()) / 86_400_000);
    if (!Number.isFinite(nights) || nights <= 0) return "";
    return w.pto_days != null ? `${nights} nights · ${w.pto_days} PTO` : `${nights} nights`;
  }
  return "";
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
  if (w.title) return w.title; // season windows carry their own label ("best Oct–Nov")
  return `${shortDate(w.start)} → ${shortDate(w.end)}`;
}

const signed = (v: number) => `${v >= 0 ? "+" : ""}${Math.round(v * 10) / 10}`;

// Ticketing suffix cruft ("Game | Official Hotel Packages") trimmed for DISPLAY; Ticketmaster pads the pipe
// with a non-breaking space on some listings (mirrors the engine's reason-line trim).
const shortEvent = (name: string) => name.replace(/\u00a0/g, " ").split(" | ")[0].trim();

// Ticketmaster often lists the same game twice (with/without the package suffix) — display-dedupe on the
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
        {laneOf(n)} · {windowLabel(n.window)}
        {windowChip(n.window) ? ` · ${windowChip(n.window)}` : n.window.kind === "weekend" ? " · weekend" : ""}
        {laneOf(n) !== "family" && ` · score ${n.score}`}
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

function WhyRows({ n }: { n: Nudge }) {
  return (
    <table className="nudge-why-rows">
      <tbody>
        {n.components.map((c, i) => (
          <tr key={`${c.name}-${i}`}>
            <td>{c.fact || c.name}</td>
            <td className={c.points >= 0 ? "pos" : "neg"}>{signed(c.points)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function NudgeCards({ data }: { data: NudgeData }) {
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ slug: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [why, setWhy] = useState<Set<string>>(new Set());
  const toggleWhy = (key: string) => setWhy((w) => {
    const next = new Set(w);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const nudges = data?.nudges ?? [];

  if (nudges.length === 0) {
    return (
      <p className="nudges-empty">
        No nudges cleared the bar in this horizon — or every upcoming window is already booked.
        {data?.note ? ` (${data.note})` : ""}
      </p>
    );
  }

  // group by lane in LANES order; any lane the payload invents rides at the end under its own name
  const known = new Set(LANES.map((l) => l.key));
  const extra = [...new Set(nudges.map(laneOf).filter((k) => !known.has(k)))].map((k) => ({ key: k, title: k }));
  const groups = [...LANES, ...extra]
    .map((l) => ({ ...l, items: nudges.filter((n) => laneOf(n) === l.key) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="nudges">
      <div className="nudges-scroll">
        {groups.map((g) => (
          <section className={`nudges-lane lane-${g.key}`} key={g.key} aria-label={g.title}>
            <h4 className="nudges-lane-title">{g.title}</h4>
            <div className="nudges-grid">
              {g.items.map((n) => {
                const lane = laneOf(n);
                const chip = windowChip(n.window);
                const key = n.slug + n.window.start;
                const open = why.has(key);
                const show = (el: HTMLElement) =>
                  setHover({ slug: n.slug, pos: tipPosition(el.getBoundingClientRect()) });
                const label =
                  lane === "family"
                    ? `${n.display} ${windowLabel(n.window)} — family window`
                    : `${n.display} ${windowLabel(n.window)} — score ${n.score}; click to open the destination doc`;
                return (
                  <div key={key} className={`nudge-card nudge-card-${lane}${n.image ? " has-thumb" : ""}`}>
                    <button
                      type="button"
                      className="nudge-main"
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
                      aria-label={label}
                    >
                      {n.image && <span className="nudge-thumb"><Cover path={n.image} /></span>}
                      <span className="nudge-head">
                        <strong className="nudge-dest">{n.display}</strong>
                        {n.in_decision && <em className="nudge-indecision">in decision</em>}
                        {lane !== "family" && <span className="nudge-score">{n.score}</span>}
                      </span>
                      <span className="nudge-window">
                        {windowLabel(n.window)}
                        {chip && <em className="nudge-lw">{chip}</em>}
                      </span>
                      <span className="nudge-reason">{n.reason}</span>
                    </button>
                    {lane !== "family" && n.components.length > 0 && (
                      <>
                        <button type="button" className="nudge-why" aria-expanded={open}
                                onClick={() => toggleWhy(key)}>
                          {open ? "hide why" : "why"}
                        </button>
                        {open && <WhyRows n={n} />}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      {data?.note && <p className="nudges-note">{data.note}</p>}
      {(data?.skipped?.length ?? 0) > 0 && (
        <p className="nudges-skipped">
          <span>skipped</span> {data!.skipped!.join(" · ")}
        </p>
      )}
      {hover &&
        (() => {
          const n = nudges.find((x) => x.slug === hover.slug);
          return n ? <NudgeTooltip n={n} pos={hover.pos} /> : null;
        })()}
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
