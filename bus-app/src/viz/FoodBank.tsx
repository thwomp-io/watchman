// Interactive food bank — the `food-bank` type ({title, groups:[{key, label}],
// restaurants:[{name, group, area?, cuisine?, price?, status?, note?, ...}]}). Grouped card
// grid; click a card → its full record.

import { useState } from "react";
import JsonView from "../JsonView";

interface Restaurant {
  name: string; group?: string; area?: string; cuisine?: string; price?: string; status?: string;
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

export default function FoodBank({ data }: { data: FoodData }) {
  const [sel, setSel] = useState<Restaurant | null>(null);
  const groups = data.groups ?? [{ key: "", label: "ALL" }];

  return (
    <div className="viz-canvas">
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
                        onClick={() => setSel(sel?.name === r.name ? null : r)}>
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
      {sel ? (
        <div className="viz-detail">
          <span className="section-label">{sel.name}</span>
          <JsonView data={Object.fromEntries(Object.entries(sel).filter(([k]) => k !== "name"))} />
        </div>
      ) : (
        <p className="viz-hint">CLICK A CARD FOR ITS FULL RECORD</p>
      )}
    </div>
  );
}
