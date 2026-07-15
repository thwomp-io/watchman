// Bead family tree (the `bead-tree` type) — the backlog as an org chart: epics with their
// children laid out top-down (tidy-tree over parent-child edges), standalone beads on a shelf
// beneath, and blocks-dependencies overlaid as dashed edges. Blocks are status-colored
// (in-progress amber, shipped green, deferred dim) with a P1 accent rail; hover a block for the
// full metadata card + an "open ticket →" deep-link into the generated vault doc (the Scatter
// hover-bridge pattern — the tooltip is interactive, so a grace timer bridges cursor travel).
// Clicking a tile quick-looks the full ticket in a DocPopup (hover behavior untouched).
// Layout WRAPS TO THE WINDOW: a ResizeObserver measures the container and families bin-pack
// into bands that fit its width, so the tree grows DOWN instead of forcing horizontal scroll
// (the claustrophobia fix — h-scroll only survives when one family is wider than the window).
// Data contract: {beads:[{id,title,status,priority,...,ticket}], edges:[{source,target,kind}]}
// — keys deliberately distinct from sankey's nodes/links so the shape-sniffer stays honest.

import { useEffect, useMemo, useRef, useState } from "react";
import DocPopup from "../DocPopup";
import { useNav } from "../nav";

interface BeadNode {
  id: string; title: string; status: string; priority: string; type?: string;
  assignee?: string; labels?: string; updated?: string; ticket?: string;
}
interface BeadEdge { source: string; target: string; kind: string }
interface TreeData { beads: BeadNode[]; edges: BeadEdge[]; omitted?: number }

const NODE_W = 148, NODE_H = 42, SLOT_W = 160, ROW_H = 92;
const PAD = { x: 14, top: 30, shelf: 46, bottom: 16 };

