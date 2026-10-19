// PoolTiles — the Career ▸ Pool tab: the levels.fyi leaderboard cut
// read through the item model, as loot tiles. One tile per company, rarity-classed by the ENGINE
// (base ilvl = median TC vs the comp anchor; named modifiers; set bonuses; the one-tier cap) — the
// UI never re-derives a level or a tier. Two panes: In-play (the arithmetic ran) and Screen-gated
// (a screen OUT flag: visible, marked, no arithmetic — a screened-out company stays in view so the
// survey still shows the whole landscape; the screen only gates what is pursued).
//
// Interaction contract is ProjectionTiles' verbatim (one pattern, every loot surface): hover or
// focus → the portal-rendered item card; coarse pointers are two-tap; click → the company's
// research entry in a DocPopup, or the levels.fyi company page when the vault has none. The
// tooltip is the whole read: base ilvl (the % and the $), each modifier on its own line —
// ENCHANTMENTS GREEN, DEBUFFS RED (sign at a glance, no reading needed) — with value + plain name + provenance
// tier, the set-bonus line, the "capped at" line when the clamp fired, and the constitutional
// footer. Data contract = `hn career pool --survey --json` (the newest persisted survey; the tab
// never fetches).

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";
import { useNav } from "./nav";

export interface PoolModifier {
  key: string;
  name: string;
  value: number;
  family: string;
  how: string; // machine | agent | the pool-items.yaml `by:` value (e.g. operator)
  source?: string;
}

export interface PoolSetBonus {
  family: string;
  name: string;
  count: number;
  have: number;
  bonus: number;
}

export interface PoolRow {
  rank: number;
  company: string;
  slug: string;
  label?: string;
  median_tc: number;
  base?: number | null;
  stock?: number | null;
  bonus?: number | null;
  on_watchlist: boolean;
  watchlist_name?: string;
  ticker?: string;
  bench: boolean;
  screen_flag: string;
  screened_out: boolean;
  base_ilvl: number | null;
  base_rarity: string | null;
  modifiers: PoolModifier[];
  set_bonuses: PoolSetBonus[];
  modifier_sum: number;
  final_ilvl: number | null;
  rarity: string | null;
  capped: boolean;
  capped_at?: string | null;
  agency: string;
  doc?: string | null;
  source?: string; // leaderboard | ladder
  cut?: string; // "<level>/<geo>" of the appearance the base came from
  basis?: string;
  appearances?: { source: string; cut: string; level: string; label: string; median_tc: number; rank?: number | null; basis?: string }[];
  // the latest openings scan's count for this company (`pool --survey` stamps it from the role items)
  openings?: { openings: number; on_profile: number; new: number };
}

export interface PoolData {
  cut: { family: string; level: string; kind: string; geo: string; url: string; key: string };
  as_of: string;
  anchor: number;
  rows: PoolRow[];
  screen_gated: PoolRow[];
  attribution: string;
  constitution: string;
  survey_file?: string;
  merged?: boolean;
  sources?: { surveys: string[]; ladders: number; band: string };
  diff?: {
    prior_as_of: string;
    new_entrants: string[];
    dropped: string[];
    rank_moves: { company: string; from: number; to: number }[];
    rarity_changes: { company: string; from: string | null; to: string | null }[];
  } | null;
}

// "unranked": the base number is not the applicant's band — shown, never scored.
const RARITY_ORDER = ["legendary", "epic", "rare", "common", "poor", "unranked"];
const usd = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const lvl = (v: number) => `${v >= 0 ? "+" : ""}${v}`;
const levelsUrl = (slug: string) => `https://www.levels.fyi/companies/${slug}/salaries/software-engineer`;

interface TipPos {
  left: number;
  top: number;
}

function tipPosition(rect: DOMRect): TipPos {
  const TIP_W = 340;
  const pad = 10;
  let left = rect.right + pad;
  if (left + TIP_W > window.innerWidth - pad) left = Math.max(pad, rect.left - TIP_W - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - 440));
  return { left, top };
}

function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}

