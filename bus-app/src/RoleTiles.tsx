// RoleTiles — Career ▸ Openings: the latest openings scan as ROLE ITEMS, on the Pool's grammar. One
// tile per opening, classed by the ENGINE (`hn career roles --json`): the company's item level as the
// base (unranked when the company has no band row), the role modifiers on top, the rarity, and a fit
// class — on-profile · adjacent · off-lane — that orders the panes. The UI never re-derives any of it.
//
// Defaults answer "what deserves my attention": new since the earlier scans + seats you can take
// without moving (the home area, remote in the US, or unknown). Chips narrow by fit, shape, level and
// rarity; "show everything" clears them. The tab follows the `company` picker scope — the Pool's
// openings chip lands here with that company selected (a dismissible chip; "auto" = every company).
//
// Interaction is the loot surfaces' contract: hover/focus → the item card (portal); coarse pointers
// are two-tap; click → the posting in a new tab; "profile" → the company's generated profile.

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";
import type { RoleItem, RolesReport } from "./generated/contracts";
import { setSelection } from "./selection";
import { coarsePointer, tipPosition, type TipPos } from "./tips";

export type RolesData = RolesReport;

const FITS = ["on-profile", "adjacent", "off-lane"] as const;
const FIT_TITLE: Record<string, string> = {
  "on-profile": "On-profile — in-lane shape, in-band or leadership level, a seat you can take",
  adjacent: "Adjacent — near the lane: a neutral shape, a below-band title, or a relocation",
  "off-lane": "Off-lane — facilities, business, hardware, or outside the US",
};
const LOCATIONS = [
  { key: "home", label: "home + remote US", keep: ["home", "remote_us", "unknown"] },
  { key: "us", label: "US", keep: ["home", "remote_us", "unknown", "relocation"] },
  { key: "all", label: "anywhere", keep: null },
] as const;
const LEVEL_LABEL: Record<string, string> = { in_band: "Staff+", lead: "leadership", below: "Senior", none: "no level" };
const LOC_LABEL: Record<string, string> = {
  home: "home area", remote_us: "remote US", relocation: "relocation", outside_us: "outside US", unknown: "location unknown",
};
const PAGE = 48;
const lvl = (v: number) => `${v >= 0 ? "+" : ""}${v}`;
// $1,412,500 → $1.41M · $306,576 → $307k
const money = (v: number) => (v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(2)}M` : `$${Math.round(v / 1000)}k`);

function CompBlock({ r, anchor }: { r: RoleItem; anchor: number }) {
  const c = r.comp;
  if (!c) {
    return (
      <div className="role-comp">
        <div className="role-comp-head">Expected comp</div>
        <div className="item-tip-stat pool-mix">
          {r.company_median_tc != null
            ? `No level read for this title on ${r.company}'s ladder — company median ${money(r.company_median_tc)} (all levels, context only)`
            : `No levels.fyi ladder cached for ${r.company} — \`hn career comp\` adds one`}
        </div>
      </div>
    );
  }
  const vs = Math.round((c.median_tc / anchor - 1) * 100);
  return (
    <div className="role-comp">
      <div className="role-comp-head">Expected comp · {c.level}</div>
      <div className="role-comp-median">
        {money(c.median_tc)} <span className={vs >= 0 ? "pos" : "neg"}>{lvl(vs)}% vs anchor</span>
      </div>
      <div className="item-tip-stat pool-mix">
        {c.low_tc != null ? `${c.low_level} ${money(c.low_tc)}` : "bottom rung"} ← median TC →{" "}
        {c.high_tc != null ? `${c.high_level} ${money(c.high_tc)}` : "top rung"}
      </div>
      <div className="item-tip-stat pool-basis">level: {c.band} · basis {c.basis}{c.note ? ` · ${c.note}` : ""}</div>
      <div className="item-tip-source">{c.attribution} · {c.location.replace(/-/g, " ")} · medians per level</div>
    </div>
  );
}

