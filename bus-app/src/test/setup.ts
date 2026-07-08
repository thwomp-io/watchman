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

// This jsdom/node combination ships no localStorage (node's experimental one wants a CLI flag; jsdom's
// is absent under vitest). The theme layer persists the operator's explicit choice there — back it with
// a plain in-memory Map so the persistence tests exercise theme.ts's real read/write logic.
if (globalThis.localStorage == null) {
  const backing = new Map<string, string>();
  const storageStub = {
    getItem: (k: string) => backing.get(k) ?? null,
    setItem: (k: string, v: string) => void backing.set(k, String(v)),
    removeItem: (k: string) => void backing.delete(k),
    clear: () => backing.clear(),
    key: (i: number) => [...backing.keys()][i] ?? null,
    get length() {
      return backing.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: storageStub, configurable: true });
  Object.defineProperty(window, "localStorage", { value: storageStub, configurable: true });
}

// --- the mocked Tauri IPC --------------------------------------------------------------------------
// api.ts is the webview's ONLY door to the backend — every call routes through src/transport.ts, whose
// Tauri implementation is `invoke(cmd, args)` from @tauri-apps/api/core (+ `listen` from /event). Mock
// both modules here so a test stages command→response fixtures and drives the real React render layer.
// The hoisted store is the one bridge that survives vitest's vi.mock hoisting; the setters are attached
// to globalThis for `src/test/mockTauri.ts` to wrap with types.
//
// jsdom has no __TAURI_INTERNALS__, so transport selection (transport.ts isTauri()) would otherwise
// pick the HTTP client and sail past this mock — stub the marker so component tests keep exercising
// the (mocked) Tauri path, exactly like the native webview they simulate. HTTP-path tests bypass this
// by constructing httpTransport() directly (see transport.test.ts).
(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ ??= {};

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
