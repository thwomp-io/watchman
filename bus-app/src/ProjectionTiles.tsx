// ProjectionTiles — the Projections tab:
// one loot-card tile per name, classified by the ENGINE's rarity ladder (12mo mid-corner return:
// rare ≈ conservative ballast · epic · legendary — the "hunt by item level" convention). Hover or
// focus a tile → the scenario grid as an item-description tooltip: rarity-colored name, item-level
// line, white stat lines, the horizon table in effect-green/red, gold flavor text carrying the
// EPS basis + provenance. Click → DocPopup on the research artifact.
//
// The tooltip renders through a PORTAL at document.body with fixed positioning from the tile's
// rect — never clipped by widget overflow (an in-tile absolute tooltip clips against the widget
// container). The panel is self-contained dark (its own bg, like the noir SVGs) so it reads
// identically on every console theme. Data contract = `hn finance projections --json`; rarity and
// item_level are ENGINE fields — the UI never re-derives them (one classification, every surface).

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";

export interface HorizonRow {
  horizon: string;
  years: number;
  tier: "estimate" | "sketch";
  price: { low: number; mid: number; high: number };
  return_pct: { low: number; mid: number; high: number };
}

export interface Projection {
  symbol: string;
  price: number;
  price_source: "live" | "ref";
  fwd_eps: number;
  eps_basis: string;
  fwd_multiple_now: number | null;
  growth_pct_low: number;
  growth_pct_high: number;
  mult_low: number;
  mult_mid: number;
  mult_high: number;
  as_of: string;
  provenance: string;
  notes: string | null;
  screen_note: string | null;
  stale: boolean;
  held: boolean;
  item_level: number;
  rarity: string; // legendary | epic | rare | common | poor (engine ladder)
  basis?: number;
  basis_return_pct?: number;
  grid: HorizonRow[];
}

export interface ProjectionData {
  projections: Projection[];
  summary?: { total: number; held: number; stale: number; by_rarity?: Record<string, number> };
  disclaimer?: string;
}

const RARITY_ORDER = ["legendary", "epic", "rare", "common", "poor"];
const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

interface TipPos {
  left: number;
  top: number;
}

// Anchor the tooltip beside the tile like an inventory hover: right side by default, flipped
// left when the viewport edge is near; clamped vertically. Fixed positioning = immune to any
// ancestor overflow/scroll container (the whole point of the portal).
function tipPosition(rect: DOMRect): TipPos {
  const TIP_W = 320;
  const pad = 10;
  let left = rect.right + pad;
  if (left + TIP_W > window.innerWidth - pad) left = Math.max(pad, rect.left - TIP_W - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - 380));
  return { left, top };
}

