// BankTiles — the Visits tab's browsable idea bank: one photo tile per outing or restaurant of the
// soonest live visit. Data contract = `hn travel bank --json` (VisitBank, generated types); the
// engine normalizes both banks to one item shape, so this component never branches on file format.
//
// Interaction follows the other tile widgets: hover/focus a tile → a quick card (portal, fixed
// position); click → a detail popup with every field and the outbound link buttons. On touch a tap
// opens the popup directly (no hover to show). Filters: the outings/restaurants switch, group
// chips, and a rain-safe toggle that hides outdoor items (restaurants carry no outdoor flag, so
// they all stay). Images load when a tile scrolls into view: a bank is dozens of photos and the
// served console reads each one over the network.

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { readImage } from "./api";
import DocPopup from "./DocPopup";
import type { BankItem, VisitBank } from "./generated/contracts";
import { coarsePointer, tipPosition, type TipPos } from "./tips";

type Kind = BankItem["kind"];

const KIND_LABEL: Record<Kind, string> = { outing: "Outings", restaurant: "Restaurants" };

const LINK_LABEL: Record<string, string> = {
  maps: "Google Maps",
  website: "Website",
  tickets: "Tickets",
  reservations: "Reserve",
  reservation: "Reserve",
  order: "Order online",
  menu: "Menu",
};

