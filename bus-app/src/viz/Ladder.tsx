// The trap-map ladder — the interactive twin of `hn finance trap-map --json`.
// v3 CHUNKY pass (field feedback): the damage-meter aesthetic. The v2 true-to-scale
// price axis rendered mostly empty canvas (the price GAPS were the dead space the operator
// flagged); v3 collapses the axis into a DENSE STACK OF FAT BARS — every row a full-width
// colored strip carrying its data ON the bar, near-zero dead space. Ladder ORDER is preserved
// (rows sort by price, the live-price strip slots at its ordinal spot — the mental model the
// v2 usability feedback ratified), and the gap information the axis used to encode spatially
// now rides each rung's distance-% chip. Bar LENGTH stays committed-$ (map-normalized, with a
// chunk floor so every bar reads fat); per-symbol categorical accents + a vertical bevel
// gradient give the bank its color identity; hot rungs (≤2% to fill) pulse amber.
// Data contract unchanged: { as_of, committed, symbols:[{ symbol, price?, prev_close?,
// day_change_pct?, rungs:[...], supports:[{level,touches}], lo, hi }], notes[] }.

import type React from "react";
import { fmtNum, useCatColors } from "./common";

interface Rung {
  side: string; qty: number; limit: number; value: number;
  distance_pct?: number | null; expires?: string; note?: string;
}
interface Shelf { level: number; touches: number }
interface SymbolLadder {
  symbol: string; price?: number | null; prev_close?: number | null;
  day_change_pct?: number | null; rungs: Rung[]; supports: Shelf[]; lo: number; hi: number;
}
export interface LadderData {
  as_of: string; committed?: number; symbols: SymbolLadder[]; notes?: string[];
}

const HOT_PCT = 2;        // ≤ this % to fill = hot (amber + glow)
const FILL_FLOOR = 0.42;  // min bar-length share — nothing renders skinny in chunky mode

// On-bar text color, computed from the accent itself (11-theme fleet: light themes run deep-ink
// accents, dark themes run bright phosphors — a luminance cut beats per-theme CSS overrides).
function textOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#101314";
  const n = parseInt(m[1], 16);
  const lum = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.52 ? "#101314" : "#f6f3ea";
}

type Row =
  | { kind: "price"; price: number }
  | { kind: "rung"; price: number; rung: Rung }
  | { kind: "shelf"; price: number; shelf: Shelf };

function OneLadder({ lad, maxValue, accent }: { lad: SymbolLadder; maxValue: number; accent: string }) {
  const dayCls = (lad.day_change_pct ?? 0) > 0 ? "pos" : (lad.day_change_pct ?? 0) < 0 ? "neg" : "";

  // One merged stack, ladder order (price descending) — the axis, densified.
  const rows: Row[] = [
    ...(lad.price != null ? [{ kind: "price", price: lad.price } as Row] : []),
    ...lad.rungs.map((r): Row => ({ kind: "rung", price: r.limit, rung: r })),
    ...lad.supports.map((s): Row => ({ kind: "shelf", price: s.level, shelf: s })),
  ].sort((a, b) => b.price - a.price);

  return (
    <div className="ladder-col">
      <div className="ladder-head" style={{ background: `linear-gradient(180deg, color-mix(in srgb, ${accent} 30%, transparent), color-mix(in srgb, ${accent} 14%, transparent))`, borderLeft: `3px solid ${accent}` }}>
        <span className="ladder-sym">{lad.symbol}</span>
        {lad.price != null
          ? <span className={`ladder-day ${dayCls}`}>
              {lad.day_change_pct != null &&
                `${lad.day_change_pct > 0 ? "+" : ""}${lad.day_change_pct.toFixed(1)}%`}
            </span>
          : <span className="ladder-day dim">unquotable</span>}
      </div>
      <div className="ladder-stack">
        {rows.map((row, i) => {
          if (row.kind === "price") {
            return (
              <div key={`p${i}`} className="ladder-price-strip">
                <span className="ladder-price-caret">▶</span>
                <span className="ladder-price-num">{fmtNum(row.price)}</span>
                <span className="ladder-price-tag">LIVE</span>
              </div>
            );
          }
          if (row.kind === "rung") {
            const r = row.rung;
            const share = maxValue > 0 ? r.value / maxValue : 0;
            const w = (FILL_FLOOR + (1 - FILL_FLOOR) * share) * 100;
            // hot = ≤2% to fill — INCLUDING in-the-money rungs (negative distance = fills at
            // the next open; the hottest state on the board, eye-caught v3b)
            const hot = r.distance_pct != null && r.distance_pct <= HOT_PCT;
            const title = `${r.side.toUpperCase()} ${r.qty} @ $${fmtNum(r.limit)} · $${fmtNum(r.value)} committed`
              + (r.distance_pct != null ? ` · ${r.distance_pct.toFixed(1)}% to fill` : "")
              + (r.expires ? ` · expires ${r.expires}` : "") + (r.note ? `\n${r.note}` : "");
            return (
              <div key={`r${i}`} className={`ladder-rung-row${hot ? " hot" : ""}`} title={title}>
                <div
                  className={`ladder-rung-bar ${r.side}${hot ? " hot" : ""}`}
                  style={{
                    width: `${w}%`,
                    // hot bars KEEP the lane accent (operator feedback: amber fill broke the
                    // per-symbol color identity) — heat rides the glow, a brighter top mix,
                    // and the amber distance chip
                    background: `linear-gradient(180deg, color-mix(in srgb, ${accent} ${hot ? 58 : 72}%, white) 0%, ${accent} 42%, color-mix(in srgb, ${accent} 68%, black) 100%)`,
                    borderColor: `color-mix(in srgb, ${accent} 55%, black)`,
                    ...({ "--hot-c": accent } as React.CSSProperties),
                  }}
                >
                  <span className="ladder-rung-qty" style={{ color: textOn(accent) }}>{`${r.qty} @ ${fmtNum(r.limit)}`}</span>
                  <span className="ladder-rung-val" style={{ color: `color-mix(in srgb, ${textOn(accent)} 72%, transparent)` }}>{`$${fmtNum(r.value)}`}</span>
                </div>
                {r.distance_pct != null && (
                  <span className={`ladder-dist${hot ? " hot" : ""}`}>
                    {`${r.distance_pct > 0 ? "−" : "+"}${Math.abs(r.distance_pct).toFixed(1)}%`}
                  </span>
                )}
              </div>
            );
          }
          const s = row.shelf;
          return (
            <div key={`s${i}`} className="ladder-shelf-row" title={`support $${fmtNum(s.level)} · ${s.touches} touch${s.touches === 1 ? "" : "es"}`}>
              <span className="ladder-shelf-level">{`$${fmtNum(s.level)} ×${s.touches}`}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Ladder({ data }: { data: LadderData }) {
  const COLORS = useCatColors();
  const symbols = data.symbols ?? [];
  if (symbols.length === 0) {
    return <p className="empty">NO RESTING ORDERS — THE SLATE IS EMPTY</p>;
  }
  const maxValue = Math.max(...symbols.flatMap((s) => s.rungs.map((r) => r.value)), 0);
  return (
    <div className="ladder-bank">
      {data.committed != null && data.committed > 0 && (
        <div className="ladder-committed">Σ ${fmtNum(data.committed)} committed by resting buys · bar length = committed $</div>
      )}
      <div className="ladder-row">
        {symbols.map((lad, i) => (
          <OneLadder key={lad.symbol} lad={lad} maxValue={maxValue} accent={COLORS[i % COLORS.length]} />
        ))}
      </div>
    </div>
  );
}