export function RoleCard({ r, constitution, anchor, pos }: { r: RoleItem; constitution: string; anchor: number; pos: TipPos }) {
  return createPortal(
    <div className={`item-tip pool-tip role-tip rarity-${r.rarity}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{r.title}</div>
      {r.final_ilvl != null ? (
        <div className="item-tip-ilvl">Item Level {r.final_ilvl} · {r.rarity}</div>
      ) : (
        <div className="item-tip-ilvl">Unranked — no level read on {r.company}'s ladder, no item level</div>
      )}
      <div className="item-tip-bind">
        {r.company}
        <span className="item-tip-slot">{r.fit}</span>
      </div>
      {r.screen_flag !== "none" && <div className="item-tip-screen">⊘ {r.screen_flag}</div>}
      <div className="item-tip-stat">
        {r.shape} · {LEVEL_LABEL[r.level] ?? r.level} · {LOC_LABEL[r.location_class] ?? r.location_class}
      </div>
      <div className="item-tip-stat pool-mix">{r.location || "—"}</div>
      <div className="item-tip-stat">
        Posted: {r.salary || "no range posted"}
      </div>
      <CompBlock r={r} anchor={anchor} />
      {r.base_ilvl != null && (
        <div className="item-tip-stat pool-basis">
          {r.base_source === "level ladder"
            ? `Base ${lvl(r.base_ilvl - r.company_mods)} (the rung vs the anchor) · company mods ${lvl(r.company_mods)} · role mods ${lvl(r.modifier_sum)}`
            : `Base ${lvl(r.base_ilvl)} (the company item${r.company_rarity ? `, ${r.company_rarity}` : ""}) · role mods ${lvl(r.modifier_sum)}`}
        </div>
      )}
      {r.modifiers.length > 0 && (
        <ul className="pool-mods">
          {r.modifiers.map((m) => (
            <li key={m.key} className={m.value >= 0 ? "enchant" : "debuff"}>
              <span className="pool-mod-val">{lvl(m.value)}</span> {m.name}
              <em className="pool-mod-how">{m.source}</em>
            </li>
          ))}
        </ul>
      )}
      <div className="item-tip-flavor">
        {r.new ? "New since the earlier scans" : `First seen ${r.first_seen || "—"}`}
        {r.days_old != null ? ` · updated ${r.days_old}d ago` : ""}
      </div>
      <div className="item-tip-source">{constitution}</div>
    </div>,
    document.body,
  );
}

function count<T>(rows: T[], key: (r: T) => string): [string, number][] {
  const m = new Map<string, number>();
  for (const r of rows) m.set(key(r), (m.get(key(r)) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

export default function RoleTiles({ lane, data }: { lane: string; data: RolesData }) {
  const company = data?.filters?.company && data.filters.company !== "auto" ? data.filters.company : null;
  // a company pick arrives from the Pool chip: show all of that company's openings, not only new ones
  const [newOnly, setNewOnly] = useState<boolean>(company === null);
  const [loc, setLoc] = useState<string>("home");
  const [fit, setFit] = useState<string | null>(null);
  const [shape, setShape] = useState<string | null>(null);
  const [level, setLevel] = useState<string | null>(null);
  const [rarity, setRarity] = useState<string | null>(null);
  const [more, setMore] = useState<Record<string, number>>({});
  const [hover, setHover] = useState<{ url: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [doc, setDoc] = useState<string | null>(null);
  const rows = data?.rows ?? [];
  const gated = data?.screen_gated ?? [];

  if (rows.length === 0 && gated.length === 0) {
    return (
      <p className="projections-empty">
        No scan yet — <code>hn career openings --write</code> persists one into{" "}
        <code>role-hunt/discoveries/</code>; this tab reads the newest.
      </p>
    );
  }

  const keep = LOCATIONS.find((l) => l.key === loc)?.keep ?? null;
  const base = rows.filter((r) => (!newOnly || r.new) && (!keep || (keep as readonly string[]).includes(r.location_class)));
  const visible = base.filter((r) =>
    (!fit || r.fit === fit) && (!shape || r.shape === shape) && (!level || r.level === level)
    && (!rarity || r.rarity === rarity));
  const reset = () => {
    setNewOnly(false); setLoc("all"); setFit(null); setShape(null); setLevel(null); setRarity(null);
  };
  const chip = (label: string, active: boolean, onClick: () => void, n?: number, cls = "") => (
    <button type="button" key={label} className={`projection-chip pool-filter ${cls}${active ? " active" : ""}`}
            onClick={onClick} aria-pressed={active}>
      {label}{n != null && <b> {n}</b>}
    </button>
  );
  const toggle = (cur: string | null, set: (v: string | null) => void, v: string) => () => set(cur === v ? null : v);

  const tile = (r: RoleItem) => {
    const show = (el: HTMLElement) => setHover({ url: r.url, pos: tipPosition(el.getBoundingClientRect(), 340, 460) });
    const open = () => window.open(r.url, "_blank", "noreferrer");
    return (
      <div key={r.url} role="link" tabIndex={0}
           className={`projection-tile pool-tile role-tile rarity-${r.rarity} fit-${r.fit}`}
           onClick={(e) => {
             if (coarsePointer() && armed !== r.url) { setArmed(r.url); show(e.currentTarget); return; }
             open();
           }}
           onKeyDown={(e) => { if (e.key === "Enter") open(); }}
           onMouseEnter={(e) => show(e.currentTarget)}
           onMouseLeave={() => setHover(null)}
           onFocus={(e) => show(e.currentTarget)}
           onBlur={() => { setHover(null); setArmed(null); }}
           aria-label={`${r.title} at ${r.company} — ${r.fit}, ${r.final_ilvl != null ? `item level ${r.final_ilvl}` : "unranked"}; opens the posting`}>
        <div className="projection-head">
          <strong className="projection-sym">{r.company}</strong>
          {r.new && <span className="projection-held role-new">NEW</span>}
          {r.screen_flag !== "none" && <span className="projection-screened" title={r.screen_flag}>⊘</span>}
          <span className="projection-ilvl">{r.final_ilvl != null ? `ilvl ${r.final_ilvl}` : "unranked"}</span>
        </div>
        <div className="role-title">{r.title}</div>
        <div className="projection-sub">
          <span className={`role-lane lane-${r.lane}`}>{r.shape}</span>
          <span className="role-loc">{r.location || "—"}</span>
          {r.salary ? <span className="role-salary">{r.salary}</span>
            : r.comp ? <span className="role-salary role-est" title={`levels.fyi median, ${r.comp.level}`}>~{money(r.comp.median_tc)}</span>
            : null}
          {r.profile && (
            <button type="button" className="role-profile"
                    onClick={(e) => { e.stopPropagation(); setDoc(r.profile); }}>profile</button>
          )}
        </div>
      </div>
    );
  };

  const all = [...rows, ...gated];
  return (
    <div className="projections pool roles">
      <div className="pool-header">
        <span>
          {data.counts.total} openings · {data.counts.new} new · on-profile {data.counts.fit["on-profile"] ?? 0}
          {" · "}scan {data.as_of || "—"} · ranked fit → new → item level
        </span>
        <span className="pool-anchor">anchor ${data.anchor.toLocaleString()}</span>
      </div>
      <div className="projections-chips pool-filters" aria-label="Filter the openings">
        {company && chip(`company: ${company} ✕`, true, () => setSelection(lane, "company", "auto"), undefined, "role-company")}
        {chip("new only", newOnly, () => setNewOnly(!newOnly), rows.filter((r) => r.new).length)}
        <span className="pool-filter-sep" />
        {LOCATIONS.map((l) => chip(l.label, loc === l.key, () => setLoc(l.key)))}
        <span className="pool-filter-sep" />
        {chip("show everything", false, reset)}
      </div>
      <div className="projections-chips pool-filters" aria-label="Fit, shape, level, rarity">
        {FITS.map((f) => chip(f, fit === f, toggle(fit, setFit, f), base.filter((r) => r.fit === f).length, `fit-chip fit-${f}`))}
        <span className="pool-filter-sep" />
        {count(base, (r) => r.shape).map(([k, n]) => chip(k, shape === k, toggle(shape, setShape, k), n))}
        <span className="pool-filter-sep" />
        {count(base, (r) => r.level).map(([k, n]) => chip(LEVEL_LABEL[k] ?? k, level === k, toggle(level, setLevel, k), n))}
        <span className="pool-filter-sep" />
        {count(base, (r) => r.rarity).map(([k, n]) => chip(k, rarity === k, toggle(rarity, setRarity, k), n, `rarity-${k}`))}
      </div>
      <div className="projections-body">
        {visible.length === 0 && <p className="projections-empty">Nothing matches these filters — try “show everything”.</p>}
        {FITS.map((f) => {
          const items = visible.filter((r) => r.fit === f);
          if (!items.length) return null;
          const n = more[f] ?? PAGE;
          return (
            <section key={f} className={`projections-pane role-pane fit-${f}`}>
              <h4 className="projections-pane-title">{FIT_TITLE[f]} · {items.length}</h4>
              <div className="projections-grid">{items.slice(0, n).map(tile)}</div>
              {items.length > n && (
                <button type="button" className="projection-chip role-more" onClick={() => setMore({ ...more, [f]: n + PAGE })}>
                  show {Math.min(PAGE, items.length - n)} more of {items.length - n}
                </button>
              )}
            </section>
          );
        })}
        {gated.length > 0 && !rarity && (
          <section className="projections-pane">
            <h4 className="projections-pane-title">Screen-gated — visible, marked, no arithmetic · {gated.length}</h4>
            <div className="projections-grid">{gated.slice(0, PAGE).map(tile)}</div>
          </section>
        )}
      </div>
      {hover && (() => {
        const r = all.find((x) => x.url === hover.url);
        return r ? <RoleCard r={r} constitution={data.constitution} anchor={data.anchor} pos={hover.pos} /> : null;
      })()}
      <p className="projections-disclaimer">{data.constitution}</p>
      {doc && <DocPopup doc={doc} onClose={() => setDoc(null)} />}
    </div>
  );
}
