// Shared viz primitives — one categorical phosphor set across all interactive
// types (mirrors render.js THEMES.instrument.categorical; keep in sync).

import { useEffect, useRef, useState } from "react";
import { useTheme, type Theme } from "../theme";

// The DARK set — the original phosphor palette, byte-for-byte (the Rust/static-SVG engine's
// instrument theme deliberately stays on these for corpus embeds). Also mirrored as
// --cat-0..--cat-9 in App.css's dark token block (keep in sync).
export const COLORS = [
  "#e8a33d", "#59b9ff", "#7dd6a0", "#ff8a7a", "#b48ead", "#6fc7c0",
  "#d98fb6", "#c9d65e", "#ffb454", "#8fbcbb",
];

// The LIGHT set — SAME hue order (adjacent distinctness was tuned; reorder in neither place),
// luminance dropped + saturation raised so every series holds ≥3:1 on the paper panel face:
// drafting-ink plots, not pastels. Mirrors --cat-0..--cat-9 in the light token block.
export const COLORS_LIGHT = [
  "#a06c08", "#1668a8", "#23784a", "#b44a2e", "#7b5484", "#1f7a72",
  "#a84a77", "#77820e", "#a85c00", "#4e7876",
];

// The BRIGHT set — imperial crimson leads (the accent's chart seat; the 0↔3 swap with gold
// keeps the set + adjacent distinctness while un-browning the lead series — the gold lead
// read as a brown wash against the red/white/black grammar). Mirrors --cat-0..--cat-9 in the bright token block.
export const COLORS_BRIGHT = [
  "#c8102e", "#1f5bc4", "#157a3a", "#8f7500", "#6d3fa8", "#0f766e",
  "#b91c74", "#5c7300", "#b45309", "#3b6e78",
];

// The creative fleet — one set per theme, accent-led (cat-0 = each theme's accent
// slot), tuned for its own chassis. Each mirrors its --cat-0..9 token run in App.css.
export const COLORS_BY_THEME: Partial<Record<Theme, string[]>> = {
  paper: COLORS_LIGHT,
  bright: COLORS_BRIGHT,
  phosphor: ["#3ddc84","#39c5cf","#a3d952","#e0b83d","#7dd6a0","#5fb3e8","#c9d65e","#4ea88a","#ffb454","#8fbcbb"],
  redwatch: ["#ff4f58","#b87ab8","#74c69d","#ff9a4d","#d96a8f","#c2554e","#ff8a90","#a8666e","#e0847a","#8f5f74"],
  fjord: ["#88c0d0","#b48ead","#a3be8c","#bf616a","#5e81ac","#8fbcbb","#d08770","#ebcb8b","#81a1c1","#6e8aa8"],
  outrun: ["#ff2d95","#00e5ff","#00ffa3","#ffd319","#c86bff","#4dc9ff","#ff6b35","#8f7fff","#39ff88","#ff5c8a"],
  abyss: ["#2dd4bf","#4aa8ff","#34d399","#ff5d6e","#9d7bea","#67e8f9","#e879a0","#a3d952","#f0b429","#5eaaa8"],
  dusk: ["#b794d4","#7aa2f7","#82c99a","#e06c75","#d19a66","#6fc7c0","#d98fb6","#a5b85c","#e8a87c","#8b93c4"],
  solar: ["#d97706","#0e7490","#4d7c0f","#c53030","#7e22ce","#0f766e","#be185d","#a16207","#ea580c","#64748b"],
  mono: ["#333333","#0057b8","#1a7a3a","#c00000","#666699","#008080","#993366","#667a00","#b35900","#5c7080"],
};

export const getColors = (theme: Theme): string[] => COLORS_BY_THEME[theme] ?? COLORS;

// The hook every interactive viz uses for series color — subscribing via useTheme means a
// baseplate toggle re-renders the chart onto the right palette immediately (SVG fills are
// attributes, not CSS custom-property consumers, so they can't ride the token swap on their own).
export function useCatColors(): string[] {
  return getColors(useTheme());
}

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
