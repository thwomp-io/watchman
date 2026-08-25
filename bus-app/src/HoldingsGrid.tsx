// HoldingsGrid — the Holdings tab: the inventory to the Projections tab's shop.
// One card per HELD instrument, sorted by position size (the engine's inventory order), each carrying
// the two appraisal reads the tab exists for: item level from the LIVE price (the projections
// number) and entry item level from the AVERAGE COST — "against the current model, how good was
// my buy?" — with entry_edge (the ilvls the entry banked) as the delta. Unappraised rows (fund
// structures / registry gaps) render dim with the reason named — honest, never hidden.
//
// Interaction contract is ProjectionTiles' verbatim (one pattern, every loot surface): hover or
// focus → the portal-rendered item card; coarse pointers are two-tap (tap 1 shows the card,
// tap 2 opens the research doc); click → DocPopup on the provenance artifact. Rarity + levels
// are ENGINE fields — the UI never re-derives them (one classification, every surface).

import { useState } from "react";
import { createPortal } from "react-dom";
import DocPopup from "./DocPopup";
import type { HorizonRow } from "./ProjectionTiles";

export interface Holding {
  symbol: string;
  name: string;
  account: string | null;
  valuation: string | null;
  shares: number;
  avg_cost: number | null;
  cost_basis: number | null;
  price: number | null;
  value: number;
  weight_pct: number;
  unrealized_gl?: number;
  unrealized_gl_pct?: number;
  appraised: boolean;
  unappraised_reason?: string;
  item_level?: number;
  rarity?: string;
  entry_item_level?: number;
  entry_rarity?: string;
  entry_edge?: number;
  paid_multiple?: number | null;
  fwd_multiple_now?: number | null;
  stale?: boolean;
  screen_note?: string | null;
  eps_basis?: string;
  as_of?: string;
  provenance?: string;
  grid?: HorizonRow[];
}

export interface HoldingsData {
  holdings: Holding[];
  summary?: {
    invested_total: number;
    count: number;
    appraised: number;
    unrealized_gl_total?: number;
    by_rarity?: Record<string, number>;
    best_entry?: { symbol: string; entry_item_level: number };
    weakest_entry?: { symbol: string; entry_item_level: number };
  };
  disclaimer?: string;
}

