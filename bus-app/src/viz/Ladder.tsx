// The trap-map ladder — the interactive twin of `hn finance trap-map --json`.
// v2 BOLD pass (field feedback): rungs are VALUE-SCALED BARS
// (length ∝ committed dollars, normalized across the whole map — "where's the big money waiting"
// as a pre-attentive read), the live price is a heavy bar + a filled PILL with big numerals, and
// a DESCENT CORRIDOR (gradient band on the rail) paints the distance from the price down to the
// deepest buy rung. Hot rungs (≤2% to fill) glow. Shelves stay deliberately faint — context,
// never the subject (hierarchy: price > rungs > shelves).
// Data contract unchanged: { as_of, committed, symbols:[{ symbol, price?, prev_close?,
// day_change_pct?, rungs:[...], supports:[{level,touches}], lo, hi }], notes[] }.

import { useMeasure, fmtNum } from "./common";

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

const LH = 300;          // per-ladder plot height (px)
const RAIL = 58;         // the rail's x
const BAR_MIN = 30;      // smallest rung bar (px)
const BAR_MAX = 130;     // biggest rung bar — the map's largest committed value
const HOT_PCT = 2;       // ≤ this % to fill = hot (glow + bold)

function OneLadder({ lad, maxValue }: { lad: SymbolLadder; maxValue: number }) {
  const span = Math.max(lad.hi - lad.lo, 0.01);
  const y = (price: number) => 10 + ((lad.hi - price) / span) * (LH - 20);
  const dayCls = (lad.day_change_pct ?? 0) > 0 ? "pos" : (lad.day_change_pct ?? 0) < 0 ? "neg" : "";
  const gid = `corridor-${lad.symbol}`;
  const buys = lad.rungs.filter((r) => r.side === "buy");
  const deepest = buys.length ? Math.min(...buys.map((r) => r.limit)) : null;

  // Label de-clutter (the eye's first-render catch): rung labels always render; shelf LINES
  // always render; shelf LABELS render top-4 by touches, ≥12px clear of every labeled row.
  const taken: number[] = lad.rungs.map((r) => y(r.limit));
  if (lad.price != null) taken.push(y(lad.price));
  const labeledShelves = new Set(
    [...lad.supports]
      .sort((a, b) => b.touches - a.touches)
      .slice(0, 4)
      .filter((s) => {
        const yy = y(s.level);
        if (taken.some((t) => Math.abs(t - yy) < 12)) return false;
        taken.push(yy);
        return true;
      }),
  );

  return (
    <div className="ladder-col">
      <div className="ladder-head">
        <span className="ladder-sym">{lad.symbol}</span>
        {lad.price != null
          ? <span className={`ladder-day ${dayCls}`}>
              {lad.day_change_pct != null &&
                `${lad.day_change_pct > 0 ? "+" : ""}${lad.day_change_pct.toFixed(1)}%`}
            </span>
          : <span className="ladder-day dim">unquotable</span>}
      </div>
      <svg className="ladder-svg" viewBox={`0 0 200 ${LH}`} preserveAspectRatio="xMidYMid meet">
        <defs>
          {/* the descent corridor: live price fading down into the trap zone */}
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--amber, #e8a33d)" stopOpacity="0.45" />
            <stop offset="1" stopColor="var(--amber, #e8a33d)" stopOpacity="0.06" />
          </linearGradient>
          <filter id={`glow-${lad.symbol}`} x="-40%" y="-40%" width="180%" height="180%">
            <feDropShadow dx="0" dy="0" stdDeviation="2.2" floodColor="var(--amber, #e8a33d)" floodOpacity="0.85" />
          </filter>
        </defs>
        {/* the rail */}
        <line x1={RAIL} y1={6} x2={RAIL} y2={LH - 6} className="ladder-rail" />
        {/* the descent corridor — price → deepest buy rung */}
        {lad.price != null && deepest != null && deepest < lad.price && (
          <rect x={RAIL - 4} width={8} y={y(lad.price)} height={y(deepest) - y(lad.price)}
                fill={`url(#${gid})`} rx={3} />
        )}
        {/* support shelves — faint dashed context */}
        {lad.supports.map((s, i) => (
          <g key={`s${i}`}>
            <line x1={36} x2={196} y1={y(s.level)} y2={y(s.level)} className="ladder-shelf" />
            {labeledShelves.has(s) && (
              <text x={196} y={y(s.level) - 3} className="ladder-shelf-label" textAnchor="end">
                {`$${fmtNum(s.level)} ×${s.touches}`}
              </text>
            )}
          </g>
        ))}
        {/* resting rungs — VALUE-SCALED bars: length = committed dollars (map-normalized) */}
        {lad.rungs.map((r, i) => {
          const w = BAR_MIN + (BAR_MAX - BAR_MIN) * (maxValue > 0 ? r.value / maxValue : 0);
          const hot = r.distance_pct != null && r.distance_pct >= 0 && r.distance_pct <= HOT_PCT;
          const yy = y(r.limit);
          const inside = w >= 74;
          return (
            <g key={`r${i}`} filter={hot ? `url(#glow-${lad.symbol})` : undefined}>
              <title>{`${r.side.toUpperCase()} ${r.qty} @ $${fmtNum(r.limit)} · $${fmtNum(r.value)} committed`
                + (r.distance_pct != null ? ` · ${r.distance_pct.toFixed(1)}% to fill` : "")
                + (r.expires ? ` · expires ${r.expires}` : "") + (r.note ? `\n${r.note}` : "")}</title>
              <rect x={40} y={yy - 5.5} width={w} height={11} rx={3}
                    className={`ladder-rung ${r.side}${hot ? " hot" : ""}`} />
              {inside
                ? <text x={46} y={yy + 3.5} className="ladder-rung-inlabel">{`${r.qty} @ ${fmtNum(r.limit)}`}</text>
                : <text x={40 + w + 5} y={yy + 3.5} className={`ladder-rung-label ${r.side}`}>
                    {`${r.qty}@${fmtNum(r.limit)}`}
                  </text>}
              {r.distance_pct != null && (
                <text x={196} y={yy + 3.5} textAnchor="end"
                      className={`ladder-dist${hot ? " hot" : ""}`}>
                  {`${r.distance_pct > 0 ? "−" : "+"}${Math.abs(r.distance_pct).toFixed(1)}%`}
                </text>
              )}
            </g>
          );
        })}
        {/* the live price — heavy bar + the pill, drawn LAST (rides above everything) */}
        {lad.price != null && (
          <g>
            <rect x={36} y={y(lad.price) - 2} width={160} height={4} rx={2} className="ladder-price" />
            <rect x={0} y={y(lad.price) - 9} width={34} height={18} rx={4} className="ladder-price-pill" />
            <text x={17} y={y(lad.price) + 3.5} textAnchor="middle" className="ladder-price-text">
              {fmtNum(lad.price)}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

export default function Ladder({ data }: { data: LadderData }) {
  const { ref } = useMeasure(0.4);
  const symbols = data.symbols ?? [];
  if (symbols.length === 0) {
    return <p className="empty">NO RESTING ORDERS — THE SLATE IS EMPTY</p>;
  }
  const maxValue = Math.max(...symbols.flatMap((s) => s.rungs.map((r) => r.value)), 0);
  return (
    <div className="ladder-bank" ref={ref}>
      {data.committed != null && data.committed > 0 && (
        <div className="ladder-committed">Σ ${fmtNum(data.committed)} committed by resting buys · bar length = committed $</div>
      )}
      <div className="ladder-row">
        {symbols.map((lad) => <OneLadder key={lad.symbol} lad={lad} maxValue={maxValue} />)}
      </div>
    </div>
  );
}
