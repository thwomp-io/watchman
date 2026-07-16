// Zoomable drill-down treemap — the interactive evolution of the static
// `treemap` type (concentration.json shape: {title, subtitle, groups:[{key,label}], nodes:
// [{label, value, group}]}). Click a tile → zoom into its group; breadcrumb backs out.
// Instrument palette: color identifies GROUP; depth reads through value-scaled luminance.

import { useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import JsonView from "../JsonView";
import { useCatColors, useMeasure } from "./common";
import { resolveRef, useNav, type Ref } from "../nav";

interface TreemapNode {
  label: string; value: number; group?: string;
  detail?: Record<string, unknown>;  // optional metadata layer — corpus truth
  ref?: Ref;  // research-report link — same tooltip mechanic as Scatter's bench maps
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
  const COLORS = useCatColors(); // theme-aware categorical set (re-renders on toggle)
  const nav = useNav();
  const [focus, setFocus] = useState<string | null>(null);
  const [detailNode, setDetailNode] = useState<TreemapNode | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; n: TreemapNode } | null>(null);

  // hover-bridge (mirrors Scatter): a node with a `ref` gets an in-tooltip link, so the tip is
  // set ONCE at tile-enter (not mouse-follow) and cleared on a short grace timer — cancelled when
  // the cursor enters the tooltip itself — letting the pointer travel tile → tooltip → link.
  const clearTimer = useRef<number | null>(null);
  const cancelClear = () => { if (clearTimer.current) { clearTimeout(clearTimer.current); clearTimer.current = null; } };
  const scheduleClear = () => { cancelClear(); clearTimer.current = window.setTimeout(() => setTip(null), 140); };
  const openRef = (r: Ref) => void resolveRef(r).then(nav.navigate);

  // 0.5→0.6 aspect: dense rosters (a full fund look-through runs many dozens of tiles) starve for height on wide screens;
  // dashboards unaffected (a fixed tile height wins over the aspect fallback in useMeasure).
  const { ref, width: W, height: H } = useMeasure(0.6);  // measures the viz-stage (crumbs excluded)
  const groups = data.groups ?? [];
  const groupLabel = (key: string) => groups.find((g) => g.key === key)?.label ?? key;
  const color = useMemo(() => {
    const keys = groups.length
      ? groups.map((g) => g.key)
      : Array.from(new Set(data.nodes.map((n) => n.group ?? "·")));
    return d3.scaleOrdinal<string, string>().domain(keys).range(COLORS);
  }, [data, groups, COLORS]);

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
    // W/H MUST be deps (a fixed field bug): without them the layout locks at whatever the
    // container measured at data-arrival — a warm session (instant data, measure lands second)
    // rendered the map at the 900px default inside a 2200px stage and never re-laid-out.
  }, [data, focus, W, H]);

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
           onMouseLeave={scheduleClear}>
        {leaves.map((leaf, i) => {
          const n = leaf.data;
          const g = n.group ?? "·";
          const w = leaf.x1 - leaf.x0, h = leaf.y1 - leaf.y0;
          const c = color(g);
          return (
            <g key={i} transform={`translate(${leaf.x0},${leaf.y0})`}
               className="tm-tile"
               onClick={() => (focus ? setDetailNode(n) : setFocus(g))}
               onMouseEnter={(e) => {
                 const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                 cancelClear();
                 setTip({ x: e.clientX - r.left, y: e.clientY - r.top, n });
               }}
               onMouseLeave={scheduleClear}>
              <rect width={w} height={h} rx={2}
                    fill={c} fillOpacity={focus ? 0.28 : 0.2}
                    stroke={c} strokeOpacity={0.55} strokeWidth={1} />
              {/* label thresholds lowered + clip-pathed: mid-size tiles show a
                  cleanly-clipped name instead of nothing — dense-roster legibility. */}
              {w > 44 && h > 26 && (
                <>
                  <clipPath id={`tmclip-${i}`}><rect width={Math.max(w - 6, 0)} height={h} /></clipPath>
                  <g clipPath={`url(#tmclip-${i})`}>
                    <text x={7} y={16} className="tm-label">{n.label}</text>
                    {h > 40 && <text x={7} y={30} className="tm-value" fill={c}>{fmt(n.value)}</text>}
                  </g>
                </>
              )}
            </g>
          );
        })}
      </svg>
      </div>
      {tip && (
        <div className={`viz-tip${tip.n.ref ? " has-link" : ""}`}
             style={{ left: tip.x + 14, top: tip.y + 10 }}
             onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
          <div className="viz-tip-head">
            <span className="viz-tip-dot" style={{ background: color(tip.n.group ?? "·") }} />
            <strong>{tip.n.label}</strong>
          </div>
          <div className="viz-tip-rows">
            <span className="k">value</span><span className="v">{fmt(tip.n.value)}</span>
            <span className="k">weight</span><span className="v">{((tip.n.value / total) * 100).toFixed(1)}%</span>
            {tip.n.group && (<><span className="k">group</span><span className="v">{groupLabel(tip.n.group)}</span></>)}
          </div>
          {tip.n.ref && (
            <button className="viz-tip-link" onClick={() => openRef(tip.n.ref!)}>
              open research report →
            </button>
          )}
        </div>
      )}
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
