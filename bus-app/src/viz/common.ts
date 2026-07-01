// Shared viz primitives — one categorical phosphor set across all interactive
// types (mirrors render.js THEMES.instrument.categorical; keep in sync).

import { useEffect, useRef, useState } from "react";

export const COLORS = [
  "#e8a33d", "#59b9ff", "#7dd6a0", "#ff8a7a", "#b48ead", "#6fc7c0",
  "#d98fb6", "#c9d65e", "#ffb454", "#8fbcbb",
];

export const fmtNum = (v: number): string =>
  v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}k`
  : Number.isInteger(v) ? v.toFixed(0) : v.toFixed(2);

export interface Tip { x: number; y: number; text: string }

// Responsive sizing: measure the container so a viz renders its viewBox to the
// ACTUAL tile size — fills exactly, no letterbox, no flex-collapse. `aspect` (h/w) is the fallback
// height when the container has no fixed height (e.g. the VizZone full-size context); the dashboard
// gives viz tiles a fixed height, so there the measured height wins.
export function useMeasure(aspect = 0.34) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: Math.round(900 * aspect) });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth || 900;
      const h = el.clientHeight > 60 ? el.clientHeight : Math.round(w * aspect);
      setSize({ width: w, height: h });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);
  return { ref, width: size.width, height: size.height };
}