export function ItemCard({ r, anchor, constitution, pos }: { r: PoolRow; anchor: number; constitution: string; pos: TipPos }) {
  const basePct = r.base_ilvl;
  return createPortal(
    <div className={`item-tip pool-tip rarity-${r.rarity ?? "common"}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{r.company}</div>
      {r.final_ilvl != null ? (
        <div className="item-tip-ilvl">Item Level {r.final_ilvl}{r.capped && r.capped_at ? ` · capped at ${r.capped_at}` : ""}</div>
      ) : r.screened_out ? (
        <div className="item-tip-ilvl">Screen-gated — no item level</div>
      ) : (
        <div className="item-tip-ilvl">Unranked — no band row, no item level</div>
      )}
      <div className="item-tip-bind">
        {r.on_watchlist ? "On watchlist" : "Not on watchlist"}
        <span className="item-tip-slot">#{r.rank} · {r.label || "—"}</span>
      </div>
      {r.screen_flag !== "none" && <div className="item-tip-screen">⊘ {r.screen_flag}</div>}
      {r.basis && <div className="item-tip-stat pool-basis">Source: {r.basis}{r.label ? ` · ${r.label}` : ""}</div>}
      {r.appearances && r.appearances.length > 1 && (
        <div className="item-tip-stat pool-mix">
          also: {r.appearances.slice(1).map((a) => `${a.source === "ladder" ? "ladder" : "LB"} ${a.level}${a.cut ? ` × ${a.cut}` : ""} ${a.label ? `${a.label} ` : ""}${usd(a.median_tc)}`).join(" · ")}
        </div>
      )}
      <div className="item-tip-stat">
        Median TC {usd(r.median_tc)}
        {basePct != null && (
          <span> · base ilvl {lvl(basePct)} vs the anchor ({usd(anchor)})</span>
        )}
      </div>
      {r.base != null && (
        <div className="item-tip-stat pool-mix">
          base {usd(r.base)} · stock {r.stock != null ? usd(r.stock) : "—"} · bonus {r.bonus ? usd(r.bonus) : "—"}
        </div>
      )}
      {r.modifiers.length > 0 && (
        <ul className="pool-mods">
          {r.modifiers.map((m) => (
            <li key={m.key} className={m.value >= 0 ? "enchant" : "debuff"}>
              <span className="pool-mod-val">{lvl(m.value)}</span> {m.name}
              <em className="pool-mod-how">{m.how}</em>
            </li>
          ))}
          {r.set_bonuses.map((b) => (
            <li key={`set-${b.family}`} className="setbonus">
              <span className="pool-mod-val">+{b.bonus}</span> {b.name} ({b.have}/{b.count})
            </li>
          ))}
        </ul>
      )}
      {r.capped && r.capped_at && <div className="item-tip-warn">Capped at {r.capped_at} — one tier from the base</div>}
      <div className="item-tip-flavor">Agency: {r.agency}</div>
      <div className="item-tip-source">{constitution}</div>
    </div>,
    document.body,
  );
}

export default function PoolTiles({ data }: { data: PoolData }) {
  const nav = useNav();
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ slug: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [rarityFilter, setRarityFilter] = useState<string | null>(null);
  // the merged pool's filters: source · cut · level — each over the row's
  // APPEARANCES so a company seen on two cuts stays visible under either filter
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [cutFilter, setCutFilter] = useState<string | null>(null);
  const [levelFilter, setLevelFilter] = useState<string | null>(null);
  const rows = data?.rows ?? [];
  const gated = data?.screen_gated ?? [];

  if (rows.length === 0 && gated.length === 0) {
    return (
      <p className="projections-empty">
        No pool survey yet — <code>hn career pool --write</code> persists a cut into{" "}
        <code>role-hunt/pool/</code>; this tab reads the newest.
      </p>
    );
  }

  const counts: Record<string, number> = {};
  for (const r of rows) if (r.rarity) counts[r.rarity] = (counts[r.rarity] ?? 0) + 1;
  const apps = (r: PoolRow) => (r.appearances && r.appearances.length > 0 ? r.appearances : [{ source: r.source ?? "leaderboard", cut: (r.cut ?? "").split("/")[1] ?? "", level: (r.cut ?? "").split("/")[0] ?? "", label: r.label ?? "", median_tc: r.median_tc }]);
  const passes = (r: PoolRow) =>
    (!rarityFilter || r.rarity === rarityFilter)
    && (!sourceFilter || apps(r).some((a) => a.source === sourceFilter))
    && (!cutFilter || apps(r).some((a) => a.cut === cutFilter))
    && (!levelFilter || apps(r).some((a) => a.level === levelFilter));
  const visible = rows.filter(passes);
  const merged = data.merged === true;
  const cuts = Array.from(new Set(rows.flatMap((r) => apps(r).map((a) => a.cut)))).filter(Boolean);
  const levels = Array.from(new Set(rows.flatMap((r) => apps(r).map((a) => a.level)))).filter(Boolean);
  const chip = (label: string, active: boolean, onClick: () => void, cls = "") => (
    <button type="button" key={label} className={`projection-chip pool-filter ${cls}${active ? " active" : ""}`} onClick={onClick} aria-pressed={active}>
      {label}
    </button>
  );
  const all = [...rows, ...gated];
  const openFor = (r: PoolRow) => {
    // the vault's research entry when the ENGINE found one (`doc` is an existence check server-side),
    // else the levels.fyi company page in a new tab
    if (r.doc) setOpenDoc(r.doc);
    else window.open(levelsUrl(r.slug), "_blank", "noreferrer");
  };

  const tile = (r: PoolRow) => {
    const show = (el: HTMLElement) => setHover({ slug: r.slug, pos: tipPosition(el.getBoundingClientRect()) });
    const rar = r.screened_out ? "gated" : (r.rarity ?? "common");
    return (
      <button
        key={r.slug}
        className={`projection-tile pool-tile rarity-${rar}${r.screened_out ? " gated" : ""}`}
        onClick={(e) => {
          if (coarsePointer() && armed !== r.slug) {
            setArmed(r.slug);
            show(e.currentTarget);
            return;
          }
          openFor(r);
        }}
        onMouseEnter={(e) => show(e.currentTarget)}
        onMouseLeave={() => setHover(null)}
        onFocus={(e) => show(e.currentTarget)}
        onBlur={() => { setHover(null); setArmed(null); }}
        aria-label={
          r.screened_out
            ? `${r.company} — screen-gated (${r.screen_flag}), median ${usd(r.median_tc)}`
            : r.rarity === "unranked"
              ? `${r.company} — unranked (no band row), median ${usd(r.median_tc)}; click to open the research entry`
              : `${r.company} — item level ${r.final_ilvl}, ${r.rarity}; click to open the research entry`
        }
      >
        <div className="projection-head">
          <strong className="projection-sym">{r.company}</strong>
          {r.on_watchlist && <span className="projection-held">WATCHLIST</span>}
          {r.screen_flag !== "none" && <span className="projection-screened" title={r.screen_flag}>⊘</span>}
          {r.final_ilvl != null && <span className="projection-ilvl">ilvl {r.final_ilvl}{r.capped ? " ⌈" : ""}</span>}
        </div>
        <div className="projection-year">
          <b>{usd(r.median_tc)}</b>
          {r.base_ilvl != null && (
            <>
              {" · base "}
              <span className={r.base_ilvl >= 0 ? "pos" : "neg"}>{lvl(r.base_ilvl)}</span>
              {r.modifier_sum !== 0 && (
                <>
                  {" · mods "}
                  <span className={r.modifier_sum >= 0 ? "pos" : "neg"}>{lvl(r.modifier_sum)}</span>
                </>
              )}
            </>
          )}
        </div>
        <div className="projection-sub">
          {merged && r.source && (
            <span className={`pool-source pool-source-${r.source}`}>{r.source === "ladder" ? "LADDER" : "LB"}{r.cut ? ` · ${r.cut.replace("/", " × ")}` : ""}</span>
          )}
          {!merged && `#${r.rank} · `}{r.label || "—"}
          {r.rarity === "unranked" && <span className="pool-unranked">no band row</span>}
          {r.screen_flag !== "none" && ` · ${r.screen_flag}`}
          {r.bench && <span className="pool-bench">BENCH{r.ticker ? ` ${r.ticker}` : ""}</span>}
          {r.openings && r.openings.openings > 0 && (
            // → Career ▸ Openings, filtered to this company (the subtab that follows `company`)
            <span role="link" tabIndex={0} className="pool-openings"
                  title={`${r.openings.openings} openings in the latest scan (${r.openings.new} new) — open them`}
                  onClick={(e) => { e.stopPropagation(); nav.navigate({ zone: "dash", follows: "company", pick: r.slug }); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.stopPropagation(); nav.navigate({ zone: "dash", follows: "company", pick: r.slug }); }
                  }}>
              {r.openings.on_profile > 0
                ? `${r.openings.on_profile} on-profile opening${r.openings.on_profile === 1 ? "" : "s"}`
                : `${r.openings.openings} opening${r.openings.openings === 1 ? "" : "s"}`}
            </span>
          )}
        </div>
      </button>
    );
  };

  const c = data.cut;
  return (
    <div className="projections pool">
      <div className="pool-header">
        <span>
          {merged
            ? `the whole pool · ${data.sources?.surveys.length ?? 0} cuts + ${data.sources?.ladders ?? 0} ladders · one item per company · base = best ${data.sources?.band ?? "staff"} number · as of ${data.as_of}`
            : `${c.family.replace(/-/g, " ")} · ${c.level.replace(/-/g, " ")} · ${c.geo.replace(/-/g, " ")} · as of ${data.as_of}`}
        </span>
        <span className="pool-anchor">anchor {usd(data.anchor)}</span>
      </div>
      {merged && (
        <div className="projections-chips pool-filters" aria-label="Filter by source, cut, level">
          {chip("every source", sourceFilter === null, () => setSourceFilter(null))}
          {chip("leaderboard", sourceFilter === "leaderboard", () => setSourceFilter(sourceFilter === "leaderboard" ? null : "leaderboard"))}
          {chip("ladder", sourceFilter === "ladder", () => setSourceFilter(sourceFilter === "ladder" ? null : "ladder"))}
          <span className="pool-filter-sep" />
          {chip("any cut", cutFilter === null, () => setCutFilter(null))}
          {cuts.map((cu) => chip(cu, cutFilter === cu, () => setCutFilter(cutFilter === cu ? null : cu)))}
          <span className="pool-filter-sep" />
          {chip("any level", levelFilter === null, () => setLevelFilter(null))}
          {levels.map((lv) => chip(lv, levelFilter === lv, () => setLevelFilter(levelFilter === lv ? null : lv)))}
        </div>
      )}
      <div className="projections-chips" role="tablist" aria-label="Filter by rarity">
        <button
          type="button"
          className={`projection-chip rarity-all${rarityFilter === null ? " active" : ""}`}
          onClick={() => setRarityFilter(null)}
          aria-pressed={rarityFilter === null}
        >
          all <b>{rows.length}</b>
        </button>
        {RARITY_ORDER.filter((r) => counts[r]).map((r) => (
          <button
            type="button"
            key={r}
            className={`projection-chip rarity-${r}${rarityFilter === r ? " active" : ""}`}
            onClick={() => setRarityFilter(rarityFilter === r ? null : r)}
            aria-pressed={rarityFilter === r}
          >
            {r} <b>{counts[r]}</b>
          </button>
        ))}
      </div>
      <div className="projections-body">
        {[
          { title: "In-play — the item model ran", items: visible },
          { title: "Screen-gated — visible, marked, no arithmetic", items: rarityFilter ? [] : gated },
        ]
          .filter((pane) => pane.items.length > 0)
          .map((pane) => (
            <section key={pane.title} className="projections-pane">
              <h4 className="projections-pane-title">{pane.title}</h4>
              <div className="projections-grid">{pane.items.map(tile)}</div>
            </section>
          ))}
      </div>
      {data.diff && (
        <p className="pool-diff">
          vs {data.diff.prior_as_of}: new {data.diff.new_entrants.join(", ") || "—"} · dropped{" "}
          {data.diff.dropped.join(", ") || "—"} · {data.diff.rank_moves.length} rank moves ·{" "}
          {data.diff.rarity_changes.length} rarity changes
        </p>
      )}
      {hover && (() => {
        const r = all.find((x) => x.slug === hover.slug);
        return r ? <ItemCard r={r} anchor={data.anchor} constitution={data.constitution} pos={hover.pos} /> : null;
      })()}
      <p className="projections-disclaimer">
        {data.constitution} · {data.attribution}
        {" · "}
        <a href={c.url} target="_blank" rel="noreferrer">leaderboard ↗</a>
      </p>
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
