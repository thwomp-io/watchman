// TripPicker — the Visits/Trips tab's navigator. Data = `hn travel banks --json` (TripBanks): every
// visit or trip folder as a tile (cover photo, title, countdown, what it carries). Clicking a tile
// sets the tab's shared selection (selection.ts); the schedule and bank widgets on the same tab
// follow it (`symbols_from: visit|trip` → their `{symbol}` arg). Nothing selected = "auto", which
// the verbs resolve to `default` — the tile this picker highlights — so the two always agree.
//
// Upcoming entries first, then the parked ideas ("someday"), then a divider and the past ones; an entry with no bank or schedule yet
// stays visible (dimmed, "nothing planned yet") so the tab reads as the full horizon.

import { useEffect, useRef, useState } from "react";
import { readImage } from "./api";
import DocPopup from "./DocPopup";
import type { TripBanks, TripEntry } from "./generated/contracts";
import { setSelection, useSelection } from "./selection";

export type TripPickerData = TripBanks;

const cache = new Map<string, string>();

export function Cover({ path }: { path: string | null }) {
  const [src, setSrc] = useState<string | null>(() => (path ? cache.get(path) ?? null : null));
  useEffect(() => {
    if (!path || cache.has(path)) return;
    let live = true;
    readImage(path).then((d) => { cache.set(path, d); if (live) setSrc(d); }).catch(() => {});
    return () => { live = false; };
  }, [path]);
  return <div className="picker-cover">{src ? <img src={src} alt="" /> : <span>no photo</span>}</div>;
}

export function summary(e: TripEntry): string {
  const bits = [
    e.outings ? `${e.outings} ideas` : "",
    e.restaurants ? `${e.restaurants} restaurants` : "",
    e.scheduled ? `${e.scheduled} planned` : "",
  ].filter(Boolean);
  return bits.length ? bits.join(" · ") : "nothing planned yet";
}

export function dates(e: TripEntry): string {
  if (!e.start) return e.date;
  const fmt = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  return e.end && e.end !== e.start ? `${fmt(e.start)} – ${fmt(e.end)}` : fmt(e.start);
}

export default function TripPicker({ lane, data }: { lane: string; data: TripPickerData }) {
  const scope = data?.scope ?? "visit";
  const picked = useSelection(lane, scope);
  const entries = data?.entries ?? [];
  const known = picked && entries.some((e) => e.slug === picked);
  const active = known ? picked : data?.default ?? null;
  const [doc, setDoc] = useState<string | null>(null);
  const strip = useRef<HTMLDivElement>(null);

  // keep the active tile in view (the strip scrolls sideways once there are many)
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>(".picker-tile.active")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [active]);

  if (!entries.length) {
    return <p className="projections-empty">No {scope === "trip" ? "trips" : "visits"} in the corpus yet.</p>;
  }
  const phase = (e: TripEntry) => e.phase ?? (e.live ? "live" : "past");
  const live = entries.filter((e) => phase(e) === "live");
  const someday = entries.filter((e) => phase(e) === "someday");
  const past = entries.filter((e) => phase(e) === "past");
  const tile = (e: TripEntry) => {
    const empty = !(e.outings || e.restaurants || e.scheduled);
    return (
      <button key={e.slug} type="button" aria-pressed={e.slug === active}
              className={`picker-tile${e.slug === active ? " active" : ""}${empty ? " empty" : ""}${e.live ? "" : " past"}`}
              onClick={() => setSelection(lane, scope, e.slug)}
              title={e.title}>
        <Cover path={e.cover} />
        <span className="picker-body">
          <strong>{e.title}</strong>
          <span className="picker-when">{e.when} · {dates(e)}</span>
          <span className="picker-sum">{summary(e)}</span>
        </span>
      </button>
    );
  };
  const current = entries.find((e) => e.slug === active);
  return (
    <div className="picker">
      <div className="picker-strip" ref={strip} role="toolbar" aria-label={scope === "trip" ? "Trips" : "Visits"}>
        {live.map(tile)}
        {someday.length > 0 && <span className="picker-divider" aria-hidden="true">someday</span>}
        {someday.map(tile)}
        {past.length > 0 && <span className="picker-divider" aria-hidden="true">past</span>}
        {past.map(tile)}
      </div>
      {current && (
        <div className="picker-foot">
          <span>{current.destination}</span>
          <a href="#" onClick={(ev) => { ev.preventDefault(); setDoc(current.doc); }}>open the {scope} note ↗</a>
        </div>
      )}
      {doc && <DocPopup doc={doc} onClose={() => setDoc(null)} />}
    </div>
  );
}