export function linkLabel(key: string): string {
  return LINK_LABEL[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

// Reserve first (the action), then Maps, then the rest in the bank's own order.
const LINK_ORDER = ["reservations", "reservation", "maps", "tickets"];
export function orderedLinks(links: Record<string, string>): [string, string][] {
  const rank = (k: string) => (LINK_ORDER.includes(k) ? LINK_ORDER.indexOf(k) : LINK_ORDER.length);
  return Object.entries(links).sort((a, b) => rank(a[0]) - rank(b[0]));
}

const WEEKDAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// The hours line for a day ("Mon 11 AM–9 PM" → "11 AM–9 PM"), from the "Day …" lines enrichment
// writes; null when the item has no weekly hours (authored free-text hours stay as written).
export function hoursOn(hours: string[], day: Date = new Date()): string | null {
  const abbr = WEEKDAY_ABBR[day.getDay()];
  const line = hours.find((h) => h.startsWith(`${abbr} `));
  return line ? line.slice(abbr.length + 1) : null;
}

export function driveText(min: number | null): string | null {
  if (min == null) return null;
  if (min === 0) return "at home";
  return min >= 60 ? `${Math.floor(min / 60)}h${min % 60 ? ` ${min % 60}m` : ""} drive` : `${min}m drive`;
}

export function ratingText(it: BankItem): string | null {
  if (it.rating == null) return null;
  return it.reviews != null ? `${it.rating}★ (${it.reviews.toLocaleString()})` : `${it.rating}★`;
}

function indoorText(v: boolean | null): string | null {
  return v === true ? "indoor" : v === false ? "outdoor" : null;
}

export function visibleItems(
  items: BankItem[], kind: Kind, group: string | null, rainSafe: boolean,
): BankItem[] {
  return items.filter(
    (i) => i.kind === kind && (group === null || i.group === group) && !(rainSafe && i.indoor === false),
  );
}

// Session cache by vault path: a data URI is deterministic per file, so remounts never refetch.
const imgCache = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

function loadImage(path: string): Promise<string> {
  const hit = imgCache.get(path);
  if (hit) return Promise.resolve(hit);
  let p = inflight.get(path);
  if (!p) {
    p = readImage(path).then((d) => {
      imgCache.set(path, d);
      return d;
    }).finally(() => inflight.delete(path));
    inflight.set(path, p);
  }
  return p;
}

export function step(idx: number, delta: number, n: number): number {
  return n === 0 ? 0 : (idx + delta + n) % n;
}

// A photo carousel over an item's images (cover first, then its gallery). Loads only when the
// tile scrolls into view, shows one photo at a time, preloads the next. Arrows and the counter
// stop propagation so paging never opens the tile's popup; on touch a horizontal swipe pages.
export function Gallery({ paths, alt, large = false }: { paths: string[]; alt: string; large?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(() => large || typeof IntersectionObserver === "undefined");
  const [idx, setIdx] = useState(0);
  const path = paths[idx] ?? null;
  const [src, setSrc] = useState<string | null>(() => (path ? imgCache.get(path) ?? null : null));
  const [failed, setFailed] = useState(false);
  const touchX = useRef<number | null>(null);
  const n = paths.length;

  useEffect(() => {
    if (inView || !ref.current) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setInView(true);
        io.disconnect();
      }
    }, { rootMargin: "200px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [inView]);

  useEffect(() => {
    setFailed(false);
    setSrc(path ? imgCache.get(path) ?? null : null);
    if (!path || !inView) return;
    let live = true;
    loadImage(path)
      .then((d) => {
        if (live) setSrc(d);
        const next = paths[step(idx, 1, n)];
        if (next && next !== path) loadImage(next).catch(() => {});
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [path, inView, idx, n, paths]);

  // The popup's large gallery pages from the keyboard anywhere in the dialog.
  useEffect(() => {
    if (!large || n < 2) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") setIdx((i) => step(i, 1, n));
      if (e.key === "ArrowLeft") setIdx((i) => step(i, -1, n));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [large, n]);

  const go = (delta: number) => (e: { stopPropagation: () => void; preventDefault?: () => void }) => {
    e.stopPropagation();
    e.preventDefault?.();
    setIdx((i) => step(i, delta, n));
  };

  return (
    <div
      className={`bank-img${large ? " large" : ""}`}
      ref={ref}
      onKeyDown={(e) => {
        if (n < 2 || large) return;
        if (e.key === "ArrowRight") go(1)(e);
        if (e.key === "ArrowLeft") go(-1)(e);
      }}
      onTouchStart={(e) => { touchX.current = e.touches[0]?.clientX ?? null; }}
      onTouchEnd={(e) => {
        const x0 = touchX.current;
        const x1 = e.changedTouches[0]?.clientX;
        touchX.current = null;
        if (n < 2 || x0 == null || x1 == null || Math.abs(x1 - x0) < 40) return;
        go(x1 < x0 ? 1 : -1)(e);
      }}
    >
      {src ? (
        <img src={src} alt={alt} />
      ) : (
        <span className="bank-img-empty">{!path ? "no photo" : failed ? "photo unavailable" : ""}</span>
      )}
      {n > 1 && (
        <>
          <button type="button" className="bank-nav prev" aria-label="Previous photo" onClick={go(-1)}>‹</button>
          <button type="button" className="bank-nav next" aria-label="Next photo" onClick={go(1)}>›</button>
          <span className="bank-count" aria-live="polite">{idx + 1}/{n}</span>
        </>
      )}
    </div>
  );
}

export function QuickCard({ it, pos }: { it: BankItem; pos: TipPos }) {
  const facts = [it.area, driveText(it.drive_min), indoorText(it.indoor)].filter(Boolean).join(" · ");
  const second = [ratingText(it), it.price, it.cuisine].filter(Boolean).join(" · ");
  const line = it.why ?? it.note;
  return createPortal(
    <div className={`item-tip bank-tip kind-${it.kind}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{it.label}</div>
      {facts && <div className="item-tip-stat">{facts}</div>}
      {second && <div className="item-tip-stat">{second}</div>}
      {it.season && <div className="item-tip-ilvl">Season: {it.season}</div>}
      {hoursOn(it.hours) && <div className="item-tip-ilvl">Today: {hoursOn(it.hours)}</div>}
      {(it.links.reservations || it.links.reservation) && <div className="item-tip-stat">Reservable ✓</div>}
      {line && <div className="item-tip-flavor">{line}</div>}
      <div className="item-tip-source">{it.group_label}</div>
    </div>,
    document.body,
  );
}

function Row({ k, v }: { k: string; v: string | null | undefined }) {
  if (v == null || v === "") return null;
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

function extraText(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

export function BankPopup({ it, onClose, onOpenDoc }: {
  it: BankItem; onClose: () => void; onOpenDoc?: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="doc-popup-backdrop" onClick={onClose}>
      <div className="doc-popup bezel bank-popup" role="dialog" aria-label={it.label}
           onClick={(e) => e.stopPropagation()}>
        <header className="doc-popup-head">
          <strong className="doc-popup-name">{it.label}</strong>
          <span className="doc-popup-path">{KIND_LABEL[it.kind]} · {it.group_label}</span>
          {onOpenDoc && (
            <a className="doc-popup-vault" href="#" onClick={(e) => { e.preventDefault(); onOpenDoc(); }}>
              visit note ↗
            </a>
          )}
          <button className="doc-popup-close" onClick={onClose} title="Close (Esc)">✕</button>
        </header>
        <div className="doc-popup-body bank-popup-body">
          {it.images.length > 0 && <Gallery paths={it.images} alt={it.label} large />}
          {Object.keys(it.links).length > 0 && (
            <div className="bank-links">
              {orderedLinks(it.links).map(([k, url]) => (
                <a key={k} className="bank-link" href={url} target="_blank" rel="noreferrer">
                  {linkLabel(k)} ↗
                </a>
              ))}
            </div>
          )}
          {it.why && <p className="bank-why">{it.why}</p>}
          {it.note && <p className="bank-note">{it.note}</p>}
          <dl className="bank-fields">
            <Row k="Area" v={it.area} />
            <Row k="Drive" v={driveText(it.drive_min)} />
            <Row k="Setting" v={indoorText(it.indoor)} />
            <Row k="Rating" v={ratingText(it)} />
            <Row k="Yelp" v={it.yelp_rating != null ? `${it.yelp_rating}★` : null} />
            <Row k="Price" v={it.price} />
            <Row k="Cuisine" v={it.cuisine} />
            <Row k="Meals" v={it.meals.join(", ")} />
            <Row k="Season" v={it.season} />
            <Row k="Status" v={it.status} />
            <Row k="Confirm" v={it.confirm} />
            {it.hours.length > 0 && (
              <>
                <dt>Hours</dt>
                <dd className="bank-hours">
                  {it.hours.map((h) => (
                    <span key={h} className={hoursOn([h]) != null ? "today" : undefined}>{h}</span>
                  ))}
                </dd>
              </>
            )}
            {it.phone && (<><dt>Phone</dt><dd><a href={`tel:${it.phone.replace(/[^+\d]/g, "")}`}>{it.phone}</a></dd></>)}
            {Object.entries(it.extra).map(([k, v]) => <Row key={k} k={k} v={extraText(v)} />)}
          </dl>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default function BankTiles({ data }: { data: VisitBank }) {
  const items = data?.items ?? [];
  const kinds = (["outing", "restaurant"] as Kind[]).filter((k) => items.some((i) => i.kind === k));
  const [kind, setKind] = useState<Kind>(kinds[0] ?? "outing");
  const [group, setGroup] = useState<string | null>(null);
  const [rainSafe, setRainSafe] = useState(false);
  const [hover, setHover] = useState<{ id: string; pos: TipPos } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [visitDoc, setVisitDoc] = useState(false);

  if (items.length === 0) {
    return (
      <p className="projections-empty">
        {data?.title || "No live visit carries a bank yet"} — banks live beside a visit's folder-note
        (<code>schedule.json</code> · <code>restaurants.json</code>).
      </p>
    );
  }

  const activeKind = kinds.includes(kind) ? kind : kinds[0];
  const groups = (data.groups ?? []).filter((g) => g.kind === activeKind);
  const shown = visibleItems(items, activeKind, group, rainSafe);
  const hidden = rainSafe ? visibleItems(items, activeKind, group, false).length - shown.length : 0;
  const hovered = hover ? items.find((i) => i.id === hover.id) : undefined;
  const opened = open ? items.find((i) => i.id === open) : undefined;

  return (
    <div className="projections bank">
      <div className="bank-head">
        {kinds.map((k) => (
          <button key={k} type="button" aria-pressed={k === activeKind}
                  className={`bank-kind${k === activeKind ? " active" : ""}`}
                  title={k === "outing" ? data.outings_title : data.restaurants_title}
                  onClick={() => { setKind(k); setGroup(null); }}>
            {KIND_LABEL[k]} <b>{items.filter((i) => i.kind === k).length}</b>
          </button>
        ))}
        {data.doc && (
          <a className="bank-visit" href="#" title={data.doc}
             onClick={(e) => { e.preventDefault(); setVisitDoc(true); }}>
            {data.title} ↗
          </a>
        )}
      </div>
      <div className="projections-chips" role="toolbar" aria-label="Filter the bank">
        <button type="button" className={`projection-chip${group === null ? " active" : ""}`}
                aria-pressed={group === null} onClick={() => setGroup(null)}>
          all
        </button>
        {groups.map((g) => (
          <button type="button" key={g.key} aria-pressed={group === g.key}
                  className={`projection-chip bank-chip${group === g.key ? " active" : ""}`}
                  onClick={() => setGroup(group === g.key ? null : g.key)}>
            {g.label} <b>{g.count}</b>
          </button>
        ))}
        <button type="button" aria-pressed={rainSafe}
                className={`projection-chip bank-rain${rainSafe ? " active" : ""}`}
                title="Hide outdoor items (the rain-day view)"
                onClick={() => setRainSafe(!rainSafe)}>
          ☂ rain-safe{hidden > 0 && <b>−{hidden}</b>}
        </button>
      </div>
      <div className="projections-body">
        <div className="bank-grid">
          {shown.map((it) => {
            const show = (el: HTMLElement) =>
              setHover({ id: it.id, pos: tipPosition(el.getBoundingClientRect(), 320, 260) });
            const sub = [it.area, driveText(it.drive_min)].filter(Boolean).join(" · ");
            const meta = [ratingText(it), it.price, it.kind === "restaurant" ? it.cuisine : null]
              .filter(Boolean).join(" · ");
            return (
              <div
                key={it.id}
                role="button"
                tabIndex={0}
                className={`bank-tile kind-${it.kind}${it.indoor === false ? " outdoor" : ""}`}
                onClick={() => { setHover(null); setOpen(it.id); }}
                onKeyDown={(e) => {
                  if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    setHover(null);
                    setOpen(it.id);
                  }
                }}
                onMouseEnter={(e) => { if (!coarsePointer()) show(e.currentTarget); }}
                onMouseLeave={() => setHover(null)}
                onFocus={(e) => { if (!coarsePointer()) show(e.currentTarget); }}
                onBlur={() => setHover(null)}
                aria-label={`${it.label}${sub ? ` — ${sub}` : ""}; open details`}
              >
                <Gallery paths={it.images} alt="" />
                <div className="bank-tile-body">
                  <strong className="bank-tile-label">{it.label}</strong>
                  {sub && <span className="bank-tile-sub">{sub}</span>}
                  {meta && <span className="bank-tile-sub">{meta}</span>}
                  <span className="bank-tile-chips">
                    {group === null && <em>{it.group_label}</em>}
                    {indoorText(it.indoor) && <em className={`setting-${indoorText(it.indoor)}`}>{indoorText(it.indoor)}</em>}
                    {it.season && <em>{it.season}</em>}
                    {it.status && <em>{it.status}</em>}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        {shown.length === 0 && <p className="projections-empty">Nothing in this filter.</p>}
        {activeKind === "restaurant" && data.notes.length > 0 && (
          <p className="projections-disclaimer">{data.notes.join(" · ")}</p>
        )}
      </div>
      {hovered && hover && <QuickCard it={hovered} pos={hover.pos} />}
      {opened && (
        <BankPopup it={opened} onClose={() => setOpen(null)}
                   onOpenDoc={data.doc ? () => { setOpen(null); setVisitDoc(true); } : undefined} />
      )}
      {visitDoc && data.doc && <DocPopup doc={data.doc} onClose={() => setVisitDoc(false)} />}
    </div>
  );
}
