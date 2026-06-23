// Interactive sankey — the live evolution of the static `sankey` type
// (unwind.json shape: {title, subtitle, nodes:[{name}], links:[{source, target, value}],
// unitPrefix?, unitSuffix?}; links reference node NAMES). Hover a ribbon → phosphor highlight +
// value; click a node → pinned in/out flow detail.

import { useMemo, useState } from "react";
import * as d3 from "d3";
import { sankey, sankeyLinkHorizontal } from "d3-sankey";
import JsonView from "../JsonView";

interface SankeyData {
  title?: string;
  subtitle?: string;
  nodes: { name: string; detail?: Record<string, unknown> }[];
  links: { source: string; target: string; value: number }[];
  unitPrefix?: string;
  unitSuffix?: string;
}

type SNode = { name: string; index?: number; x0?: number; x1?: number; y0?: number; y1?: number };
type SLink = {
  source: SNode | number; target: SNode | number; value: number;
  width?: number; index?: number;
};

export default function Sankey({ data }: { data: SankeyData }) {
  const [hoverLink, setHoverLink] = useState<number | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const W = 860, H = 380;
  const unit = (v: number) => `${data.unitPrefix ?? ""}${v}${data.unitSuffix ?? ""}`;

  const layout = useMemo(() => {
    const names = data.nodes.map((n) => n.name);
    const nodes: SNode[] = names.map((name) => ({ name }));
    const links: SLink[] = data.links.map((l) => ({
      source: names.indexOf(l.source), target: names.indexOf(l.target), value: l.value,
    }));
    const gen = sankey<SNode, SLink>()
      .nodeWidth(10)
      .nodePadding(22)
      .extent([[140, 12], [W - 250, H - 12]]);
    return gen({ nodes, links });
  }, [data]);

  const path = sankeyLinkHorizontal();
  const pinnedFlows = pinned
    ? {
        in: layout.links.filter((l) => (l.target as SNode).name === pinned),
        out: layout.links.filter((l) => (l.source as SNode).name === pinned),
      }
    : null;

  return (
    <div className="viz-canvas">
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg">
        {layout.links.map((l, i) => (
          <path key={i} d={path(l as never) ?? ""}
                className={`sk-link ${hoverLink === i ? "hover" : ""}`}
                strokeWidth={Math.max(1.5, l.width ?? 1)}
                onMouseEnter={() => setHoverLink(i)}
                onMouseLeave={() => setHoverLink(null)} />
        ))}
        {layout.links.map((l, i) =>
          hoverLink === i ? (
            <text key={`t${i}`} className="sk-link-label"
                  x={(((l.source as SNode).x1 ?? 0) + ((l.target as SNode).x0 ?? 0)) / 2}
                  y={((l as { y0?: number }).y0 ?? 0) / 2 + ((l as { y1?: number }).y1 ?? 0) / 2 - 6}
                  textAnchor="middle">
              {unit(l.value)}
            </text>
          ) : null,
        )}
        {layout.nodes.map((n, i) => {
          const left = (n.x0 ?? 0) < W / 2;
          return (
            <g key={i} className={`sk-node ${pinned === n.name ? "pinned" : ""}`}
               onClick={() => setPinned(pinned === n.name ? null : n.name)}>
              <rect x={n.x0} y={n.y0} width={(n.x1 ?? 0) - (n.x0 ?? 0)}
                    height={Math.max(2, (n.y1 ?? 0) - (n.y0 ?? 0))} rx={1.5} />
              <text x={left ? (n.x0 ?? 0) - 8 : (n.x1 ?? 0) + 8}
                    y={((n.y0 ?? 0) + (n.y1 ?? 0)) / 2}
                    dy="0.35em" textAnchor={left ? "end" : "start"}>
                {n.name}
              </text>
            </g>
          );
        })}
      </svg>
      {pinnedFlows && (
        <div className="sk-detail">
          <span className="section-label">{pinned}</span>
          {pinnedFlows.in.length > 0 && (
            <p>IN · {pinnedFlows.in.map((l) => `${(l.source as SNode).name} ${unit(l.value)}`).join(" · ")}</p>
          )}
          {pinnedFlows.out.length > 0 && (
            <p>OUT · {pinnedFlows.out.map((l) => `${(l.target as SNode).name} ${unit(l.value)}`).join(" · ")}</p>
          )}
          <p>NET · IN {unit(d3.sum(pinnedFlows.in, (l) => l.value))} / OUT {unit(d3.sum(pinnedFlows.out, (l) => l.value))}</p>
          {(() => {
            const detail = data.nodes.find((n) => n.name === pinned)?.detail;
            return detail ? <JsonView data={detail} /> : null;
          })()}
        </div>
      )}
      {!pinned && <p className="viz-hint">HOVER A RIBBON FOR VALUE · CLICK A NODE TO PIN ITS FLOWS</p>}
    </div>
  );
}
