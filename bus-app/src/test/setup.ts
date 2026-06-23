import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// --- jsdom gaps the webview components touch -------------------------------------------------------
// D3/viz components construct a ResizeObserver; jsdom has none. A no-op stub is enough for the layout
// tests (they don't measure real geometry).
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

// --- the mocked Tauri IPC --------------------------------------------------------------------------
// api.ts is the webview's ONLY door to the Rust shell — every call is `invoke(cmd, args)` from
// @tauri-apps/api/core (+ `listen` from /event). Mock both here so a test stages command→response
// fixtures and drives the real React render layer. The hoisted store is the one bridge that survives
// vitest's vi.mock hoisting; the setters are attached to globalThis for `src/test/mockTauri.ts` to wrap
// with types.
const store = vi.hoisted(() => ({
  handlers: new Map<string, (args: Record<string, unknown>) => unknown>(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, args?: Record<string, unknown>) => {
    const handler = store.handlers.get(cmd);
    if (!handler) throw new Error(`mockTauri: no handler staged for command "${cmd}"`);
    return handler(args ?? {});
  },
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: async () => () => {}, // no-op subscribe → no-op unlisten
}));

(globalThis as unknown as { __mockTauri: unknown }).__mockTauri = {
  set: (cmd: string, handler: (args: Record<string, unknown>) => unknown) =>
    store.handlers.set(cmd, handler),
  setValue: (cmd: string, value: unknown) => store.handlers.set(cmd, () => value),
  reset: () => store.handlers.clear(),
};
