// Interactive calendar grid (the `calendar` type's twin) — real month grids over the
// emitter's window: day cells color-dotted by item kind (the static renderer's palette + the live
// segments), hover a day for the quick card, click for the FULL day popup (the BeadTree tile
// grammar: hover = glance, click = detail). Data: {title, subtitle, from, to,
// days:[{date, items:[{label, kind, segment, tier, venue, time, url, source}]}]} — `days`+`from`+
// `to` is the sniff signature on all three surfaces; the derived months[] key (ignored here)
// drives the static SVG renderer from the SAME JSON (the two-consumer contract).

import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

interface CalItem {
  label: string; kind: string; segment?: string; tier?: string;
  venue?: string; time?: string | null; url?: string; source?: string;
}
interface CalDay { date: string; items: CalItem[] }
interface CalData {
  title?: string; subtitle?: string; from: string; to: string; days: CalDay[];
  variant?: string;  // "big" = the one-month wall board (a fullscreen monitor's calendar)
}

// The static renderer's kind palette, extended for the live segments (music/arts/misc).
const KIND_COLOR: Record<string, string> = {
  holiday: "#34c759", sports: "#1f5bc4", personal: "#af52de", "mega-event": "#ff9500",
  music: "#e0447f", arts: "#0e9c9c", misc: "#8e8e93",
};
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

const iso = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

