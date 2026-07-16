// The VIZ zone: vault-discovered diagram data, rendered interactively.
// Left rail = discovered entries grouped by owning doc (shape-sniffed types; unsupported ones
// listed honestly with their phase). Main = the interactive renderer for the selection.

import { useEffect, useMemo, useState } from "react";
import { listViz, readViz } from "../api";
import ErrorBoundary from "../ErrorBoundary";
import { useNav } from "../nav";
import type { VizEntry } from "../types";
import BarChart from "./BarChart";
import Donuts from "./Donut";
import FoodBank from "./FoodBank";
import LineChart from "./LineChart";
import Matrix from "./Matrix";
import Radar from "./Radar";
import Sankey from "./Sankey";
import Scatter from "./Scatter";
import Schedule from "./Schedule";
import Treemap from "./Treemap";
import VestTimeline from "./VestTimeline";
import Ladder from "./Ladder";
import BeadTree from "./BeadTree";

const PHASE_BY_TYPE: Record<string, string> = {
  unknown: "TBD",  // remaining: radial/map/calendar — interactive on demand
};

// `target` (a VizEntry.path) deep-links a specific diagram (e.g. back/forward restoring the scatter you
// were on); absent, the first supported entry is shown. Selection is reported up so the nav history can
// restore the exact spot.
export default function VizZone({ target }: { target?: string }) {
  const nav = useNav();
  const [entries, setEntries] = useState<VizEntry[]>([]);
  const [selected, setSelected] = useState<VizEntry | null>(null);
  const [data, setData] = useState<unknown>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void listViz().then(setEntries);
  }, []);

  // initial + target-driven selection (guarded so the report→target round-trip can't loop)
  useEffect(() => {
    if (entries.length === 0) return;
    if (target) {
      const want = entries.find((e) => e.path === target && e.supported);
      if (want && want.path !== selected?.path) { select(want); return; }
    }
    if (!selected) {
      const first = entries.find((e) => e.supported);
      if (first) select(first);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, target]);

  // keep the nav `current` in sync with the open diagram (so a later navigate captures it)
  useEffect(() => {
    if (selected) nav.report({ zone: "viz", viz: selected.path });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const select = (e: VizEntry) => {
    setSelected(e);
    setData(null);
    setError("");
    readViz(e.path)
      .then((raw) => setData(JSON.parse(raw)))
      .catch((err) => setError(String(err)));
  };

  // vault-mirroring tree: top = first path segment (the lane/area), sub = the rest of the
  // doc path. LIVE entries pin first under their lane. Categories collapse (operator feedback: the flat
  // list became a sift); the selection's category auto-expands.
  const tree = useMemo(() => {
    const out = new Map<string, Map<string, VizEntry[]>>();
    for (const e of entries) {
      const live = e.path.startsWith("live:");
      const top = live ? e.doc.split(" ·")[0] : e.doc.split("/")[0];
      const sub = live ? "LIVE" : e.doc.split("/").slice(1).join("/") || "·";
      const subs = out.get(top) ?? new Map<string, VizEntry[]>();
      const arr = subs.get(sub) ?? [];
      arr.push(e);
      subs.set(sub, arr);
      out.set(top, subs);
    }
    // LIVE sub-group first within each top
    for (const [top, subs] of out) {
      const sorted = new Map<string, VizEntry[]>(
        Array.from(subs.entries()).sort(([a], [b]) =>
          a === "LIVE" ? -1 : b === "LIVE" ? 1 : a.localeCompare(b)),
      );
      out.set(top, sorted);
    }
    return out;
  }, [entries]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (selected) {
      const top = selected.path.startsWith("live:")
        ? selected.doc.split(" ·")[0] : selected.doc.split("/")[0];
      setOpen((o) => (o.has(top) ? o : new Set(o).add(top)));
    }
  }, [selected]);
  const toggle = (top: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (n.has(top)) n.delete(top); else n.add(top);
      return n;
    });
  const title = (data as { title?: string } | null)?.title;
  const subtitle = (data as { subtitle?: string } | null)?.subtitle;

  return (
    <div className="viz-zone">
      <nav className="viz-rail bezel">
        {Array.from(tree.entries()).map(([top, subs]) => {
          const count = Array.from(subs.values()).reduce((n, a) => n + a.length, 0);
          const isOpen = open.has(top);
          return (
            <div key={top} className="viz-tree-top">
              <button className="viz-top-toggle" onClick={() => toggle(top)}>
                <span className="chev">{isOpen ? "▾" : "▸"}</span>
                {top.toUpperCase().replace(/-/g, " ")}
                <span className="viz-top-count">{count}</span>
              </button>
              {isOpen && Array.from(subs.entries()).map(([sub, items]) => (
                <div key={sub} className="viz-doc">
                  {sub !== "·" && (
                    <span className="viz-doc-label" title={sub}>
                      {sub === "LIVE" ? "LIVE" : `${sub} /`}
                    </span>
                  )}
                  {items.map((e) => (
                    <button key={e.path}
                            className={[
                              "viz-item",
                              selected?.path === e.path ? "active" : "",
                              e.supported ? "" : "unsupported",
                            ].join(" ")}
                            disabled={!e.supported}
                            title={e.title || e.name}
                            onClick={() => select(e)}>
                      <span className="viz-item-name">{e.name.replace(/-/g, " ")}</span>
                      <span className="viz-item-type">
                        {e.path.startsWith("live:") && <em className="live-chip">LIVE</em>}
                        {e.viz_type.toUpperCase()}
                        {!e.supported && ` · ${PHASE_BY_TYPE[e.viz_type] ?? "TBD"}`}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          );
        })}
        {entries.length === 0 && <p className="empty">NO VIZ DATA DISCOVERED IN THE VAULT</p>}
      </nav>

      <section className="viz-stage bezel">
        {selected && (
          <header className="viz-stage-head">
            <span className="lane-tag">{selected.doc.split("/")[0]}</span>
            <div className="viz-stage-titles">
              <h2>{title || selected.name}</h2>
              {subtitle && <p>{subtitle}</p>}
            </div>
            <span className="surface-when">{selected.path}</span>
            {selected.path.startsWith("live:") && (
              <button onClick={() => select(selected)}>⟳ REFRESH</button>
            )}
          </header>
        )}
        {error && <p className="surface-error">{error}</p>}
        <ErrorBoundary resetKey={selected?.path ?? ""}>
        {data != null && selected?.viz_type === "treemap" && <Treemap data={data as never} />}
        {data != null && selected?.viz_type === "sankey" && <Sankey data={data as never} />}
        {data != null && selected?.viz_type === "pies" && <Donuts data={data as never} />}
        {data != null && selected?.viz_type === "line" && <LineChart data={data as never} />}
        {data != null && selected?.viz_type === "matrix" && <Matrix data={data as never} />}
        {data != null && selected?.viz_type === "rank-bar" && <BarChart data={data as never} />}
        {data != null && selected?.viz_type === "compare" && <Radar data={data as never} />}
        {data != null && selected?.viz_type === "scatter" && <Scatter data={data as never} />}
        {data != null && selected?.viz_type === "schedule" && <Schedule data={data as never} />}
        {data != null && selected?.viz_type === "food-bank" && <FoodBank data={data as never} />}
        {data != null && selected?.viz_type === "vest-timeline" && <VestTimeline data={data as never} />}
        {data != null && selected?.viz_type === "ladder" && <Ladder data={data as never} />}
        {data != null && selected?.viz_type === "bead-tree" && <BeadTree data={data as never} />}
        {!selected && <p className="empty">SELECT A DIAGRAM FROM THE RAIL</p>}
        </ErrorBoundary>
      </section>
    </div>
  );
}