const pct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
const lvl = (v: number) => `${v >= 0 ? "+" : ""}${v}`;
const usd = (v: number) =>
  `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

interface TipPos {
  left: number;
  top: number;
}

function tipPosition(rect: DOMRect): TipPos {
  const TIP_W = 320;
  const pad = 10;
  let left = rect.right + pad;
  if (left + TIP_W > window.innerWidth - pad) left = Math.max(pad, rect.left - TIP_W - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - 420));
  return { left, top };
}

function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}

function AppraisalTip({ h, pos }: { h: Holding; pos: TipPos }) {
  return createPortal(
    <div className={`item-tip rarity-${h.rarity ?? "common"}`} style={{ left: pos.left, top: pos.top }} role="tooltip">
      <div className="item-tip-name">{h.symbol}</div>
      {h.item_level != null && <div className="item-tip-ilvl">Item Level {h.item_level}</div>}
      <div className="item-tip-bind">
        {`${h.shares.toLocaleString()} sh`}
        {h.avg_cost != null && ` @ $${h.avg_cost.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
        <span className="item-tip-slot">{h.weight_pct.toFixed(1)}% of book</span>
      </div>
      {h.screen_note && <div className="item-tip-screen">⊘ {h.screen_note}</div>}
      <div className="item-tip-stat">
        {usd(h.value)}
        {h.unrealized_gl_pct != null && (
          <span className={h.unrealized_gl_pct >= 0 ? "pos" : "neg"}> ({pct(h.unrealized_gl_pct)})</span>
        )}
      </div>
      {h.entry_item_level != null && (
        <div className="item-tip-stat">
          Entry appraisal {lvl(h.entry_item_level)} ({h.entry_rarity})
          {h.entry_edge != null && (
            <span className={h.entry_edge >= 0 ? "pos" : "neg"}> · edge {lvl(h.entry_edge)}</span>
          )}
        </div>
      )}
      {h.paid_multiple != null && h.fwd_multiple_now != null && (
        <div className="item-tip-stat">
          Paid {h.paid_multiple}x forward · market now {h.fwd_multiple_now}x
        </div>
      )}
      {h.grid && (
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
            {h.grid.map((row) => (
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
      )}
      {h.stale && <div className="item-tip-warn">Params stale — re-anchor at the next print</div>}
      {h.eps_basis && <div className="item-tip-flavor">"{h.eps_basis}"</div>}
      {h.provenance && (
        <div className="item-tip-source">{h.provenance.split("/").pop()} · as of {h.as_of}</div>
      )}
    </div>,
    document.body,
  );
}

export default function HoldingsGrid({ data }: { data: HoldingsData }) {
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [hover, setHover] = useState<{ symbol: string; pos: TipPos } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const rows = data?.holdings ?? [];

  if (rows.length === 0) {
    return (
      <p className="projections-empty">
        No holdings on record — positions come from the portfolio seed; appraisals from{" "}
        <code>finance/reference/projection-params.yaml</code>.
      </p>
    );
  }

  const s = data?.summary;
  return (
    <div className="holdings">
      {s && (
        <div className="holdings-summary">
          <span>
            invested <b>{usd(s.invested_total)}</b>
          </span>
          <span>
            appraised <b>{s.appraised}/{s.count}</b>
          </span>
          {s.unrealized_gl_total != null && (
            <span className={s.unrealized_gl_total >= 0 ? "pos" : "neg"}>
              open {usd(s.unrealized_gl_total)}
            </span>
          )}
          {s.best_entry && (
            <span>
              best entry <b>{s.best_entry.symbol} {lvl(s.best_entry.entry_item_level)}</b>
            </span>
          )}
          {s.weakest_entry && (
            <span>
              weakest <b>{s.weakest_entry.symbol} {lvl(s.weakest_entry.entry_item_level)}</b>
            </span>
          )}
        </div>
      )}
      <div className="holdings-grid">
        {rows.map((h) => {
          const show = (el: HTMLElement) =>
            setHover({ symbol: h.symbol, pos: tipPosition(el.getBoundingClientRect()) });
          const appraisal = h.appraised ? (
            <div className="holding-appraisal">
              ilvl <b>{h.item_level}</b>
              {" · entry "}
              <b>{h.entry_item_level}</b>
              {h.entry_edge != null && (
                <span className={h.entry_edge >= 0 ? "pos" : "neg"}> ({lvl(h.entry_edge)})</span>
              )}
              {h.stale && <em className="stale-chip">stale</em>}
            </div>
          ) : (
            <div className="holding-appraisal dim">{h.unappraised_reason}</div>
          );
          return (
            <button
              key={h.symbol}
              className={`holding-card rarity-${h.appraised ? h.rarity : "unappraised"}${h.appraised ? "" : " unappraised"}`}
              onClick={(e) => {
                if (coarsePointer() && armed !== h.symbol) {
                  setArmed(h.symbol);
                  show(e.currentTarget);
                  return;
                }
                if (h.provenance) setOpenDoc(h.provenance);
              }}
              onMouseEnter={(e) => show(e.currentTarget)}
              onMouseLeave={() => setHover(null)}
              onFocus={(e) => show(e.currentTarget)}
              onBlur={() => { setHover(null); setArmed(null); }}
              aria-label={
                h.appraised
                  ? `${h.symbol} — ${h.weight_pct.toFixed(1)}% of book, item level ${h.item_level}, entry ${h.entry_item_level}; click to open the research artifact`
                  : `${h.symbol} — ${h.weight_pct.toFixed(1)}% of book, unappraised (${h.unappraised_reason})`
              }
            >
              <div className="projection-head">
                <strong className="projection-sym">{h.symbol}</strong>
                {h.screen_note && <span className="projection-screened" title={h.screen_note}>⊘</span>}
                <span className="holding-weight">{h.weight_pct.toFixed(1)}%</span>
              </div>
              <div className="holding-line">
                {usd(h.value)}
                {h.unrealized_gl_pct != null && (
                  <span className={h.unrealized_gl_pct >= 0 ? "pos" : "neg"}> {pct(h.unrealized_gl_pct)}</span>
                )}
              </div>
              {appraisal}
            </button>
          );
        })}
      </div>
      {hover && (() => {
        const h = rows.find((r) => r.symbol === hover.symbol);
        return h ? <AppraisalTip h={h} pos={hover.pos} /> : null;
      })()}
      {data?.disclaimer && <p className="projections-disclaimer">{data.disclaimer}</p>}
      {openDoc && <DocPopup doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
