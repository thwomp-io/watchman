// LaddersGrid — the Career ▸ Ladders tab: the cached
// levels.fyi comp ladders as loot cards read through the POOL's rarity primitive. Color carries the
// meaning, as on the Pool tab — so every rung carries its own ENGINE rarity + ilvl (`hn career comp --all
// --json`, per-level `rarity`/`ilvl` through the pool's classifier + bands) and the card is drawn
// as bars: one horizontal bar per rung, filled in its rarity color, scaled within the card, with
// the comp anchor as a vertical line crossing the whole ladder; the applicant's band rows get a
// bright outline; the card's left edge is tinted by the band row's rarity (the Pool tile's grammar)
// so a scan across cards ranks companies without reading. The pay report renders as a PERCENTILE
// STRIP (two rows, p25→p90 as a band, median tick, the anchor marker, the Staff/Principal median
// ticks) with the six bullet lines in an expander.
//
// Bar scale: SQUARE ROOT of median TC within the card: a linear
// scale let an IC8-class outlier at 3.5× the anchor compress the poor/common rungs to slivers; sqrt
// keeps every rung readable while the order and the anchor's position stay truthful. The anchor
// line sits at sqrt(anchor) on the same scale.
//
// Interaction contract = the loot surfaces' (hover/focus a RUNG → the rung card through the portal;
// coarse pointers two-tap; click the card → the research entry / the levels page). The widget
// never classifies: rarity, ilvl, band rows and the card tint are all engine fields.

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";

export interface LadderLevel {
  level: string;
  median_tc: number;
  yoe?: string | null;
  title?: string | null;
  band?: boolean;
  band_name?: string | null;
  band_basis?: string | null;
  vs_anchor_pct?: number | null;
  ilvl?: number | null;
  rarity?: string | null;
}

export interface Ladder {
  company: string;
  slug: string;
  family: string;
  location: string;
  currency: string;
  url: string;
  last_updated?: string;
  median_tc: number | null;
  high_tc?: number | null;
  levels: LadderLevel[];
  mix?: { base?: number; stock?: number; bonus?: number } | null;
  note?: string | null;
  fetched_at: string;
  attribution: string;
  stale: boolean;
  on_watchlist: boolean;
  tier?: string;
  ticker?: string;
  bench: boolean;
  doc?: string | null;
  card_rarity?: string | null;
  card_ilvl?: number | null;
  card_level?: string | null;
  band_basis?: string | null;
  bands?: Record<string, { level: string; basis: string }>;
}

export interface LaddersReport {
  year: number | null;
  anchor_read: string[];
  metro?: { location?: string; median?: number; p25?: number; p75?: number; p90?: number };
  us?: { median?: number; p25?: number; p75?: number; p90?: number };
  levels?: Record<string, { prior?: number | null; current?: number | null; change?: string }>;
  fetched_at?: string;
}

export interface LaddersData {
  ladders: Ladder[];
  tiles?: unknown[];
  report?: LaddersReport | null;
  bands?: Record<string, number>;
  anchor: number | null;
  band_words: string[];
  count: number;
  stale: number;
  attribution: string;
}

const RARITY_RANK: Record<string, number> = { legendary: 4, epic: 3, rare: 2, common: 1, poor: 0 };
const usd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const usdK = (v: number | null | undefined) => (v == null ? "—" : `$${Math.round(v / 1000)}k`);
const lvl = (v: number) => `${v >= 0 ? "+" : ""}${v}`;
const levelsUrl = (l: Ladder) => l.url.replace(/\.md$/, "");

interface TipPos {
  left: number;
  top: number;
}

function tipPosition(rect: DOMRect): TipPos {
  const TIP_W = 300;
  const pad = 10;
  let left = rect.right + pad;
  if (left + TIP_W > window.innerWidth - pad) left = Math.max(pad, rect.left - TIP_W - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - 260));
  return { left, top };
}

function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}