function ItemTooltip({ p, pos }: { p: Projection; pos: TipPos }) {
  return createPortal(
    <div className={`item-tip rarity-${p.rarity}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{p.symbol}</div>
      <div className="item-tip-ilvl">Item Level {p.item_level}</div>
      <div className="item-tip-bind">
        {p.held ? "Status: Held" : "Status: Watchlist"}
        <span className="item-tip-slot">{p.price_source === "ref" ? "Ref price" : "Live"}</span>
      </div>
      {p.screen_note && <div className="item-tip-screen">⊘ {p.screen_note}</div>}
      <div className="item-tip-stat">
        ${p.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
        {p.fwd_multiple_now != null && ` · ${p.fwd_multiple_now}x forward`}
        {` on $${p.fwd_eps}/sh`}
      </div>
      <div className="item-tip-stat">
        +{p.growth_pct_low}–{p.growth_pct_high}% Earnings Growth /yr
      </div>
      <div className="item-tip-stat">
        Multiple band {p.mult_low} / {p.mult_mid} / {p.mult_high}x
      </div>
      {p.held && p.basis != null && (
        <div className="item-tip-stat">
          Basis ${p.basis.toLocaleString(undefined, { minimumFractionDigits: 2 })}
          {p.basis_return_pct != null && (
            <span className={p.basis_return_pct >= 0 ? "pos" : "neg"}> ({pct(p.basis_return_pct)})</span>
          )}
        </div>
      )}
      <table className="item-tip-grid">
        <thead>
          <tr>
            <th />
            <th>Low</th>
            <th>Mid</th>
            <th>High</th>
          </tr>
        </thead>
        <tbody>
          {p.grid.map((row) => (
            <tr key={row.horizon} className={row.tier === "sketch" ? "tier-sketch" : ""}>
              <td>
                {row.horizon}
                {row.tier === "sketch" && <em className="tier-chip">sketch</em>}
              </td>
              {(["low", "mid", "high"] as const).map((c) => (
                <td key={c} className={row.return_pct[c] >= 0 ? "pos" : "neg"}>
                  {pct(row.return_pct[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {p.stale && <div className="item-tip-warn">Params stale — re-anchor at the next print</div>}
      <div className="item-tip-flavor">
        "{p.eps_basis}"
      </div>
      <div className="item-tip-source">{p.provenance.split("/").pop()} · as of {p.as_of}</div>
    </div>,
    document.body,
  );
}

// Touch has no hover, and the item card (the scenario grid, the whole read) is hover-born — so on
// coarse pointers a tile is a TWO-TAP control: first tap shows the item card, second tap on the
// same tile opens the research doc. Fine pointers keep hover=card /
// click=doc. Queried per-render (cheap) so a desktop touchscreen flip mid-session behaves.
function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}

export default function ProjectionTiles({ data }: { data: ProjectionData }) {
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ symbol: string; pos: TipPos } | null>(null);
  // The two-tap arm state: which symbol the last coarse-pointer tap landed on (its card is up).
  const [armed, setArmed] = useState<string | null>(null);
  // Rarity filter: null = All. Clicking the active tier toggles back to All.
  const [rarityFilter, setRarityFilter] = useState<string | null>(null);
  const tiles = data?.projections ?? [];

  if (tiles.length === 0) {
    return (
      <p className="projections-empty">
        No projection params yet — entries are authored in{" "}
        <code>finance/reference/projection-params.yaml</code> at research/grade time.
      </p>
    );
  }

  // Counts come from the tiles themselves so the chips always agree with what's filterable
  // (summary.by_rarity is the engine's copy of the same census — kept as the contract, not the source).
  const counts: Record<string, number> = {};
  for (const t of tiles) counts[t.rarity] = (counts[t.rarity] ?? 0) + 1;
  const visible = rarityFilter ? tiles.filter((t) => t.rarity === rarityFilter) : tiles;

  return (
    <div className="projections">
      <div className="projections-chips" role="tablist" aria-label="Filter by rarity">
        <button
          type="button"
          className={`projection-chip rarity-all${rarityFilter === null ? " active" : ""}`}
          onClick={() => setRarityFilter(null)}
          aria-pressed={rarityFilter === null}
        >
          all <b>{tiles.length}</b>
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
      {[
        // Tri-pane scan order (held book first, then the unheld screen-clear names, then
        // the screen-gated landscape). A held name always ranks as held even if it carries a screen note.
        { title: "Held positions", items: visible.filter((t) => t.held) },
        { title: "In-play targets — unheld, screen-clear", items: visible.filter((t) => !t.held && !t.screen_note) },
        { title: "Screen-gated — screened-out or undecided (research only)", items: visible.filter((t) => !t.held && t.screen_note) },
      ]
        .filter((pane) => pane.items.length > 0)
        .map((pane) => (
          <section key={pane.title} className="projections-pane">
            <h4 className="projections-pane-title">{pane.title}</h4>
            <div className="projections-grid">
              {pane.items.map((p) => {
          const show = (el: HTMLElement) => setHover({ symbol: p.symbol, pos: tipPosition(el.getBoundingClientRect()) });
          return (
            <button
              key={p.symbol}
              className={`projection-tile rarity-${p.rarity}${p.stale ? " stale" : ""}`}
              onClick={(e) => {
                // Two-tap on touch: tap 1 arms the tile + shows the item card, tap 2 opens the doc.
                if (coarsePointer() && armed !== p.symbol) {
                  setArmed(p.symbol);
                  show(e.currentTarget);
                  return;
                }
                setOpenDoc(p.provenance);
              }}
              onMouseEnter={(e) => show(e.currentTarget)}
              onMouseLeave={() => setHover(null)}
              onFocus={(e) => show(e.currentTarget)}
              onBlur={() => { setHover(null); setArmed(null); }}
              aria-label={`${p.symbol} — item level ${p.item_level}, ${p.rarity}; click to open the research artifact`}
            >
              <div className="projection-head">
                <strong className="projection-sym">{p.symbol}</strong>
                {p.held && <span className="projection-held">HELD</span>}
                {p.screen_note && <span className="projection-screened" title={p.screen_note}>⊘</span>}
                <span className="projection-ilvl">ilvl {p.item_level}</span>
              </div>
              <div className="projection-year">
                {(() => {
                  const yr = p.grid.find((r) => r.horizon === "12mo");
                  return yr ? (
                    <>
                      12mo{" "}
                      <span className={yr.return_pct.low >= 0 ? "pos" : "neg"}>{pct(yr.return_pct.low)}</span>
                      {" … "}
                      <b className={yr.return_pct.mid >= 0 ? "pos" : "neg"}>{pct(yr.return_pct.mid)}</b>
                      {" … "}
                      <span className={yr.return_pct.high >= 0 ? "pos" : "neg"}>{pct(yr.return_pct.high)}</span>
                    </>
                  ) : null;
                })()}
              </div>
              <div className="projection-sub">
                ${p.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                {p.fwd_multiple_now != null && ` · fwd ${p.fwd_multiple_now}x`}
                {p.stale && <em className="stale-chip">stale</em>}
              </div>
            </button>
              );
              })}
            </div>
          </section>
        ))}
      {hover && (() => {
        const p = tiles.find((t) => t.symbol === hover.symbol);
        return p ? <ItemTooltip p={p} pos={hover.pos} /> : null;
      })()}
      {data?.disclaimer && <p className="projections-disclaimer">{data.disclaimer}</p>}
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