const trunc = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export default function BeadTree({ data }: { data: TreeData }) {
  const nav = useNav();
  // one hover at a time: a block ({b}) or a blocks-edge ({e}) — entering either claims the card
  const [hover, setHover] = useState<
    { px: number; py: number; b: BeadNode; e?: never } | { px: number; py: number; e: BeadEdge; b?: never } | null
  >(null);

  // hover-bridge (the Scatter pattern): the tooltip carries links, so leaving the block/edge arms
  // a short grace timer that entering the tooltip cancels.
  const clearTimer = useRef<number | null>(null);
  const cancelClear = () => { if (clearTimer.current) { clearTimeout(clearTimer.current); clearTimer.current = null; } };
  const showHover = (h: NonNullable<typeof hover>) => { cancelClear(); setHover(h); };
  const scheduleClear = () => { cancelClear(); clearTimer.current = window.setTimeout(() => setHover(null), 140); };
  const openTicket = (b: BeadNode) => { if (b.ticket) nav.navigate({ zone: "vault", doc: b.ticket }); };
  // tile click = quick-look (the DocPopup primitive); the tooltip links keep their VAULT nav
  const [popupDoc, setPopupDoc] = useState<string | null>(null);

  // tooltip placement is SCROLL-VIEWPORT-aware: the canvas is far taller than its window, so a
  // card clamped only to content bounds still dies behind the tile's bottom edge (the eye caught
  // it). Below the pointer when it fits in the visible window, flipped above when it doesn't.
  const scrollRef = useRef<HTMLDivElement>(null);
  const tipTop = (py: number, cardH: number, offset: number) => {
    const el = scrollRef.current;
    const visTop = el?.scrollTop ?? 0;
    const visBottom = el ? el.scrollTop + el.clientHeight : Number.MAX_SAFE_INTEGER;
    const below = py + offset;
    const t = below + cardH > visBottom ? py - cardH - 10 : below;
    return Math.max(visTop + 8, t);
  };

  // wrap-to-window: measure the scroll container so the layout re-flows on ANY window resize
  // (Tauri or browser). jsdom's stubbed ResizeObserver never fires → the default width holds.
  const [availW, setAvailW] = useState(1100);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setAvailW(el.clientWidth || 1100);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const layout = useMemo(() => {
    const beads = data.beads ?? [];
    const byId = new Map(beads.map((b) => [b.id, b]));
    const childEdges = (data.edges ?? []).filter(
      (e) => e.kind === "child" && byId.has(e.source) && byId.has(e.target),
    );
    const parentOf = new Map(childEdges.map((e) => [e.target, e.source]));
    const kids = new Map<string, string[]>();
    for (const b of beads) {
      const p = parentOf.get(b.id);
      if (p) (kids.get(p) ?? kids.set(p, []).get(p)!).push(b.id);
    }
    const roots = beads.filter((b) => !parentOf.has(b.id));
    const familyRoots = roots.filter((r) => (kids.get(r.id)?.length ?? 0) > 0);
    const singles = roots.filter((r) => !(kids.get(r.id)?.length ?? 0));

    // pass 1 — measure each family's slot width (leaf count), cycle-guarded
    const seen = new Set<string>();
    const widthOf = (id: string): number => {
      if (seen.has(id)) return 0;
      seen.add(id);
      const c = kids.get(id) ?? [];
      return c.length ? Math.max(1, c.reduce((n, k) => n + widthOf(k), 0)) : 1;
    };
    const famW = new Map(familyRoots.map((r) => [r.id, widthOf(r.id)]));

    // pass 2 — bin-pack families into BANDS that fit the container width. A family wider than
    // the window keeps its own band (h-scroll only then, never as the default posture).
    const widest = Math.max(1, ...famW.values());
    const availSlots = Math.max(widest, Math.floor((availW - PAD.x * 2) / SLOT_W), 1);
    const bandAt = new Map<string, { band: number; x0: number }>();
    let band = 0, cursor = 0;
    for (const r of familyRoots) {
      const w = famW.get(r.id) ?? 1;
      if (cursor > 0 && cursor + w > availSlots) { band++; cursor = 0; }
      bandAt.set(r.id, { band, x0: cursor });
      cursor += w;
    }

    // pass 3 — place nodes; per-band depth drives each band's height
    const pos = new Map<string, { cx: number; depth: number; band: number }>();
    const bandDepth = new Map<number, number>();
    const visited = new Set<string>();
    const place = (id: string, depth: number, x0: number, b: number): number => {
      if (visited.has(id)) return 0; // cycle guard — bd shouldn't produce one, we survive one
      visited.add(id);
      bandDepth.set(b, Math.max(bandDepth.get(b) ?? 0, depth));
      const c = kids.get(id) ?? [];
      let w = 0;
      if (!c.length) w = 1;
      else { let cx = x0; for (const k of c) cx += place(k, depth + 1, cx, b); w = Math.max(1, cx - x0); }
      pos.set(id, { cx: x0 + w / 2, depth, band: b });
      return w;
    };
    for (const r of familyRoots) {
      const at = bandAt.get(r.id)!;
      place(r.id, 0, at.x0, at.band);
    }
    const bandY = new Map<number, number>();
    let y = PAD.top;
    for (let b = 0; bandDepth.has(b); b++) {
      bandY.set(b, y);
      y += ((bandDepth.get(b) ?? 0) + 1) * ROW_H + 18;
    }

    // the shelf: standalone beads + anything the tree walk couldn't place (a cyclic parent
    // edge orphans its members — they must still render, never silently vanish)
    const shelf = [...singles, ...beads.filter((b) => !pos.has(b.id) && !singles.includes(b))];
    const shelfCols = availSlots;
    const shelfRows = Math.ceil(shelf.length / shelfCols);
    const shelfY = y + (shelf.length && familyRoots.length ? PAD.shelf - 18 : 0);
    const width = PAD.x * 2 + availSlots * SLOT_W;
    const height = shelfY + shelfRows * (NODE_H + 22) + PAD.bottom;

    const xy = (id: string): { x: number; y: number } | null => {
      const p = pos.get(id);
      if (p) {
        return { x: PAD.x + p.cx * SLOT_W - NODE_W / 2, y: (bandY.get(p.band) ?? PAD.top) + p.depth * ROW_H };
      }
      const i = shelf.findIndex((s) => s.id === id);
      if (i < 0) return null;
      return { x: PAD.x + (i % shelfCols) * SLOT_W, y: shelfY + Math.floor(i / shelfCols) * (NODE_H + 22) };
    };
    return { beads, byId, childEdges, singles, familyRoots, xy, width, height, shelfY };
  }, [data, availW]);

  if (!layout.beads.length) {
    return <div className="viz-canvas"><p className="viz-hint">NO ACTIVE BEADS — a quiet board is a calm board</p></div>;
  }

  const blockEdges = (data.edges ?? []).filter((e) => e.kind === "blocks");

  return (
    <div className="viz-canvas beadtree-canvas">
      <div className="beadtree-scroll" ref={scrollRef}>
        <svg width={layout.width} height={layout.height} className="beadtree-svg">
          {layout.childEdges.map((e, i) => {
            const a = layout.xy(e.source), b = layout.xy(e.target);
            if (!a || !b) return null;
            const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y;
            const my = (y1 + y2) / 2;
            return <path key={`c${i}`} className="beadtree-edge"
                         d={`M${x1},${y1} V${my} H${x2} V${y2}`} />;
          })}
          {blockEdges.map((e, i) => {
            const a = layout.xy(e.source), b = layout.xy(e.target);
            if (!a || !b) return null;
            const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H / 2, x2 = b.x + NODE_W / 2, y2 = b.y + NODE_H / 2;
            const d = `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`;
            // the visible dash is 1.2px — an invisible fat twin underneath is the actual hover
            // target, so the relationship card doesn't demand pixel-hunting
            return (
              <g key={`b${i}`}>
                <path className={`beadtree-edge-blocks${hover?.e === e ? " hot" : ""}`} d={d} />
                <path className="beadtree-edge-hit" d={d}
                      onMouseEnter={() => showHover({ px: (x1 + x2) / 2, py: (y1 + y2) / 2, e })}
                      onMouseLeave={scheduleClear} />
              </g>
            );
          })}
          {layout.beads.map((b) => {
            const p = layout.xy(b.id);
            if (!p) return null;
            const hot = hover?.b === b ||
              (hover?.e != null && (hover.e.source === b.id || hover.e.target === b.id));
            return (
              <g key={b.id} className={`beadtree-node st-${b.status}${b.priority === "P1" ? " p1" : ""}${hot ? " hot" : ""}`}
                 transform={`translate(${p.x},${p.y})`} style={{ cursor: "pointer" }}
                 onMouseEnter={() => showHover({ px: p.x + NODE_W / 2, py: p.y, b })}
                 onMouseLeave={scheduleClear}
                 onClick={() => { if (b.ticket) setPopupDoc(b.ticket); }}>
                <rect className="beadtree-box" width={NODE_W} height={NODE_H} rx={5} />
                <rect className="beadtree-rail" width={3.5} height={NODE_H} rx={1.5} />
                <text className="beadtree-id" x={10} y={16}>{trunc(b.id, 22)}</text>
                <text className="beadtree-title" x={10} y={32}>{trunc(b.title, 24)}</text>
              </g>
            );
          })}
        </svg>

        {hover?.b && (
          <div className="viz-tip has-link beadtree-tip"
               style={{ left: Math.max(8, Math.min(hover.px - 130, layout.width - 290)),
                        top: tipTop(hover.py, 190, NODE_H + 8) }}
               onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
            <div className="viz-tip-head">
              <span className={`beadtree-tip-dot st-${hover.b.status}`} />
              <strong>{hover.b.id}</strong>
            </div>
            <div className="viz-tip-detail">{hover.b.title}</div>
            <div className="viz-tip-rows">
              <span className="k">status</span><span className="v">{hover.b.status}</span>
              <span className="k">priority</span><span className="v">{hover.b.priority}</span>
              {hover.b.assignee && (<><span className="k">assignee</span><span className="v">{hover.b.assignee}</span></>)}
              {hover.b.labels && (<><span className="k">labels</span><span className="v">{hover.b.labels}</span></>)}
              {hover.b.updated && (<><span className="k">updated</span><span className="v">{hover.b.updated}</span></>)}
            </div>
            {hover.b.ticket && (
              <button className="viz-tip-link" onClick={() => openTicket(hover.b)}>open ticket →</button>
            )}
          </div>
        )}

        {hover?.e && (() => {
          // the relationship card: what the dashed line MEANS, endpoints side-by-side. `blocks`
          // semantics: source must close before target can proceed (bd's depends_on direction).
          const src = layout.byId.get(hover.e.source);
          const tgt = layout.byId.get(hover.e.target);
          if (!src || !tgt) return null;
          return (
            <div className="viz-tip has-link beadtree-tip beadtree-rel-tip"
                 style={{ left: Math.max(8, Math.min(hover.px - 190, layout.width - 400)),
                          top: tipTop(hover.py, 205, 14) }}
                 onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
              <div className="viz-tip-head">
                <strong className="beadtree-rel-kind">BLOCKS-DEP</strong>
                <span className="beadtree-rel-state">
                  {src.status === "closed" ? "resolved — blocker closed" : "active — blocker must close first"}
                </span>
              </div>
              <div className="beadtree-rel">
                {[src, tgt].map((b, i) => (
                  <div key={b.id} className="beadtree-rel-card">
                    <div className="beadtree-rel-role">{i === 0 ? "blocker" : "blocked"}</div>
                    <div className="viz-tip-head">
                      <span className={`beadtree-tip-dot st-${b.status}`} />
                      <strong>{b.id}</strong>
                    </div>
                    <div className="viz-tip-detail">{b.title}</div>
                    <div className="beadtree-rel-meta">{b.status} · {b.priority}</div>
                    {b.ticket && (
                      <button className="viz-tip-link" onClick={() => openTicket(b)}>open ticket →</button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })()}
      </div>
      <p className="viz-hint">
        HOVER A BLOCK OR A DASHED LINE FOR DETAIL · CLICK A TILE = QUICK-LOOK TICKET · DASHED = BLOCKS-DEP
        {data.omitted ? ` · ${data.omitted} QUIET SINGLES OFF-TREE` : ""}
      </p>
      {popupDoc && <DocPopup doc={popupDoc} onClose={() => setPopupDoc(null)} />}
    </div>
  );
}
