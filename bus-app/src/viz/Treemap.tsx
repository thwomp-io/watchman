// Zoomable drill-down treemap — the interactive evolution of the static
// `treemap` type (concentration.json shape: {title, subtitle, groups:[{key,label}], nodes:
// [{label, value, group}]}). Click a tile → zoom into its group; breadcrumb backs out.
// Instrument palette: color identifies GROUP; depth reads through value-scaled luminance.

import { useMemo, useState } from "react";
import * as d3 from "d3";
import JsonView from "../JsonView";
import { COLORS, useMeasure } from "./common";

interface TreemapNode {
  label: string; value: number; group?: string;
  detail?: Record<string, unknown>;  // optional metadata layer — corpus truth
}
interface TreemapData {
  title?: string;
  subtitle?: string;
  groups?: { key: string; label: string }[];
  nodes: TreemapNode[];
}

const fmt = (v: number): string =>
  v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toFixed(0);

export default function Treemap({ data }: { data: TreemapData }) {
  const [focus, setFocus] = useState<string | null>(null);
  const [detailNode, setDetailNode] = useState<TreemapNode | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  const { ref, width: W, height: H } = useMeasure(0.5);  // measures the viz-stage (crumbs excluded)
  const groups = data.groups ?? [];
  const groupLabel = (key: string) => groups.find((g) => g.key === key)?.label ?? key;
  const color = useMemo(() => {
    const keys = groups.length
      ? groups.map((g) => g.key)
      : Array.from(new Set(data.nodes.map((n) => n.group ?? "·")));
    return d3.scaleOrdinal<string, string>().domain(keys).range(COLORS);
  }, [data, groups]);

  const total = d3.sum(data.nodes, (n) => n.value);

  const leaves = useMemo(() => {
    const visible = focus ? data.nodes.filter((n) => (n.group ?? "·") === focus) : data.nodes;
    const root = d3
      .hierarchy<{ children: TreemapNode[] }>({ children: visible } as never)
      .sum((d) => (d as unknown as TreemapNode).value ?? 0)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    d3.treemap<{ children: TreemapNode[] }>().size([W, H]).paddingInner(2).paddingOuter(1)(root);
    return root.leaves() as unknown as Array<
      d3.HierarchyRectangularNode<TreemapNode>
    >;
  }, [data, focus]);

  const focusTotal = focus
    ? d3.sum(data.nodes.filter((n) => (n.group ?? "·") === focus), (n) => n.value)
    : total;

  return (
    <div className="viz-canvas treemap-canvas">
      <div className="viz-crumbs">
        <button className={focus ? "" : "active"}
                onClick={() => { setFocus(null); setDetailNode(null); }}>ALL</button>
        {focus && <span className="crumb-sep">/</span>}
        {focus && <button className="active">{groupLabel(focus).toUpperCase()}</button>}
        <span className="crumb-total">Σ {fmt(focusTotal)} {focus && `· ${((focusTotal / total) * 100).toFixed(1)}% OF ALL`}</span>
      </div>
      <div className="viz-stage" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg"
           onMouseLeave={() => setTip(null)}>
        {leaves.map((leaf, i) => {
          const n = leaf.data;
          const g = n.group ?? "·";
          const w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
          const c = color(g);
          return (
            <g key={i} transform={`translate(${leaf.x0},${leaf.y0})`}
               className="tm-tile"
               onClick={() => (focus ? setDetailNode(n) : setFocus(g))}
               onMouseMove={(e) => {
                 const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                 setTip({
                   x: e.clientX - r.left, y: e.clientY - r.top,
                   text: `${n.label} · ${fmt(n.value)} · ${((n.value / total) * 100).toFixed(1)}%`
                          + (focus ? "" : ` · ${groupLabel(g)}`),
                 });
               }}>
              <rect width={w} height={h} rx={2}
                    fill={c} fillOpacity={focus ? 0.28 : 0.2}
                    stroke={c} strokeOpacity={0.55} strokeWidth={1} />
              {w > 64 && h > 30 && (
                <>
                  <text x={7} y={16} className="tm-label">{n.label}</text>
                  <text x={7} y={30} className="tm-value" fill={c}>{fmt(n.value)}</text>
                </>
              )}
            </g>
          );
        })}
      </svg>
      </div>
      {tip && <div className="viz-tip" style={{ left: tip.x + 14, top: tip.y + 10 }}>{tip.text}</div>}
      {!focus && <p className="viz-hint">CLICK A TILE TO DRILL INTO ITS GROUP</p>}
      {focus && !detailNode && <p className="viz-hint">CLICK A TILE FOR ITS DETAIL RECORD</p>}
      {detailNode && (
        <div className="viz-detail">
          <span className="section-label">{detailNode.label} · {fmt(detailNode.value)}</span>
          {detailNode.detail
            ? <JsonView data={detailNode.detail} />
            : <p className="empty">NO DETAIL ON FILE — enrich the data JSON to populate this layer</p>}
        </div>
      )}
    </div>
  );
}