// The sqrt scale (see the header): a rung's bar width as a fraction of the card's widest rung.
function barScale(levels: LadderLevel[], anchor: number | null): (v: number) => number {
  const max = Math.max(...levels.map((l) => l.median_tc), anchor ?? 0, 1);
  const root = Math.sqrt(max);
  return (v: number) => Math.max(0.02, Math.sqrt(Math.max(v, 0)) / root);
}

export function RungCard({ l, lv, anchor, pos }: { l: Ladder; lv: LadderLevel; anchor: number | null; pos: TipPos }) {
  const gap = anchor != null ? lv.median_tc - anchor : null;
  return createPortal(
    <div className={`item-tip rung-tip rarity-${lv.rarity ?? "common"}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{l.company} · {lv.level}</div>
      {lv.ilvl != null && <div className="item-tip-ilvl">Item Level {lv.ilvl} · {lv.rarity}</div>}
      <div className="item-tip-bind">
        {lv.band ? `Your ${lv.band_name ?? "band"} band · ${lv.band_basis ?? "?"}` : "Rung"}
        <span className="item-tip-slot">{lv.yoe ? `${lv.yoe} yoe` : "yoe —"}</span>
      </div>
      <div className="item-tip-stat">Median TC {usd(lv.median_tc)}</div>
      {gap != null && (
        <div className="item-tip-stat">
          <span className={gap >= 0 ? "pos" : "neg"}>{gap >= 0 ? "+" : "−"}{usd(Math.abs(gap))}</span> vs the anchor ({usd(anchor)})
        </div>
      )}
      {lv.title && <div className="item-tip-stat">{lv.title}</div>}
      {l.mix && (
        <div className="item-tip-stat pool-mix">
          ladder mix: base {usd(l.mix.base)} · stock {usd(l.mix.stock)} · bonus {usd(l.mix.bonus)}
        </div>
      )}
      <div className="item-tip-source">{l.attribution}</div>
    </div>,
    document.body,
  );
}

// One percentile row of the strip: p25→p90 as a band on a shared dollar axis, the median tick,
// the anchor marker, and the report's Staff/Principal median ticks.
function PercentileRow({ label, pct, anchor, ticks, axisMax }: {
  label: string;
  pct: { median?: number; p25?: number; p75?: number; p90?: number };
  anchor: number | null;
  ticks: { name: string; value: number }[];
  axisMax: number;
}) {
  const x = (v: number | undefined | null) => (v == null ? null : Math.min(100, (v / axisMax) * 100));
  const p25 = x(pct.p25), p90 = x(pct.p90), med = x(pct.median), p75 = x(pct.p75), anc = x(anchor);
  return (
    <div className="pstrip-row">
      <div className="pstrip-label">{label}</div>
      <div className="pstrip-axis" aria-label={`${label}: p25 ${usdK(pct.p25)} · median ${usdK(pct.median)} · p75 ${usdK(pct.p75)} · p90 ${usdK(pct.p90)}`}>
        {p25 != null && p90 != null && <div className="pstrip-band" style={{ left: `${p25}%`, width: `${p90 - p25}%` }} />}
        {med != null && <div className="pstrip-tick pstrip-median" style={{ left: `${med}%` }} title={`median ${usdK(pct.median)}`} />}
        {p75 != null && <div className="pstrip-tick pstrip-p75" style={{ left: `${p75}%` }} title={`p75 ${usdK(pct.p75)}`} />}
        {ticks.map((t) => {
          const tx = x(t.value);
          return tx == null ? null : (
            <div key={t.name} className="pstrip-tick pstrip-level" style={{ left: `${tx}%` }} title={`${t.name} median ${usdK(t.value)}`}>
              <span>{t.name}</span>
            </div>
          );
        })}
        {anc != null && (
          <div className="pstrip-anchor" style={{ left: `${anc}%` }} title={`anchor ${usdK(anchor)}`}>
            <span>anchor</span>
          </div>
        )}
      </div>
      <div className="pstrip-nums">
        p25 {usdK(pct.p25)} · med {usdK(pct.median)} · p75 {usdK(pct.p75)} · p90 {usdK(pct.p90)}
      </div>
    </div>
  );
}

export default function LaddersGrid({ data }: { data: LaddersData }) {
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ key: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [sortRarity, setSortRarity] = useState(false);
  const ladders = data?.ladders ?? [];

  if (ladders.length === 0) {
    return (
      <p className="projections-empty">
        No cached ladders — <code>hn career comp &lt;company&gt;</code> lands one in{" "}
        <code>role-hunt/comp/</code>; this tab reads the cache.
      </p>
    );
  }

  const rep = data.report;
  const anchor = data.anchor;
  const staff = rep?.levels?.["Staff Engineer"]?.current ?? null;
  const principal = rep?.levels?.["Principal Engineer"]?.current ?? null;
  const ticks = [
    ...(staff ? [{ name: "Staff", value: staff }] : []),
    ...(principal ? [{ name: "Principal", value: principal }] : []),
  ];
  const axisMax = Math.max(
    rep?.metro?.p90 ?? 0, rep?.us?.p90 ?? 0, anchor ?? 0, staff ?? 0, principal ?? 0, 1,
  ) * 1.12;

  const ordered = sortRarity
    ? [...ladders].sort(
        (a, b) => (RARITY_RANK[b.card_rarity ?? "poor"] ?? 0) - (RARITY_RANK[a.card_rarity ?? "poor"] ?? 0)
          || (b.card_ilvl ?? -999) - (a.card_ilvl ?? -999),
      )
    : ladders;

  const rungKey = (l: Ladder, lv: LadderLevel) => `${l.slug}|${l.family}|${l.location}|${lv.level}`;
  const hovered = hover
    ? (() => {
        for (const l of ladders) for (const lv of l.levels) if (rungKey(l, lv) === hover.key) return { l, lv };
        return null;
      })()
    : null;

  return (
    <div className="holdings ladders">
      {rep && (rep.metro || rep.us) && (
        <div className="ladders-report pstrip">
          <div className="ladders-report-head">
            Pay report {rep.year ?? ""} · anchor {usd(anchor)}
            <span className="ladders-report-pct">{rep.metro?.location ?? "metro"} / United States · all levels</span>
          </div>
          {rep.metro && <PercentileRow label={rep.metro.location?.split(",")[0] ?? "Metro"} pct={rep.metro} anchor={anchor} ticks={ticks} axisMax={axisMax} />}
          {rep.us && <PercentileRow label="United States" pct={rep.us} anchor={anchor} ticks={ticks} axisMax={axisMax} />}
          {rep.anchor_read?.length > 0 && (
            <details className="ladders-report-details">
              <summary>the anchor read, in words</summary>
              <ul className="ladders-report-read">
                {rep.anchor_read.map((ln) => (
                  <li key={ln}>{ln}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <div className="holdings-summary">
        <span>
          ladders <b>{data.count}</b>
        </span>
        {data.stale > 0 && (
          <span>
            stale <b>{data.stale}</b>
          </span>
        )}
        {anchor != null && (
          <span>
            anchor <b>{usd(anchor)}</b>
          </span>
        )}
        <span>band rows: {data.band_words.join(" / ")}</span>
        <button
          type="button"
          className={`projection-chip rarity-all ladders-sort${sortRarity ? " active" : ""}`}
          onClick={() => setSortRarity((v) => !v)}
          aria-pressed={sortRarity}
        >
          {sortRarity ? "sorted by rarity" : "watchlist first"}
        </button>
      </div>
      <div className="holdings-grid ladders-grid">
        {ordered.map((l) => {
          const scale = barScale(l.levels, anchor);
          const anchorX = anchor != null && l.levels.length > 0 ? scale(anchor) * 100 : null;
          return (
            <div
              key={`${l.slug}-${l.family}-${l.location}`}
              className={`holding-card ladder-card rarity-${l.card_rarity ?? "common"}${l.stale ? " stale" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                if (l.doc) setOpenDoc(l.doc);
                else window.open(levelsUrl(l), "_blank", "noreferrer");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  if (l.doc) setOpenDoc(l.doc);
                  else window.open(levelsUrl(l), "_blank", "noreferrer");
                }
              }}
              aria-label={`${l.company} — ${l.card_rarity ?? "unclassified"}${l.card_ilvl != null ? `, band item level ${l.card_ilvl}` : ""}; click to open ${l.doc ? "the research entry" : "the levels.fyi page"}`}
            >
              <div className="projection-head">
                <strong className="projection-sym">{l.company}</strong>
                {l.on_watchlist && <span className="projection-held">{l.tier ? l.tier.toUpperCase() : "WATCHLIST"}</span>}
                {l.stale && <em className="stale-chip">stale</em>}
                {l.card_ilvl != null && (
                  <span className="projection-ilvl" title={`band ${l.card_level ?? ""}: ${l.band_basis ?? ""}`}>
                    ilvl {l.card_ilvl}
                  </span>
                )}
              </div>
              <div className="ladder-sub">
                median {usd(l.median_tc)}
                {l.high_tc != null && ` · high ${usd(l.high_tc)}`}
                {l.bench && <span className="pool-bench">BENCH{l.ticker ? ` ${l.ticker}` : ""}</span>}
              </div>
              {l.levels.length > 0 ? (
                <div className="ladder-bars">
                  {anchorX != null && (
                    <div className="ladder-anchor" style={{ left: `calc(${anchorX}% * var(--bars-col) / 100%)` }}>
                      <span>anchor</span>
                    </div>
                  )}
                  {l.levels.map((lv) => {
                    const key = rungKey(l, lv);
                    const show = (el: HTMLElement) => setHover({ key, pos: tipPosition(el.getBoundingClientRect()) });
                    return (
                      <button
                        type="button"
                        key={key}
                        className={`ladder-rung rarity-${lv.rarity ?? "common"}${lv.band ? " band" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (coarsePointer() && armed !== key) {
                            setArmed(key);
                            show(e.currentTarget);
                            return;
                          }
                          if (l.doc) setOpenDoc(l.doc);
                          else window.open(levelsUrl(l), "_blank", "noreferrer");
                        }}
                        onMouseEnter={(e) => show(e.currentTarget)}
                        onMouseLeave={() => setHover(null)}
                        onFocus={(e) => show(e.currentTarget)}
                        onBlur={() => { setHover(null); setArmed(null); }}
                        aria-label={`${lv.level} — median ${usd(lv.median_tc)}, item level ${lv.ilvl ?? "—"}, ${lv.rarity ?? "unclassified"}${lv.band ? ", your band" : ""}`}
                      >
                        <span className="ladder-rung-bar" style={{ width: `${scale(lv.median_tc) * 100}%` }} />
                        <span className="ladder-rung-label">
                          <b>{lv.level}</b> {usdK(lv.median_tc)}
                          {lv.ilvl != null && <em className={lv.ilvl >= 0 ? "pos" : "neg"}> {lvl(lv.ilvl)}</em>}
                          {lv.yoe && <i className="ladder-yoe"> {lv.yoe}y</i>}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="holding-appraisal dim">{l.note ?? "no per-level data"}</div>
              )}
              <div className="ladder-foot">
                {l.card_level && (
                  <span className={`ladder-basis basis-${l.band_basis ?? "none"}`}>
                    band {l.card_level}: {l.band_basis}
                    {l.bands?.principal && ` · principal ${l.bands.principal.level}: ${l.bands.principal.basis}`} ·{" "}
                  </span>
                )}
                {l.mix && (
                  <span>
                    mix {usdK(l.mix.base)} · {usdK(l.mix.stock)} · {usdK(l.mix.bonus)} ·{" "}
                  </span>
                )}
                fetched {l.fetched_at.slice(0, 10)} · {l.attribution}
              </div>
            </div>
          );
        })}
      </div>
      {hovered && hover && <RungCard l={hovered.l} lv={hovered.lv} anchor={anchor} pos={hover.pos} />}
      <p className="projections-disclaimer">
        {data.attribution} · rung colors = the Pool's rarity bands on median TC vs the anchor (sqrt-scaled bars) ·
        refresh a company with <code>hn career comp &lt;name&gt; --refresh</code>
      </p>
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
