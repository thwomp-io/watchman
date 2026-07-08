// Theme state — N themes over
// ONE token vocabulary. The CSS carries every design (App.css: `:root` = dark,
// `:root[data-theme="paper"]` = the vintage engineering-paper sibling,
// `:root[data-theme="bright"]` = the clean white/black console); this module owns WHICH one is
// active and keeps everything honest about it:
//
//   - persistence: localStorage "watchman.theme" holds an EXPLICIT user choice only. No key
//     = follow the OS (prefers-color-scheme), live — flipping the OS re-themes the console
//     until the user pins a preference with the baseplate menu. OS-light resolves to BRIGHT
//     (the canonical light); paper is a deliberate pick, never an ambient default.
//   - legacy: the original two-theme build stored "light" for what is now "paper" — reads
//     migrate transparently so a pinned pre-menu preference survives the upgrade.
//   - first paint: index.html carries a tiny inline script that applies the same resolution
//     BEFORE the bundle loads, so a light-theme user never sees a dark flash (and vice versa).
//   - the door for React: useTheme() subscribes components (the viz layer re-picks its
//     categorical palette on change); everything else re-themes for free through the CSS vars.
//   - PWA chrome: the theme-color metas are re-pointed on every apply so the browser/OS chrome
//     matches the active chassis, not just the OS default.

import { useSyncExternalStore } from "react";

export type Theme =
  | "dark" | "bright" | "paper"
  | "phosphor" | "redwatch" | "fjord" | "outrun" | "abyss" | "dusk" | "solar" | "mono";

const ALL_THEMES: readonly Theme[] = [
  "dark", "bright", "paper", "phosphor", "redwatch", "fjord", "outrun", "abyss", "dusk", "solar", "mono",
];

export const THEME_KEY = "watchman.theme";

/** The baseplate menu's roster — value + the engraved label it wears. */
export const THEMES: ReadonlyArray<{ value: Theme; label: string }> = [
  // the three utilitarian daily drivers first, then the creative fleet
  { value: "dark", label: "NIGHT" },
  { value: "bright", label: "BRIGHT" },
  { value: "paper", label: "PAPER" },
  { value: "phosphor", label: "PHOSPHOR" },
  { value: "redwatch", label: "REDWATCH" },
  { value: "fjord", label: "FJORD" },
  { value: "abyss", label: "ABYSS" },
  { value: "dusk", label: "DUSK" },
  { value: "outrun", label: "OUTRUN" },
  { value: "solar", label: "SOLAR" },
  { value: "mono", label: "MONO" },
];

/* the chassis "app well" tone per theme — MUST match --graphite-0 in App.css (keep in sync) */
const CHROME_COLOR: Record<Theme, string> = {
  dark: "#0b0d0e",
  paper: "#d8d0bc",
  bright: "#0a0d12", // the black frame — PWA chrome hugs the masthead, not the well
  phosphor: "#030805",
  redwatch: "#0a0304",
  fjord: "#21252e",
  outrun: "#120821",
  abyss: "#04101c",
  dusk: "#191423",
  solar: "#ecdfc3",
  mono: "#e6e6e6",
};

const isTheme = (v: unknown): v is Theme => ALL_THEMES.includes(v as Theme);

/** The explicit stored preference, or null when the console follows the OS. */
export function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light") return "paper"; // pre-menu builds stored "light" for the paper design
    return isTheme(v) ? v : null;
  } catch {
    return null; // storage denied (private-mode edge) → behave as "no preference"
  }
}

/** What the OS wants right now. Light maps to BRIGHT — the console's canonical light. */
export function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "bright" : "dark";
}

/** The theme that should be (and after apply, is) on screen: preference, else OS. */
export function resolveTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

const listeners = new Set<() => void>();
let current: Theme = "dark";

function apply(theme: Theme): void {
  current = theme;
  document.documentElement.dataset.theme = theme;
  // re-point the PWA/browser chrome color at the active chassis. The two media-scoped metas in
  // index.html cover the pre-JS instant; from here on the ACTIVE theme wins on both so an
  // explicit preference overrides the OS-matched meta too.
  for (const m of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    m.content = CHROME_COLOR[theme];
  }
  listeners.forEach((l) => l());
}

/** Boot: apply preference-else-OS, and track OS changes while no preference is pinned. */
export function initTheme(): void {
  apply(resolveTheme());
  // react to OS scheme changes ONLY while the user hasn't pinned a choice
  const mq = window.matchMedia?.("(prefers-color-scheme: light)");
  mq?.addEventListener?.("change", () => {
    if (storedTheme() === null) apply(systemTheme());
  });
}

export function getTheme(): Theme {
  return current;
}

/** Pin an explicit theme (persisted) — the menu's action. */
export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* storage denied → still applies for this session */
  }
  apply(theme);
}

/** Un-pin: back to following the OS (the menu's AUTO row). */
export function clearTheme(): void {
  try {
    localStorage.removeItem(THEME_KEY);
  } catch {
    /* storage denied → nothing was pinned anyway */
  }
  apply(systemTheme());
}

/** Subscribe a component to the active theme (viz palettes re-resolve on change). */
export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getTheme,
    getTheme,
  );
}
