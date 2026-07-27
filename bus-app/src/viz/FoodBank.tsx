// Interactive food bank (v2) — the `food-bank` type ({title,
// groups:[{key, label}], restaurants:[{name, group, area?, cuisine?, price?, status?, meals?,
// hostfit?, confirm?, note?, links?:[{label,url}], ...}]}). Grouped card grid; hover a card →
// the glance tip (fit line + source links, sticky via the hover-bridge); click → the full
// structured record with a source-links row. `links` is an optional contract field the static
// twin (viz/render.js renderFoodBank) ignores — two consumers, one contract, grown
// backward-compatibly.

import { useRef, useState } from "react";
import JsonView from "../JsonView";

export interface VizLink { label: string; url: string }
interface Restaurant {
  name: string; group?: string; area?: string; cuisine?: string; price?: string; status?: string;
  meals?: string[] | string; hostfit?: string; confirm?: string; note?: string; links?: VizLink[];
  [k: string]: unknown;
}
interface FoodData {
  title?: string; subtitle?: string;
  groups?: { key: string; label: string }[];
  restaurants: Restaurant[];
}

const STATUS_CLASS: Record<string, string> = {
  confirmed: "pos", booked: "pos", open: "pos",
  verify: "warn", call: "warn", fuzzy: "warn",
  closed: "neg", gone: "neg",
};
// keys the structured detail card renders itself; JsonView shows only the residual
const RENDERED = new Set(["name", "group", "area", "cuisine", "price", "status", "meals", "hostfit", "confirm", "note", "links"]);

const mealsOf = (r: Restaurant) => (Array.isArray(r.meals) ? r.meals.join(" / ") : r.meals);

export default function FoodBank({ data }: { data: FoodData }) {
  const [sel, setSel] = useState<Restaurant | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; r: Restaurant } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const groups = data.groups ?? [{ key: "", label: "ALL" }];

  // hover-bridge (the Scatter pattern): the tip carries clickable source links, so moving
  // cursor card→tip must not dismiss it — grace timer on leave, cancelled on tip-enter.
  const clearTimer = useRef<number | null>(null);
  const cancelClear = () => { if (clearTimer.current) { clearTimeout(clearTimer.current); clearTimer.current = null; } };
  const scheduleClear = () => { cancelClear(); clearTimer.current = window.setTimeout(() => setTip(null), 140); };
  const showTip = (e: React.MouseEvent, r: Restaurant) => {
    cancelClear();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ x: Math.min(e.clientX - rect.left + 14, Math.max(8, rect.width - 330)), y: e.clientY - rect.top + 10, r });
  };

  const residual = (r: Restaurant) =>
    Object.fromEntries(Object.entries(r).filter(([k]) => !RENDERED.has(k)));

  return (
    <div className="viz-canvas" ref={canvasRef}>
      {groups.map((g) => {
        const members = data.restaurants.filter((r) => !g.key || r.group === g.key);
        if (members.length === 0) return null;
        return (
          <section key={g.key} className="food-group">
            <span className="section-label">{g.label}</span>
            <div className="food-grid">
              {members.map((r) => (
                <button key={r.name}
                        className={`food-card ${sel?.name === r.name ? "selected" : ""}`}
                        onClick={() => setSel(sel?.name === r.name ? null : r)}
                        onMouseMove={(e) => showTip(e, r)}
                        onMouseLeave={scheduleClear}>
                  <span className="food-name">{r.name}</span>
                  <span className="food-meta">
                    {[r.cuisine, r.area, r.price].filter(Boolean).join(" · ")}
                  </span>
                  {r.status && (
                    <span className={`food-status ${STATUS_CLASS[r.status] ?? ""}`}>
                      {r.status.toUpperCase()}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </section>
        );
      })}

      {tip && (
        <div className={`viz-tip${tip.r.links?.length ? " has-link" : ""}`}
             style={{ left: tip.x, top: tip.y }}
             onMouseEnter={cancelClear} onMouseLeave={scheduleClear}>
          <div className="viz-tip-head">
            {tip.r.status && <span className={`food-status ${STATUS_CLASS[tip.r.status] ?? ""}`}>{tip.r.status.toUpperCase()}</span>}
            <strong>{tip.r.name}</strong>
          </div>
          <div className="viz-tip-rows">
            {tip.r.cuisine && (<><span className="k">cuisine</span><span className="v">{tip.r.cuisine}</span></>)}
            {tip.r.area && (<><span className="k">where</span><span className="v">{tip.r.area}</span></>)}
            {tip.r.price && (<><span className="k">price</span><span className="v">{tip.r.price}</span></>)}
            {mealsOf(tip.r) && (<><span className="k">meals</span><span className="v">{mealsOf(tip.r)}</span></>)}
          </div>
          {(tip.r.hostfit || tip.r.note) && <div className="viz-tip-detail">{tip.r.hostfit || tip.r.note}</div>}
          {(tip.r.links ?? []).slice(0, 2).map((l) => (
            <a key={l.url} className="viz-tip-link" href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a>
          ))}
        </div>
      )}

      {sel ? (
        <div className="viz-detail">
          <span className="section-label">
            {sel.name}
            {sel.status && <span className={`food-status ${STATUS_CLASS[sel.status] ?? ""}`}> · {sel.status.toUpperCase()}</span>}
          </span>
          <p className="food-detail-meta">{[sel.cuisine, sel.area, sel.price, mealsOf(sel)].filter(Boolean).join("  ·  ")}</p>
          <div className="viz-detail-rows">
            {sel.hostfit && (<><span className="k">fit</span><span className="v">{sel.hostfit}</span></>)}
            {sel.confirm && (<><span className="k">confirm</span><span className="v">{sel.confirm}</span></>)}
            {sel.note && (<><span className="k">note</span><span className="v">{sel.note}</span></>)}
          </div>
          {(sel.links ?? []).length > 0 && (
            <div className="viz-detail-links">
              {sel.links!.map((l) => (
                <a key={l.url} href={l.url} target="_blank" rel="noreferrer">{l.label} ↗</a>
              ))}
            </div>
          )}
          {Object.keys(residual(sel)).length > 0 && <JsonView data={residual(sel)} />}
        </div>
      ) : (
        <p className="viz-hint">HOVER A CARD FOR THE GLANCE TIP · CLICK FOR ITS FULL RECORD</p>
      )}
    </div>
  );
}
