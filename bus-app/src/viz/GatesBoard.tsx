// The plan-gates board — the interactive twin of `hn finance gates --json`.
// A ranked-meter treatment — a news/TV graphic, not a spreadsheet:
// every upcoming binary is a FAT ranked bar — bar LENGTH encodes imminence (today = full
// bleed, the horizon edge = a stub with a chunk floor so nothing renders skinny), color
// carries the gate KIND (prints vs macro, per-kind categorical accents from the theme
// fleet), and the big right-aligned countdown is the number the eye came for. Confirmed
// print dates render solid; estimates carry the honest (est) chip — the same
// announcement-vs-projection contract the CLI label enforces. Hot gates (≤2d) glow.
// Data contract: { as_of, horizon_days, gates:[{ kind, symbol?, label, date?, days?,
// confirmed }], notes[] } — `gates` is the sniff signature.

import type React from "react";
import { useCatColors } from "./common";

export interface GateItem {
  kind: string; symbol?: string | null; label: string;
  date?: string | null; days?: number | null; confirmed?: boolean;
}
export interface GatesData {
  as_of: string; horizon_days: number; gates: GateItem[]; notes?: string[];
}

const FILL_FLOOR = 0.3; // min bar-length share — every gate reads as a block, never a sliver
const HOT_DAYS = 2;     // ≤ this many days out = hot (glow + amber count)

function textOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#101314";
  const n = parseInt(m[1], 16);
  const lum = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.52 ? "#101314" : "#f6f3ea";
}

function countdown(days: number | null | undefined): string {
  if (days == null) return "—";
  if (days === 0) return "TODAY";
  if (days === 1) return "1d";
  return `${days}d`;
}

export default function GatesBoard({ data }: { data: GatesData }) {
  const COLORS = useCatColors();
  const gates = data.gates ?? [];
  if (gates.length === 0) {
    return <div className="gates-empty">no gates inside {data.horizon_days}d — clear runway</div>;
  }
  // stable per-kind accents: kinds in first-seen order pull from the categorical fleet
  const kinds = [...new Set(gates.map((g) => g.kind))];
  const accentFor = (kind: string) => COLORS[kinds.indexOf(kind) % COLORS.length];
  const horizon = Math.max(1, data.horizon_days);

  return (
    <div className="gates-board">
      {gates.map((g, i) => {
        const accent = accentFor(g.kind);
        const days = g.days ?? horizon;
        const share = Math.max(0, Math.min(1, 1 - days / horizon));
        const w = (FILL_FLOOR + (1 - FILL_FLOOR) * share) * 100;
        const hot = days <= HOT_DAYS;
        const est = g.kind === "print" && !g.confirmed;
        const title = `${g.label}${g.date ? ` · ${g.date}` : ""}${est ? " · estimate (filing cadence / algo)" : g.kind === "print" ? " · confirmed" : ""}`;
        return (
          <div key={i} className={`gates-row${hot ? " hot" : ""}`} title={title}>
            <span className="gates-rank">{i + 1}</span>
            <div
              className={`gates-bar${hot ? " hot" : ""}`}
              style={{
                width: `${w}%`,
                background: `linear-gradient(180deg, color-mix(in srgb, ${accent} ${hot ? 56 : 70}%, white) 0%, ${accent} 40%, color-mix(in srgb, ${accent} 66%, black) 100%)`,
                borderColor: `color-mix(in srgb, ${accent} 55%, black)`,
                ...({ "--hot-c": accent } as React.CSSProperties),
              }}
            >
              <span className="gates-kind" style={{ color: textOn(accent) }}>
                {g.kind.toUpperCase()}
              </span>
              {g.symbol && (
                <span className="gates-sym" style={{ color: textOn(accent) }}>{g.symbol}</span>
              )}
              <span
                className="gates-label"
                style={{ color: `color-mix(in srgb, ${textOn(accent)} 82%, transparent)` }}
              >
                {g.label}
              </span>
              {est && <span className="gates-est" style={{ color: textOn(accent) }}>est</span>}
            </div>
            <span className={`gates-count${hot ? " hot" : ""}`}>
              {countdown(g.days)}
              {g.date && <span className="gates-date">{g.date.slice(5)}</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}