export default function Calendar({ data }: { data: CalData }) {
  const [hover, setHover] = useState<{ x: number; y: number; day: CalDay } | null>(null);
  const [popup, setPopup] = useState<CalDay | null>(null);
  // big-board month cursor (index into the month roster); -1 = uninitialized → today's month
  const [monthIdx, setMonthIdx] = useState(-1);

  const byDate = useMemo(
    () => new Map((data.days ?? []).map((d) => [d.date, d])),
    [data],
  );

  // Month roster from the window bounds — every month the window touches renders, empty ones
  // included (a quiet month IS information on a planning calendar).
  const months = useMemo(() => {
    const out: { label: string; year: number; month: number }[] = [];
    const from = new Date(`${data.from}T00:00`);
    const to = new Date(`${data.to}T00:00`);
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return out;
    const cur = new Date(from.getFullYear(), from.getMonth(), 1);
    while (cur <= to && out.length < 13) {
      out.push({
        label: cur.toLocaleString("en-US", { month: "long", year: "numeric" }),
        year: cur.getFullYear(),
        month: cur.getMonth(),
      });
      cur.setMonth(cur.getMonth() + 1);
    }
    return out;
  }, [data]);

  const today = iso(new Date());
  const inWindow = (d: string) => d >= data.from && d <= data.to;

  if (!months.length) {
    return <div className="viz-canvas"><p className="viz-hint">NO CALENDAR WINDOW IN THE DATA</p></div>;
  }

  // The full-day popup — shared by both variants (grid + big board).
  const dayPopup = popup && createPortal(
    // The DocPopup chrome (backdrop + bezel + head) with a day-detail body — item cards
    // carrying segment/tier chips, venue/time, and the TM "post ↗" purchase link.
    <div className="doc-popup-backdrop" onClick={() => setPopup(null)}>
      <div className="doc-popup bezel cal-popup" role="dialog" aria-label={popup.date}
           onClick={(ev) => ev.stopPropagation()}>
        <header className="doc-popup-head">
          <strong>
            {new Date(`${popup.date}T00:00`).toLocaleDateString("en-US",
              { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
          </strong>
          <button onClick={() => setPopup(null)}>✕ CLOSE</button>
        </header>
        <div className="cal-popup-body">
          {popup.items.map((it, i) => (
            <div key={i} className="cal-popup-item">
              <div className="cal-popup-item-head">
                <i style={{ background: KIND_COLOR[it.kind] ?? KIND_COLOR.misc }} />
                <strong>{it.label}</strong>
                {it.tier === "centerpiece" && <em className="cal-chip centerpiece">CENTERPIECE</em>}
              </div>
              <div className="cal-popup-item-meta">
                {[it.segment, it.venue, it.time].filter(Boolean).join(" · ") || "all day"}
                {it.source === "reference" && " · almanac"}
              </div>
              {it.url && (
                <a className="ext-link" href={it.url} target="_blank" rel="noreferrer">post ↗</a>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );

  // ── the BIG board (variant="big") — one month, wall-display cells with the labels IN the
  // cells (a 27" monitor's calendar), month nav + TODAY. Shares the popup/tooltip/palette. ──
  if (data.variant === "big") {
    const todayIdx = Math.max(0, months.findIndex(
      (m) => m.year === new Date().getFullYear() && m.month === new Date().getMonth(),
    ));
    const idx = monthIdx >= 0 ? monthIdx : todayIdx;
    const m = months[Math.min(idx, months.length - 1)];
    const first = new Date(m.year, m.month, 1);
    const daysIn = new Date(m.year, m.month + 1, 0).getDate();
    const lead = first.getDay();
    const cells: (string | null)[] = [
      ...Array.from({ length: lead }, () => null),
      ...Array.from({ length: daysIn }, (_, i) => iso(new Date(m.year, m.month, i + 1))),
    ];
    while (cells.length % 7) cells.push(null);
    return (
      <div className="viz-canvas calbig-canvas">
        <header className="calbig-head">
          <button className="calbig-nav" disabled={idx <= 0} onClick={() => setMonthIdx(idx - 1)}>◀</button>
          <h2 className="calbig-title">{m.label}</h2>
          <button className="calbig-nav" disabled={idx >= months.length - 1}
                  onClick={() => setMonthIdx(idx + 1)}>▶</button>
          <button className="calbig-today" onClick={() => setMonthIdx(todayIdx)}>TODAY</button>
          <span className="calbig-legend">
            {Object.entries(KIND_COLOR).map(([k, c]) => (
              <span key={k} className="cal-legend-item"><i style={{ background: c }} />{k}</span>
            ))}
          </span>
        </header>
        <div className="calbig-grid">
          {["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"].map((w) => (
            <span key={w} className="calbig-wd">{w}</span>
          ))}
          {cells.map((d, i) => {
            if (!d) return <span key={`b${i}`} className="calbig-cell blank" />;
            const day = byDate.get(d);
            const items = day?.items ?? [];
            const cls = [
              "calbig-cell",
              items.length ? "has-items" : "",
              d === today ? "today" : "",
              inWindow(d) ? "" : "outside",
            ].join(" ");
            return (
              <div key={d}
                   className={cls}
                   data-tier={items.some((it) => it.tier === "centerpiece") ? "centerpiece" : undefined}
                   onClick={() => day && setPopup(day)}>
                <span className="calbig-daynum">{Number(d.slice(8))}</span>
                <div className="calbig-pills">
                  {items.slice(0, 5).map((it, k) => (
                    <span key={k} className="calbig-pill"
                          style={{ borderLeftColor: KIND_COLOR[it.kind] ?? KIND_COLOR.misc }}>
                      {it.time ? `${it.time.slice(0, 5)} ` : ""}{it.label}
                    </span>
                  ))}
                  {items.length > 5 && <em className="calbig-more">+{items.length - 5} more</em>}
                </div>
              </div>
            );
          })}
        </div>
        {dayPopup}
      </div>
    );
  }

  return (
    <div className="viz-canvas cal-canvas">
      <div className="cal-months">
        {months.map((m) => {
          const first = new Date(m.year, m.month, 1);
          const daysIn = new Date(m.year, m.month + 1, 0).getDate();
          const lead = first.getDay();
          const cells: (string | null)[] = [
            ...Array.from({ length: lead }, () => null),
            ...Array.from({ length: daysIn }, (_, i) => iso(new Date(m.year, m.month, i + 1))),
          ];
          return (
            <section key={m.label} className="cal-month bezel">
              <h3 className="cal-month-name">{m.label}</h3>
              <div className="cal-grid">
                {WEEKDAYS.map((w, i) => <span key={`h${i}`} className="cal-wd">{w}</span>)}
                {cells.map((d, i) => {
                  if (!d) return <span key={`b${i}`} className="cal-cell blank" />;
                  const day = byDate.get(d);
                  const n = day?.items.length ?? 0;
                  const kinds = [...new Set((day?.items ?? []).map((it) => it.kind))];
                  const cls = [
                    "cal-cell",
                    n ? "has-items" : "",
                    d === today ? "today" : "",
                    inWindow(d) ? "" : "outside",
                  ].join(" ");
                  return (
                    <button
                      key={d}
                      className={cls}
                      // centerpiece days get the loud ring — the trip-around-able signal
                      data-tier={day?.items.some((it) => it.tier === "centerpiece") ? "centerpiece" : undefined}
                      onMouseEnter={(ev) => {
                        if (!day) return;
                        const r = (ev.target as HTMLElement).getBoundingClientRect();
                        setHover({ x: r.left + r.width / 2, y: r.bottom, day });
                      }}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => day && setPopup(day)}
                    >
                      <span className="cal-daynum">{Number(d.slice(8))}</span>
                      {n > 0 && (
                        <span className="cal-dots">
                          {kinds.slice(0, 3).map((k) => (
                            <i key={k} style={{ background: KIND_COLOR[k] ?? KIND_COLOR.misc }} />
                          ))}
                          {n > 3 && <em className="cal-more">+{n - 3}</em>}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      <div className="cal-legend">
        {Object.entries(KIND_COLOR).map(([k, c]) => (
          <span key={k} className="cal-legend-item"><i style={{ background: c }} />{k}</span>
        ))}
      </div>
      <p className="viz-hint">HOVER A DAY FOR THE GLANCE CARD · CLICK = THE FULL DAY · RING = CENTERPIECE</p>

      {hover && (
        <div
          className="viz-tip cal-tip"
          style={{
            left: Math.max(8, Math.min(hover.x - 130, window.innerWidth - 280)),
            top: Math.min(hover.y + 6, window.innerHeight - 200),
          }}
        >
          <div className="viz-tip-head"><strong>{hover.day.date}</strong></div>
          {hover.day.items.slice(0, 6).map((it, i) => (
            <div key={i} className="cal-tip-row">
              <i style={{ background: KIND_COLOR[it.kind] ?? KIND_COLOR.misc }} />
              <span className="cal-tip-label">{it.label}</span>
              {it.time && <span className="cal-tip-time">{it.time}</span>}
            </div>
          ))}
          {hover.day.items.length > 6 && (
            <div className="viz-tip-detail">+{hover.day.items.length - 6} more — click for all</div>
          )}
        </div>
      )}

      {dayPopup}
    </div>
  );
}
