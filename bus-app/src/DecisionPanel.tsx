// DecisionPanel — the Planning tab's open decision. Data = `hn travel decision --json`: the soonest
// live trip whose folder carries a compare radar, passed through with the trip's name, window and a
// `ranked` list (axis total, strongest + weakest axis — computed by the verb, never re-derived here).
// The radar sits at a readable size beside the ranked list; hovering (or focusing) a row brings its
// trace forward. When no decision is live the verb returns no candidates and the widget renders
// nothing at all (see `hasDecision` — Dash drops the tile).

import { useState } from "react";
import { useNav } from "./nav";
import Radar from "./viz/Radar";
import { useCatColors } from "./viz/common";

export interface RankedRow { label: string; total: number; strongest: string; weakest: string }

export interface DecisionData {
  title?: string;
  subtitle?: string;
  max?: number;
  axes: string[];
  candidates: { label: string; values: number[] }[];
  trip?: string;
  trip_name?: string;
  trip_kind?: string;
  window?: string;
  ranked?: RankedRow[];
}

export function hasDecision(d: unknown): boolean {
  const c = (d as DecisionData | null)?.candidates;
  return Array.isArray(c) && c.length > 0;
}

export default function DecisionPanel({ data }: { data: DecisionData }) {
  const nav = useNav();
  const colors = useCatColors();
  const [hot, setHot] = useState<string | null>(null);
  if (!hasDecision(data)) return null;
  const ranked = data.ranked ?? [];
  const colorOf = (label: string) => {
    const i = data.candidates.findIndex((c) => c.label === label);
    return colors[(i < 0 ? 0 : i) % colors.length];
  };
  const best = (data.max ?? 5) * data.axes.length;
  return (
    <div className="decision">
      <div className="decision-head">
        <strong>{[data.trip_name, data.window].filter(Boolean).join(" — ")}</strong>
        {data.title && <span>{data.title}</span>}
        {data.trip && (
          <a href="#" onClick={(e) => {
            e.preventDefault();
            nav.navigate({ zone: "dash", follows: data.trip_kind || "trip", pick: data.trip });
          }}>open the trip ↗</a>
        )}
      </div>
      <div className="decision-body">
        <div className="decision-radar"><Radar data={data} highlight={hot} bare /></div>
        <ol className="decision-rank" onMouseLeave={() => setHot(null)}>
          {ranked.map((r) => (
            <li key={r.label} tabIndex={0} className={hot === r.label ? "hot" : ""}
                onMouseEnter={() => setHot(r.label)} onFocus={() => setHot(r.label)}
                onBlur={() => setHot(null)}>
              <span className="swatch" style={{ background: colorOf(r.label) }} />
              <span className="decision-label">{r.label}</span>
              <span className="decision-total" title={`axis total, of ${best}`}>{r.total}</span>
              <span className="decision-axes">
                <span className="pos">▲ {r.strongest}</span>
                <span className="neg">▼ {r.weakest}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
      {data.subtitle && <p className="decision-foot">{data.subtitle}</p>}
    </div>
  );
}
