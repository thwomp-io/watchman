// TickerStrip — a scannable row of current values (the commodity-ticker read): each cell is one
// rail row from `hn finance global --json` — name, the level, the day's change, and which session
// that change measures. Yields read in % with the change in basis points; futures show their
// contract month small, so a roll never hides behind a continuous-looking name.

import type { GlobalQuote } from "./generated/contracts";

export type RailRow = GlobalQuote;

interface GlobalJson {
  rails?: Record<string, RailRow[]>;
}

// Yahoo quotes these Treasury indices as the yield itself (5.29 = 5.29%).
const YIELDS = new Set(["^IRX", "^FVX", "^TNX", "^TYX"]);

export function indexRows(data: unknown): Map<string, RailRow> {
  const out = new Map<string, RailRow>();
  for (const rows of Object.values((data as GlobalJson)?.rails ?? {})) {
    for (const r of rows ?? []) if (r?.symbol && !out.has(r.symbol)) out.set(r.symbol, r);
  }
  return out;
}

// Precision follows the instrument: sub-$10 contracts (nat gas, RBOB, ULSD, EUR/USD) need four
// places to show a day's move; big levels (indices, BTC) read best grouped with two.
export function fmtLevel(v: number): string {
  if (Math.abs(v) < 10) {
    const [int, frac = ""] = v.toFixed(4).split(".");
    return `${int}.${frac.replace(/0+$/, "").padEnd(2, "0")}`;
  }
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function shortName(name: string): string {
  return name.split(" · ")[0];
}

export function sessionText(r: RailRow): string {
  if (r.live && r.market_time) {
    const t = new Date(r.market_time * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return `LIVE ${t}`;
  }
  const label = (r.session_label ?? "").replace(/ close$/, "");
  return label ? `CLOSED ${label}` : "";
}

function Cell({ symbol, row }: { symbol: string; row: RailRow | undefined }) {
  if (!row || row.price == null) {
    return (
      <div className="ticker-cell missing">
        <div className="ticker-name">{symbol}</div>
        <div className="ticker-level">—</div>
        <div className="ticker-foot">not on the feed</div>
      </div>
    );
  }
  const yieldRow = YIELDS.has(row.symbol);
  const dir = (row.change ?? 0) > 0 ? "pos" : (row.change ?? 0) < 0 ? "neg" : "flat";
  const arrow = dir === "pos" ? "▲" : dir === "neg" ? "▼" : "■";
  const change = row.change == null ? "" : yieldRow
    ? `${row.change > 0 ? "+" : ""}${Math.round(row.change * 100)}bp`
    : `${row.change > 0 ? "+" : ""}${fmtLevel(row.change)}`;
  const pct = row.change_pct == null || yieldRow
    ? ""
    : `${row.change_pct > 0 ? "+" : ""}${row.change_pct.toFixed(2)}%`;
  return (
    <div className={`ticker-cell ${dir}`} title={row.name}>
      <div className="ticker-name">
        {shortName(row.name)}
        {row.contract_month && <span className="ticker-month">{row.contract_month}</span>}
      </div>
      <div className="ticker-level">{fmtLevel(row.price)}{yieldRow ? "%" : ""}</div>
      <div className="ticker-move">
        <span className="ticker-arrow">{arrow}</span> {change} {pct && <span>{pct}</span>}
      </div>
      <div className="ticker-foot">{sessionText(row)}</div>
    </div>
  );
}

export function TickerStrip({ data, tickers }: { data: unknown; tickers: string[] }) {
  if (!tickers.length) return <p className="empty">NO TICKERS CONFIGURED</p>;
  const rows = indexRows(data);
  return (
    <div className="ticker-strip">
      {tickers.map((s) => <Cell key={s} symbol={s} row={rows.get(s)} />)}
    </div>
  );
}
