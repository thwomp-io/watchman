// Theme state (menu + multi-theme semantics) — the
// initialization/persistence contract: stored preference wins; no preference follows the OS
// (OS-light = BRIGHT, the canonical light); picking pins an explicit choice; AUTO un-pins;
// legacy "light" reads as "paper"; OS changes re-theme ONLY while unpinned; metas track apply.

import { beforeEach, describe, expect, it } from "vitest";
import {
  clearTheme,
  getTheme,
  initTheme,
  resolveTheme,
  setTheme,
  storedTheme,
  THEME_KEY,
} from "./theme";

// a controllable matchMedia: `matches` answers "(prefers-color-scheme: light)" and captured
// listeners let a test flip the OS scheme mid-flight
let osIsLight = false;
let schemeListeners: ((e: { matches: boolean }) => void)[] = [];
function installMatchMedia() {
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: q.includes("light") ? osIsLight : false,
    addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => schemeListeners.push(cb),
    removeEventListener: () => {},
  });
}
const flipOS = (light: boolean) => {
  osIsLight = light;
  schemeListeners.forEach((cb) => cb({ matches: light }));
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  osIsLight = false;
  schemeListeners = [];
  installMatchMedia();
});

describe("theme init", () => {
  it("OS-light resolves to BRIGHT when nothing is stored (the canonical light)", () => {
    osIsLight = true;
    initTheme();
    expect(getTheme()).toBe("bright");
    expect(document.documentElement.dataset.theme).toBe("bright");
    expect(localStorage.getItem(THEME_KEY)).toBeNull(); // following, not pinning
  });

  it("prefers the stored choice over the OS scheme", () => {
    localStorage.setItem(THEME_KEY, "dark");
    osIsLight = true;
    initTheme();
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("migrates the legacy 'light' preference to paper", () => {
    localStorage.setItem(THEME_KEY, "light"); // a pre-menu build's pin for the paper design
    expect(storedTheme()).toBe("paper");
    initTheme();
    expect(getTheme()).toBe("paper");
  });

  it("ignores a corrupt stored value (falls back to OS)", () => {
    localStorage.setItem(THEME_KEY, "solarized");
    expect(resolveTheme()).toBe("dark");
  });

  it("follows a live OS scheme change while no preference is stored", () => {
    initTheme();
    expect(getTheme()).toBe("dark");
    flipOS(true);
    expect(getTheme()).toBe("bright");
    expect(document.documentElement.dataset.theme).toBe("bright");
  });

  it("does NOT follow the OS once a preference is pinned", () => {
    initTheme();
    setTheme("dark"); // explicit pin
    flipOS(true);
    expect(getTheme()).toBe("dark");
  });
});

describe("menu semantics + persistence", () => {
  it("picking a theme pins and persists it", () => {
    initTheme(); // OS dark
    setTheme("bright");
    expect(getTheme()).toBe("bright");
    expect(localStorage.getItem(THEME_KEY)).toBe("bright");
    setTheme("paper");
    expect(getTheme()).toBe("paper");
    expect(localStorage.getItem(THEME_KEY)).toBe("paper");
  });

  it("AUTO (clearTheme) un-pins and returns to following the OS", () => {
    initTheme();
    setTheme("paper"); // pinned
    osIsLight = true;
    clearTheme(); // the menu's AUTO row
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(getTheme()).toBe("bright"); // back on the OS (light → bright)
    flipOS(false);
    expect(getTheme()).toBe("dark"); // and live-following again
  });

  it("apply re-points the theme-color metas at the active chassis", () => {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#0b0d0e";
    document.head.appendChild(meta);
    try {
      setTheme("paper");
      expect(meta.content).toBe("#d8d0bc"); // paper --graphite-0
      setTheme("bright");
      expect(meta.content).toBe("#0a0d12"); // bright — the black frame chrome
      setTheme("dark");
      expect(meta.content).toBe("#0b0d0e"); // dark --graphite-0
    } finally {
      meta.remove();
    }
  });
});
