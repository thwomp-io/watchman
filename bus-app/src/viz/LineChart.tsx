// Interactive line chart — the `line` type ({title, subtitle, yPrefix?,
// series:[{label, points:[{x: ISO-date, y}]}]}). Crosshair tracks the nearest point; drag a
// brush window to zoom the x-domain; double-click resets.

import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { COLORS, fmtNum, useMeasure } from "./common";

interface Point { x: string; y: number }
interface Series { label: string; points: Point[] }
interface RefLevel { label: string; y: number }
interface LineData {
  title?: string; subtitle?: string; yPrefix?: string; series: Series[];
  levels?: RefLevel[];  // optional reference lines (e.g. support levels) — drawn dashed
}

const M = { top: 16, right: 24, bottom: 28, left: 64 };

export default function LineChart({ data }: { data: LineData }) {
  const { ref, width: W, height: H } = useMeasure(0.30);
  const parse = d3.utcParse("%Y-%m-%d");
  data = { ...data, series: data.series ?? [] };
  const all = useMemo(
    () => data.series.flatMap((s) => s.points.map((p) => ({ ...p, d: parse(p.x) as Date }))),
    [data],  // eslint-disable-line react-hooks/exhaustive-deps
  );
  const fullX = d3.extent(all, (p) => p.d) as [Date, Date];
  const [xDom, setXDom] = useState<[Date, Date]>(fullX);
  const [cross, setCross] = useState<{ px: number; py: number; label: string } | null>(null);
  const dragRef = useRef<number | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);

  // Snap the zoom window back to the data's full extent whenever that extent changes — e.g. a pack swap
  // replaces the series with a different date range. xDom is sticky zoom state, seeded once from the data
  // on mount; without this it stays pinned to the PRIOR data's window, so after a swap to a
  // different-range series the line is clipped/anchored to the old window (only a remount cleared it).
  // Same-extent refreshes (an intraday reprice) keep the user's zoom. Clear interaction state too.
  const x0 = fullX[0] ? fullX[0].getTime() : null;
  const x1 = fullX[1] ? fullX[1].getTime() : null;
  useEffect(() => {
    if (x0 == null || x1 == null) return;
    setXDom([new Date(x0), new Date(x1)]);
    setCross(null);
    setDrag(null);
    dragRef.current = null;
  }, [x0, x1]);

  // Empty series (e.g. offline / no live key → no intraday bars): render a clean note, never crash.
  // d3.extent on zero points yields [undefined, undefined], so the xDom[0].getTime() math below throws
  // (the ErrorBoundary catches it as a "render fault", which reads as broken). Guarded AFTER all hooks.
  if (all.length === 0) {
    return (
      <div ref={ref} style={{
        display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
        minHeight: 120, padding: "0 24px", textAlign: "center", fontSize: 12, color: "var(--ink-faint)",
      }}>
        {data.subtitle || "No data to chart yet."}
      </div>
    );
  }

  const visible = all.filter((p) => p.d >= xDom[0] && p.d <= xDom[1]);
  const x = d3.scaleUtc().domain(xDom).range([M.left, W - M.right]);
  const levelYs = (data.levels ?? []).map((l) => l.y);
  const yExtRaw = d3.extent([...visible.map((p) => p.y), ...levelYs]) as [number, number];
  const yExt = yExtRaw;
  const pad = (yExt[1] - yExt[0]) * 0.08 || 1;
  const y = d3.scaleLinear().domain([yExt[0] - pad, yExt[1] + pad]).range([H - M.bottom, M.top]);
  const yp = data.yPrefix ?? "";
  const line = d3.line<{ d: Date; y: number }>().x((p) => x(p.d)).y((p) => y(p.y));

  const svgPos = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return ((e.clientX - r.left) / r.width) * W;
  };

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const px = svgPos(e);
    if (dragRef.current != null) {
      setDrag([dragRef.current, px]);
      return;
    }
    const date = x.invert(px);
    let best: { px: number; py: number; label: string } | null = null;
    let bestDist = Infinity;
    for (const s of data.series) {
      for (const p of s.points) {
        const d = parse(p.x) as Date;
        if (d < xDom[0] || d > xDom[1]) continue;
        const dist = Math.abs(d.getTime() - date.getTime());
        if (dist < bestDist) {
          bestDist = dist;
          best = {
            px: x(d), py: y(p.y),
            label: `${p.x} · ${yp}${fmtNum(p.y)}${data.series.length > 1 ? ` · ${s.label}` : ""}`,
          };
        }
      }
    }
    setCross(best);
  };

  const endDrag = () => {
    if (dragRef.current != null && drag && Math.abs(drag[1] - drag[0]) > 12) {
      const [a, b] = [Math.min(...drag), Math.max(...drag)];
      setXDom([x.invert(a), x.invert(b)]);
    }
    dragRef.current = null;
    setDrag(null);
  };

  const zoomed = xDom[0].getTime() !== fullX[0].getTime() || xDom[1].getTime() !== fullX[1].getTime();

  return (
    <div className="viz-canvas" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg line-chart"
           onMouseMove={onMove}
           onMouseDown={(e) => { dragRef.current = svgPos(e); }}
           onMouseUp={endDrag}
           onMouseLeave={() => { setCross(null); endDrag(); }}
           onDoubleClick={() => setXDom(fullX)}>
        {y.ticks(5).map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={M.left - 8} y={y(t)} dy="0.35em" textAnchor="end" className="axis-label">
              {yp}{fmtNum(t)}
            </text>
          </g>
        ))}
        {x.ticks(6).map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor="middle" className="axis-label">
            {d3.utcFormat("%b %d")(t)}
          </text>
        ))}
        {(data.levels ?? []).map((l, i) => (
          <g key={`lv${i}`}>
            <line x1={M.left} x2={W - M.right} y1={y(l.y)} y2={y(l.y)} className="ref-level" />
            <text x={W - M.right - 4} y={y(l.y) - 4} textAnchor="end" className="ref-level-label">
              {l.label}
            </text>
          </g>
        ))}
        {data.series.map((s, i) => {
          const pts = s.points
            .map((p) => ({ d: parse(p.x) as Date, y: p.y }))
            .filter((p) => p.d >= xDom[0] && p.d <= xDom[1]);
          return (
            <g key={s.label}>
              <path d={line(pts) ?? ""} fill="none" stroke={COLORS[i % COLORS.length]}
                    strokeWidth={2} strokeLinejoin="round" />
              {pts.map((p, j) => (
                <circle key={j} cx={x(p.d)} cy={y(p.y)} r={2.5}
                        fill={COLORS[i % COLORS.length]} />
              ))}
            </g>
          );
        })}
        {drag && (
          <rect x={Math.min(...drag)} y={M.top} width={Math.abs(drag[1] - drag[0])}
                height={H - M.top - M.bottom} className="brush-rect" />
        )}
        {cross && !drag && (
          <g>
            <line x1={cross.px} x2={cross.px} y1={M.top} y2={H - M.bottom} className="crosshair" />
            <circle cx={cross.px} cy={cross.py} r={4.5} className="crosshair-dot" />
          </g>
        )}
      </svg>
      {cross && !drag && (
        <div className="viz-tip" style={{ left: Math.min(cross.px + 14, W - 180), top: cross.py - 30 }}>
          {cross.label}
        </div>
      )}
      <p className="viz-hint">
        {zoomed ? "DOUBLE-CLICK TO RESET ZOOM" : "DRAG A WINDOW TO ZOOM · CROSSHAIR TRACKS NEAREST POINT"}
      </p>
    </div>
  );
}
