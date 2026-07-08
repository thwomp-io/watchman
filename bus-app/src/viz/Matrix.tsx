// Interactive matrix — the `matrix` type ({title, subtitle, max, axes[],
// rows:[{label, group?, values[], reach?, detail?{}}], detailCols?, reachLabel?}). Heatmap
// cells amber-scaled to value/max, hover → cell tooltip, click a row → its detail record
// (the vault data already carries `detail` — the layer renders for free here).

import { useState } from "react";
import JsonView from "../JsonView";
import { useNav, type Ref } from "../nav";
import { type Tip } from "./common";

interface MatrixRow {
  label: string; group?: string; values: number[]; reach?: string;
  detail?: Record<string, unknown>; ref?: Ref;
}
interface MatrixData {
  title?: string; subtitle?: string; max?: number; axes: string[];
  rows: MatrixRow[]; reachLabel?: string;
}

export default function Matrix({ data }: { data: MatrixData }) {
  const nav = useNav();
  const [tip, setTip] = useState<Tip | null>(null);
  const [sel, setSel] = useState<MatrixRow | null>(null);
  const max = data.max ?? Math.max(...data.rows.flatMap((r) => r.values), 1);

  return (
    <div className="viz-canvas">
      <div className="matrix-wrap" onMouseLeave={() => setTip(null)}>
        <table className="matrix">
          <thead>
            <tr>
              <th />
              {data.axes.map((a) => <th key={a}>{a}</th>)}
              {data.reachLabel && <th>{data.reachLabel}</th>}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.label} className={sel?.label === r.label ? "selected" : ""}
                  onClick={() => setSel(sel?.label === r.label ? null : r)}>
                <th>
                  {r.group && <span className="lane-tag">{r.group}</span>}
                  {r.ref ? (
                    <a
                      className="wikilink"
                      href="#"
                      title={`open ${r.label}'s profile`}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation(); // don't also toggle the row's detail panel
                        nav.navigate(r.ref!);
                      }}
                    >
                      {r.label}
                    </a>
                  ) : (
                    r.label
                  )}
                </th>
                {r.values.map((v, i) => (
                  <td key={i}
                      style={{ background: `rgba(var(--amber-rgb), ${(v / max) * 0.55})` }}
                      onMouseMove={(e) => {
                        const w = e.currentTarget.closest(".matrix-wrap") as HTMLElement;
                        const rect = w.getBoundingClientRect();
                        setTip({
                          x: e.clientX - rect.left, y: e.clientY - rect.top,
                          text: `${r.label} × ${data.axes[i]} · ${v}/${max}`,
                        });
                      }}>
                    {v}
                  </td>
                ))}
                {data.reachLabel && <td className="reach">{r.reach ?? ""}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {tip && <div className="viz-tip" style={{ left: tip.x + 14, top: tip.y + 10 }}>{tip.text}</div>}
      </div>
      {sel && (
        <div className="viz-detail">
          <span className="section-label">{sel.label}</span>
          {sel.detail
            ? <JsonView data={sel.detail} />
            : <p className="empty">NO DETAIL ON FILE — enrich the data JSON to populate this layer</p>}
        </div>
      )}
      {!sel && <p className="viz-hint">HOVER A CELL FOR ITS SCORE · CLICK A ROW FOR ITS DETAIL RECORD</p>}
    </div>
  );
}
