// ScheduleBoard — the Visits tab's interactive timeline. Data = `hn travel schedule --json`
// (VisitSchedule): one column per date with the planned items, pinned markers, who is around, and
// the home forecast; the visit's bank rides along for the unscheduled chips underneath.
//
// Desktop: a time grid (dayStart → dayEnd, the evening shaded), items as lane-colored blocks at
// their times (✎ penciled = dashed), markers pinned at their time, a weather chip + availability
// bar per column. Phone: the same days as stacked cards (time + label lists) — CSS picks one.
// Hover an item or a chip → the bank quick card; click → the bank detail popup, the same two
// components the tiles use. An item that names no bank entry gets a small item card instead.
// Read-only: schedule.json is the source of truth.

import { useEffect, useMemo, useState, type FocusEvent, type KeyboardEvent, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { BankPopup, driveText, QuickCard } from "./BankTiles";
import DocPopup from "./DocPopup";
import type { BankItem, ScheduleDay, ScheduleItem, VisitSchedule } from "./generated/contracts";
import { coarsePointer, tipPosition, type TipPos } from "./tips";

// Okabe–Ito (color-blind safe), yellow dropped for contrast on the paper theme.
const LANE_PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#D55E00", "#CC79A7", "#8C7AE6", "#999999"];
const PX_PER_HOUR = 30;
// The stacked time-over-title block needs this much height; a shorter block (under ~68 minutes at
// 30px/hour) reads as one line, time and title inline, instead of clipping the title.
export const TWO_LINE_MIN_PX = 34;

/** How many title lines a stacked block of `h` px holds: the block's padding + border (6px) and
 * the time row (13px) come off the top, each title line is 11px × 1.25. A title longer than that
 * ends in an ellipsis on its last visible line instead of being sliced by the block's edge. */
export function titleLines(h: number): number {
  return Math.max(1, Math.floor((h - 6 - 13) / 13.75));
}

export function minutes(t: string | null | undefined): number | null {
  if (!t) return null;
  const m = /^(\d{1,2}):(\d{2})\s*([ap])?m?$/i.exec(t.trim());
  if (!m) return null;
  let h = Number(m[1]);
  const ap = m[3]?.toLowerCase();
  if (ap === "p" && h < 12) h += 12;
  if (ap === "a" && h === 12) h = 0;
  return h * 60 + Number(m[2]);
}

export function laneColors(data: VisitSchedule): Record<string, string> {
  const order: string[] = [];
  for (const it of data.bank.items) if (it.kind === "outing" && it.lane && !order.includes(it.lane)) order.push(it.lane);
  for (const d of data.days) for (const it of d.items) if (it.lane && !order.includes(it.lane)) order.push(it.lane);
  return Object.fromEntries(order.map((l, i) => [l, LANE_PALETTE[i % LANE_PALETTE.length]]));
}

export const DRIVE_BANDS: { key: string; label: string; max: number | null }[] = [
  { key: "any", label: "any drive", max: null },
  { key: "15", label: "≤15m", max: 15 },
  { key: "30", label: "≤30m", max: 30 },
  { key: "60", label: "≤1h", max: 60 },
];

export function chipItems(
  bank: BankItem[], kind: BankItem["kind"], group: string | null, indoorOnly: boolean, band: string,
): BankItem[] {
  const max = DRIVE_BANDS.find((b) => b.key === band)?.max ?? null;
  return bank.filter((b) =>
    b.kind === kind && (group === null || b.group === group) && !(indoorOnly && b.indoor === false)
    && (max === null || (b.drive_min != null && b.drive_min <= max)));
}

function fmtDay(d: ScheduleDay): string {
  const [, m, dd] = d.date.split("-");
  return `${d.weekday} ${Number(m)}/${Number(dd)}`;
}

function WeatherChip({ d, unit }: { d: ScheduleDay; unit: string }) {
  const w = d.weather;
  if (!w) return <span className="vsched-wx none" title="no forecast for this day">—</span>;
  const temps = w.high != null && w.low != null ? `${Math.round(w.high)}°/${Math.round(w.low)}°` : "";
  const rain = w.precip_sum ? `${w.precip_sum.toFixed(2)}${unit === "inch" ? '"' : "mm"}` : "";
  const title = [w.condition, temps, w.precip_prob != null ? `${w.precip_prob}% chance` : "",
    w.precip_hours ? `${w.precip_hours.toFixed(0)}h of precip` : "", rain].filter(Boolean).join(" · ");
  return (
    <span className={`vsched-wx${w.wet ? " wet" : ""}`} title={title}>
      {w.wet && <b>☂</b>} {temps} {w.condition}{rain && ` · ${rain}`}
    </span>
  );
}

function ItemCard({ it, pos }: { it: ScheduleItem; pos: TipPos }) {
  return createPortal(
    <div className="item-tip bank-tip" style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{it.label}</div>
      {it.start && <div className="item-tip-stat">{it.start}{it.end ? `–${it.end}` : ""}</div>}
      {it.note && <div className="item-tip-flavor">{it.note}</div>}
      {it.lane && <div className="item-tip-source">{it.lane}</div>}
    </div>,
    document.body,
  );
}

export default function ScheduleBoard({ data }: { data: VisitSchedule }) {
  const [hover, setHover] = useState<{ key: string; pos: TipPos } | null>(null);
  const [openBank, setOpenBank] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<ScheduleItem | null>(null);
  const [visitDoc, setVisitDoc] = useState(false);
  const [kind, setKind] = useState<BankItem["kind"]>("outing");
  const [group, setGroup] = useState<string | null>(null);
  const [indoorOnly, setIndoorOnly] = useState(false);
  const [band, setBand] = useState("any");
  const colors = useMemo(() => (data?.days?.length ? laneColors(data) : {}), [data]);
  useEffect(() => {
    if (!openItem) return;
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") setOpenItem(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openItem]);

  if (!data?.days?.length) {
    return <p className="projections-empty">{data?.title || "No live visit carries a schedule yet"}.</p>;
  }

  const bankById = new Map(data.bank.items.map((b) => [b.id, b]));
  const startMin = minutes(data.day_start) ?? 480;
  const endMin = Math.max(startMin + 60, minutes(data.day_end) ?? 1320);
  const eveMin = minutes(data.evening_start);
  const span = endMin - startMin;
  const height = (span / 60) * PX_PER_HOUR;
  const y = (m: number) => ((Math.min(Math.max(m, startMin), endMin) - startMin) / span) * height;
  const hours: number[] = [];
  for (let m = Math.ceil(startMin / 120) * 120; m <= endMin; m += 120) hours.push(m);
  const groupColor = new Map(data.availability_groups.map((g) => [g.key, g.color ?? "var(--ink-faint)"]));
  const groupLabel = new Map(data.availability_groups.map((g) => [g.key, g.label]));
  const catColor = new Map(data.marker_cats.map((c) => [c.key, c.color ?? "var(--ink-dim)"]));
  const scheduledBank = new Set(data.days.flatMap((d) => d.items.map((i) => i.bank_id)).filter(Boolean));

  const showTip = (key: string, el: HTMLElement) => {
    if (!coarsePointer()) setHover({ key, pos: tipPosition(el.getBoundingClientRect(), 320, 220) });
  };
  const openFor = (it: ScheduleItem) => {
    setHover(null);
    if (it.bank_id && bankById.has(it.bank_id)) setOpenBank(it.bank_id);
    else setOpenItem(it);
  };
  const itemHandlers = (it: ScheduleItem, key: string) => ({
    role: "button" as const,
    tabIndex: 0,
    onClick: () => openFor(it),
    onKeyDown: (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFor(it); } },
    onMouseEnter: (e: MouseEvent<HTMLElement>) => showTip(key, e.currentTarget),
    onMouseLeave: () => setHover(null),
    onFocus: (e: FocusEvent<HTMLElement>) => showTip(key, e.currentTarget),
    onBlur: () => setHover(null),
  });

  const groups = data.bank.groups.filter((g) => g.kind === kind);
  const chips = chipItems(data.bank.items, kind, group, indoorOnly, band);
  const hoveredKey = hover?.key ?? "";
  const hoveredBank = hoveredKey.startsWith("bank:") ? bankById.get(hoveredKey.slice(5)) : undefined;
  const hoveredItem = hoveredKey.startsWith("item:")
    ? data.days.flatMap((d) => d.items.map((it, i) => ({ k: `item:${d.date}:${i}`, it }))).find((x) => x.k === hoveredKey)?.it
    : undefined;
  const hoveredItemBank = hoveredItem?.bank_id ? bankById.get(hoveredItem.bank_id) : undefined;

  return (
    <div className="projections sched">
      <div className="vsched-head">
        <a className="bank-visit" href="#" onClick={(e) => { e.preventDefault(); setVisitDoc(true); }}>{data.title} ↗</a>
        <span className="vsched-legend">
          {data.availability_groups.map((g) => (
            <span key={g.key}><i style={{ background: g.color ?? undefined }} />{g.label}</span>
          ))}
          {data.marker_cats.map((c) => (
            <span key={c.key}><i className="diamond" style={{ background: c.color ?? undefined }} />{c.label}</span>
          ))}
        </span>
        {data.weather_note && <span className="vsched-note">{data.weather_note}</span>}
      </div>
      <div className="projections-body">
        {/* desktop: the time grid */}
        <div className="vsched-grid" style={{ gridTemplateColumns: `44px repeat(${data.days.length}, minmax(118px, 1fr))` }}>
          <div className="vsched-corner" />
          {data.days.map((d) => (
            <div key={d.date} className={`vsched-dayhead${d.weather?.wet ? " wet" : ""}`}>
              <strong>{fmtDay(d)}</strong>
              <WeatherChip d={d} unit={data.precip_unit} />
              <span className="vsched-avail">
                {d.availability.map((g) => (
                  <i key={g} style={{ background: groupColor.get(g) }} title={groupLabel.get(g) ?? g} />
                ))}
              </span>
              {/* markers live in the header (a timed one also ticks the grid at its time), so a
                  pinned label never sits on top of an item block */}
              {d.markers.map((m, i) => (
                <span key={i} className="vsched-allday" style={{ borderColor: catColor.get(m.cat ?? "") }}
                      title={`${m.time ? `${m.time} ` : ""}${m.label}`}>
                  {m.time && <b>{m.time}</b>}{m.label}
                </span>
              ))}
              {d.items.filter((it) => !it.start).map((it, i) => (
                <span key={i} className={`vsched-allday item${it.penciled ? " penciled" : ""}`}
                      style={{ borderColor: colors[it.lane ?? ""] }}
                      {...itemHandlers(it, `item:${d.date}:${d.items.indexOf(it)}`)}>{it.label}</span>
              ))}
            </div>
          ))}
          <div className="vsched-axis" style={{ height }}>
            {hours.map((m) => (
              <span key={m} style={{ top: y(m) }}>{`${String(Math.floor(m / 60)).padStart(2, "0")}:00`}</span>
            ))}
          </div>
          {data.days.map((d) => (
            <div key={d.date} className="vsched-col" style={{ height }}>
              {hours.map((m) => <span key={m} className="vsched-hline" style={{ top: y(m) }} />)}
              {eveMin != null && eveMin < endMin && (
                <span className="vsched-evening" style={{ top: y(eveMin), height: height - y(eveMin) }} />
              )}
              {d.items.map((it, i) => {
                const s = minutes(it.start);
                if (s == null) return null;
                const e = minutes(it.end) ?? s + 60;
                const top = y(s);
                const h = Math.max(22, y(e) - top);
                const compact = h < TWO_LINE_MIN_PX;
                return (
                  <div key={i} className={`vsched-item${compact ? " compact" : ""}${it.penciled ? " penciled" : ""}${it.bank_id ? " linked" : ""}`}
                       style={{ top, height: h, ["--lane" as string]: colors[it.lane ?? ""] ?? "var(--ink-dim)" }}
                       aria-label={`${it.label}, ${it.start}${it.end ? `–${it.end}` : ""}`}
                       {...itemHandlers(it, `item:${d.date}:${i}`)}>
                    <span className="vsched-item-time">{it.start}{it.end ? `–${it.end}` : ""}</span>
                    <span className="vsched-item-label"
                          style={compact ? undefined : { WebkitLineClamp: titleLines(h) }}>{it.label}</span>
                  </div>
                );
              })}
              {d.markers.map((m, i) => {
                const t = minutes(m.time);
                return t == null ? null : (
                  <span key={i} className="vsched-tick" style={{ top: y(t), ["--cat" as string]: catColor.get(m.cat ?? "") }}
                        title={`${m.time} ${m.label}`} />
                );
              })}
            </div>
          ))}
        </div>

        {/* phone: the same days as stacked cards */}
        <div className="vsched-list">
          {data.days.map((d) => (
            <section key={d.date} className={`vsched-card${d.weather?.wet ? " wet" : ""}`}>
              <header>
                <strong>{fmtDay(d)}</strong>
                <WeatherChip d={d} unit={data.precip_unit} />
                <span className="vsched-avail">
                  {d.availability.map((g) => <i key={g} style={{ background: groupColor.get(g) }} />)}
                  <em>{d.availability.map((g) => groupLabel.get(g) ?? g).join(" · ")}</em>
                </span>
              </header>
              {d.markers.map((m, i) => (
                <div key={`m${i}`} className="vsched-card-row marker" style={{ ["--cat" as string]: catColor.get(m.cat ?? "") }}>
                  <span>{m.time ?? "all day"}</span>◆ {m.label}
                </div>
              ))}
              {d.items.map((it, i) => (
                <div key={i} className={`vsched-card-row item${it.penciled ? " penciled" : ""}`}
                     style={{ ["--lane" as string]: colors[it.lane ?? ""] ?? "var(--ink-dim)" }}
                     {...itemHandlers(it, `item:${d.date}:${i}`)}>
                  <span>{it.start ?? "anytime"}</span>{it.label}
                </div>
              ))}
              {!d.items.length && !d.markers.length && <div className="vsched-card-row free">open day</div>}
            </section>
          ))}
        </div>

        <section className="vsched-bank">
          <div className="projections-chips" role="toolbar" aria-label="Filter the bank">
            {(["outing", "restaurant"] as const).filter((k) => data.bank.items.some((b) => b.kind === k)).map((k) => (
              <button key={k} type="button" aria-pressed={kind === k}
                      className={`bank-kind${kind === k ? " active" : ""}`}
                      onClick={() => { setKind(k); setGroup(null); }}>
                {k === "outing" ? "Ideas" : "Restaurants"}
              </button>
            ))}
            <button type="button" aria-pressed={group === null}
                    className={`projection-chip bank-chip${group === null ? " active" : ""}`} onClick={() => setGroup(null)}>all</button>
            {groups.map((g) => (
              <button key={g.key} type="button" aria-pressed={group === g.key}
                      className={`projection-chip bank-chip${group === g.key ? " active" : ""}`}
                      onClick={() => setGroup(group === g.key ? null : g.key)}>{g.label}</button>
            ))}
            <button type="button" aria-pressed={indoorOnly}
                    className={`projection-chip bank-rain${indoorOnly ? " active" : ""}`}
                    onClick={() => setIndoorOnly(!indoorOnly)}>☂ indoor only</button>
            <select className="vsched-band" aria-label="Drive time" value={band} onChange={(e) => setBand(e.target.value)}>
              {DRIVE_BANDS.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
            </select>
          </div>
          <div className="vsched-chips">
            {chips.map((b) => (
              <button key={b.id} type="button"
                      className={`vsched-chip${scheduledBank.has(b.id) ? " planned" : ""}`}
                      style={{ ["--lane" as string]: b.kind === "outing" ? colors[b.lane ?? ""] ?? "var(--ink-dim)" : "var(--bank-restaurant)" }}
                      onClick={() => { setHover(null); setOpenBank(b.id); }}
                      onMouseEnter={(e) => showTip(`bank:${b.id}`, e.currentTarget)}
                      onMouseLeave={() => setHover(null)}
                      onFocus={(e) => showTip(`bank:${b.id}`, e.currentTarget)}
                      onBlur={() => setHover(null)}>
                <i />{b.label}
                {driveText(b.drive_min) && <em>{b.drive_min === 0 ? "home" : `${b.drive_min}m`}</em>}
                {scheduledBank.has(b.id) && <em className="planned-tag">planned</em>}
              </button>
            ))}
            {!chips.length && <span className="projections-empty">Nothing in this filter.</span>}
          </div>
        </section>
      </div>
      {hoveredBank && hover && <QuickCard it={hoveredBank} pos={hover.pos} />}
      {hoveredItem && hover && (hoveredItemBank
        ? <QuickCard it={hoveredItemBank} pos={hover.pos} />
        : <ItemCard it={hoveredItem} pos={hover.pos} />)}
      {openBank && bankById.get(openBank) && (
        <BankPopup it={bankById.get(openBank)!} onClose={() => setOpenBank(null)}
                   onOpenDoc={() => { setOpenBank(null); setVisitDoc(true); }} />
      )}
      {openItem && (
        createPortal(
          <div className="doc-popup-backdrop" onClick={() => setOpenItem(null)}>
            <div className="doc-popup bezel bank-popup" role="dialog" aria-label={openItem.label} onClick={(e) => e.stopPropagation()}>
              <header className="doc-popup-head">
                <strong className="doc-popup-name">{openItem.label}</strong>
                <span className="doc-popup-path">{openItem.date}{openItem.lane ? ` · ${openItem.lane}` : ""}</span>
                <button className="doc-popup-close" onClick={() => setOpenItem(null)} title="Close (Esc)">✕</button>
              </header>
              <div className="doc-popup-body bank-popup-body">
                <dl className="bank-fields">
                  <dt>When</dt><dd>{openItem.start ? `${openItem.start}${openItem.end ? `–${openItem.end}` : ""}` : "anytime"}</dd>
                  {openItem.note && (<><dt>Note</dt><dd>{openItem.note}</dd></>)}
                  <dt>Status</dt><dd>{openItem.penciled ? "penciled suggestion (✎)" : "planned"}</dd>
                </dl>
              </div>
            </div>
          </div>,
          document.body,
        )
      )}
      {visitDoc && data.doc && <DocPopup doc={data.doc} onClose={() => setVisitDoc(false)} />}
    </div>
  );
}
