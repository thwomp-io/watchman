// Transport-seam tests. Three concerns: (1) selection picks the right
// implementation per context, (2) the HTTP client speaks the server's wire contract
// (POST /api/invoke/{cmd}, JSON args, bearer token, Tauri-shaped rejection on non-2xx), and
// (3) api.ts wrappers genuinely ride the seam (setTransport swaps the backend for the whole surface).
import { afterEach, describe, expect, it, vi } from "vitest";

import { unreadCount } from "./api";
import {
  bootstrapToken, httpTransport, isTauri, resetTokenMemo, resetTokenPrompt, resolveTransport,
  setTransport, tauriTransport, type Transport,
} from "./transport";

const win = window as unknown as Record<string, unknown>;

afterEach(() => {
  setTransport(null); // drop any test-installed transport so other suites resolve fresh
  win.__TAURI_INTERNALS__ ??= {}; // restore the setup.ts stub if a test deleted it
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("selection", () => {
  it("detects the Tauri webview via __TAURI_INTERNALS__ and resolves the Tauri transport", () => {
    expect(isTauri()).toBe(true); // setup.ts stubs the marker for the whole suite
    expect(resolveTransport()).toBe(tauriTransport);
  });

  it("resolves the HTTP transport in a plain browser (no marker)", () => {
    delete win.__TAURI_INTERNALS__;
    expect(isTauri()).toBe(false);
    const t = resolveTransport();
    expect(t).not.toBe(tauriTransport);
    expect(typeof t.invoke).toBe("function");
  });

  it("honors the VITE_TRANSPORT=http override even inside the Tauri shell", () => {
    vi.stubEnv("VITE_TRANSPORT", "http");
    expect(resolveTransport()).not.toBe(tauriTransport); // marker present, override wins
  });
});

describe("httpTransport", () => {
  it("POSTs /api/invoke/{cmd} with JSON args + bearer token and returns the parsed result", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(42), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const t = httpTransport({ base: "http://bus-host:8787", token: "tok-abc" });
    const result = await t.invoke<number>("unread_count", { lane: "finance" });

    expect(result).toBe(42);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://bus-host:8787/api/invoke/unread_count");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc");
    expect(JSON.parse(init.body as string)).toEqual({ lane: "finance" });
  });

  it("defaults to same-origin, no auth header, {} body when args are omitted", async () => {
    const fetchMock = vi.fn(async () => new Response("null", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await httpTransport().invoke("list_packs");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/invoke/list_packs");
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("rejects with the command name + status on a non-2xx (the Tauri-shaped failure)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("verb not allowlisted", { status: 403 })));
    await expect(httpTransport().invoke("run_widget")).rejects.toThrow(
      /run_widget: 403 verb not allowlisted/,
    );
  });

  it("subscribes as a quiet no-op (push emulation is the polling seam)", async () => {
    const unlisten = await httpTransport().listen("bus-updated", () => {});
    expect(() => unlisten()).not.toThrow();
  });
});

describe("api.ts rides the seam", () => {
  it("routes wrappers through whatever transport is installed", async () => {
    const calls: Array<[string, unknown]> = [];
    const fake: Transport = {
      invoke: async <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
        calls.push([cmd, args]);
        return 7 as T;
      },
      listen: async () => () => {},
    };
    setTransport(fake);
    await expect(unreadCount()).resolves.toBe(7);
    expect(calls).toEqual([["unread_count", undefined]]);
  });
});

describe("web-mode bootstrap + refresh seam", () => {
  it("captures ?token=, strips it from the URL, and memoizes it", () => {
    resetTokenMemo();
    window.history.replaceState(null, "", "/?token=sekrit&tab=dash");
    expect(bootstrapToken()).toBe("sekrit");
    expect(window.location.search).toBe("?tab=dash"); // secret gone from the address bar
    expect(bootstrapToken()).toBe("sekrit"); // later calls ride the memo (storage is best-effort)
    resetTokenMemo();
  });

  it("emulates bus-updated by polling unread_count (primed immediately)", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response("3", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const seen: unknown[] = [];
    const unlisten = await httpTransport().listen("bus-updated", (n) => seen.push(n));
    await vi.advanceTimersByTimeAsync(10); // the priming tick's microtasks
    expect(seen).toEqual([3]);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(seen).toEqual([3, 3]);
    unlisten();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(seen.length).toBe(2); // unlisten actually stops the poll
    vi.useRealTimers();
  });

  it("fires vault-changed on a coarse tick and no-ops bus-select", async () => {
    vi.useFakeTimers();
    let ticks = 0;
    const unlisten = await httpTransport().listen("vault-changed", () => ticks++);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(ticks).toBe(1);
    unlisten();
    const noop = await httpTransport().listen("bus-select", () => ticks++);
    expect(() => noop()).not.toThrow();
    vi.useRealTimers();
  });
});

describe("401-driven token prompt (wrong/rotated tokens)", () => {
  it("prompts once on 401, retries with the entered token, and shares one prompt across calls", async () => {
    resetTokenMemo();
    resetTokenPrompt();
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const auth = (init.headers as Record<string, string>).Authorization;
      return auth === "Bearer fresh-tok"
        ? new Response("5", { status: 200 })
        : new Response("bad token", { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const promptMock = vi.fn(() => "fresh-tok");
    vi.stubGlobal("prompt", promptMock);
    window.prompt = promptMock as unknown as typeof window.prompt;

    const t = httpTransport(); // no explicit token → the 401 path owns recovery
    const [a, b] = await Promise.all([t.invoke<number>("unread_count"), t.invoke<number>("list_events")]);
    expect(a).toBe(5);
    expect(b).toBe(5);
    expect(promptMock).toHaveBeenCalledTimes(1); // concurrent 401s share ONE dialog
    resetTokenMemo();
    resetTokenPrompt();
  });

  it("cancelled prompt degrades to the original 401 rejection (told once, then quiet)", async () => {
    resetTokenMemo();
    resetTokenPrompt();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("bad token", { status: 401 })));
    const promptMock = vi.fn(() => null);
    window.prompt = promptMock as unknown as typeof window.prompt;
    await expect(httpTransport().invoke("unread_count")).rejects.toThrow(/401/);
    await expect(httpTransport().invoke("unread_count")).rejects.toThrow(/401/);
    expect(promptMock).toHaveBeenCalledTimes(1); // no re-prompt stampede after a cancel
    resetTokenMemo();
    resetTokenPrompt();
  });

  it("an explicitly-constructed token never prompts (the transport owner decided)", async () => {
    resetTokenPrompt();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("bad", { status: 401 })));
    const promptMock = vi.fn(() => "x");
    window.prompt = promptMock as unknown as typeof window.prompt;
    await expect(httpTransport({ token: "fixed" }).invoke("x")).rejects.toThrow(/401/);
    expect(promptMock).not.toHaveBeenCalled();
  });
});
