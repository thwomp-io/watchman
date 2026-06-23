// Generic JSON → instrument-panel renderer. Zero domain assumptions — the OSS
// seam: arrays-of-objects → readout tables, objects → stat chips + nested sections, scalars →
// a single stat. Every surface renders through this; "RAW" always available as ground truth.

import { useState } from "react";

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const isObjArray = (v: unknown): v is Record<string, unknown>[] =>
  Array.isArray(v) && v.length > 0 && v.every(isObj);

function cell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === "boolean") return v ? "✓" : "✗";
  if (typeof v === "string") return v;
  const s = JSON.stringify(v);
  return s.length > 48 ? s.slice(0, 47) + "…" : s;
}

const isUrl = (v: unknown): v is string => typeof v === "string" && /^https?:\/\//.test(v);

// A cell value that's an http(s) URL renders as a compact external link ("post ↗") instead of the
// raw URL — surfaces per-row links (e.g. the shortlist's posting URL) the way the markdown tables do.
// Opens in the system browser (read-rich/execute-gated: viewing a public posting is the maintainer's act).
function Cell({ value }: { value: unknown }) {
  if (isUrl(value)) {
    return (
      <a className="ext-link" href={value} target="_blank" rel="noreferrer" title={value}>
        post ↗
      </a>
    );
  }
  return <>{cell(value)}</>;
}

// A "directional" column is a period CHANGE or gain/loss (day move, day $, unrealized G/L) — sign
// matters BOTH ways, so positives go green AND negatives red. A MAGNITUDE column (price, qty, distance)
// is not a gain, so positives stay neutral (only negatives, which shouldn't occur, would red).
// Rationale: de-green the wall so pullbacks pop red; positive day-moves go green so
// "moving further vs closer" reads at a glance. Distance_pct stays a magnitude — its size is the signal.
const DIRECTIONAL = /change|^day_pct$|_gl$|_gl_|move_pct|^gain/i;

function signClass(v: unknown, key?: string): string {
  const directional = !!key && DIRECTIONAL.test(key);
  if (typeof v === "number") {
    if (v < 0) return "neg";
    return directional && v > 0 ? "pos" : "";  // positive: green only in directional change columns
  }
  const s = typeof v === "string" ? v.trim() : "";
  if (s.startsWith("+")) return "pos";
  if (s.startsWith("-") && s !== "-" && s !== "—") return "neg";
  return "";
}

function ReadoutTable({ rows, columns }: { rows: Record<string, unknown>[]; columns?: string[] }) {
  const present = new Set(rows.flatMap((r) => Object.keys(r)));
  // explicit columns (widget-configured) take precedence — keep only those actually present, in order;
  // else auto-derive (capped at 8 to avoid overflow).
  const cols = columns?.length
    ? columns.filter((c) => present.has(c))
    : Array.from(present).slice(0, 8);
  return (
    <table className="readout">
      <thead>
        <tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c} className={signClass(r[c], c)} title={cell(r[c])}><Cell value={r[c]} /></td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatGrid({ entries }: { entries: [string, unknown][] }) {
  if (entries.length === 0) return null;
  return (
    <div className="stat-grid">
      {entries.map(([k, v]) => (
        <div className="stat" key={k}>
          <span className="stat-label">{k.replace(/_/g, " ")}</span>
          <span className={`stat-value ${signClass(v, k)}`}>{cell(v)}</span>
        </div>
      ))}
    </div>
  );
}

function Section({ name, value }: { name: string; value: unknown }) {
  if (isObjArray(value)) {
    return (
      <div className="json-section">
        <span className="section-label">{name.replace(/_/g, " ")} · {(value as unknown[]).length}</span>
        <ReadoutTable rows={value} />
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div className="json-section">
        <span className="section-label">{name.replace(/_/g, " ")}</span>
        <span className="inline-list">{value.map(cell).join(" · ") || "—"}</span>
      </div>
    );
  }
  if (isObj(value)) {
    return (
      <div className="json-section">
        <span className="section-label">{name.replace(/_/g, " ")}</span>
        <StatGrid entries={Object.entries(value)} />
      </div>
    );
  }
  return null;
}

export default function JsonView({ data, columns }: { data: unknown; columns?: string[] }) {
  const [raw, setRaw] = useState(false);
  const rawToggle = (
    <button className="raw-toggle" onClick={() => setRaw(!raw)}>
      {raw ? "READOUT" : "RAW"}
    </button>
  );
  if (raw) {
    return (
      <div className="json-view">
        {rawToggle}
        <pre className="raw-json">{JSON.stringify(data, null, 2)}</pre>
      </div>
    );
  }
  if (isObjArray(data)) {
    return <div className="json-view">{rawToggle}<ReadoutTable rows={data} columns={columns} /></div>;
  }
  if (isObj(data)) {
    const entries = Object.entries(data);
    const scalars = entries.filter(([, v]) => !isObj(v) && !Array.isArray(v));
    const compound = entries.filter(([, v]) => isObj(v) || Array.isArray(v));
    return (
      <div className="json-view">
        {rawToggle}
        <StatGrid entries={scalars} />
        {compound.map(([k, v]) => <Section key={k} name={k} value={v} />)}
      </div>
    );
  }
  return (
    <div className="json-view">
      {rawToggle}
      <StatGrid entries={[["value", data]]} />
    </div>
  );
}
