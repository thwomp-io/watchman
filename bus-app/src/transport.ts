// The transport seam: api.ts is the webview's single door onto the backend,
// and this module is the single door's HINGE — every api.ts call goes through a Transport, which is
// either the Tauri IPC bridge (the native console, unchanged behavior) or an HTTP client (the
// browser-served web console, served over HTTP by the bus server). Keeping BOTH implementations
// behind one two-method interface is what makes maintaining the two form-factors nearly free; protect
// this seam in review — a feature that imports @tauri-apps/* outside this file (or api.ts's event
// re-exports) forks the door and breaks the web build silently.

import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen } from "@tauri-apps/api/event";

// Structurally identical to Tauri's UnlistenFn — redefined here so the Transport interface (and any
// HTTP-side code) never needs a type import from the Tauri packages.
export type Unlisten = () => void;

export interface Transport {
  /** Run a backend command (a commands.rs name) with camelCase args; resolves its deserialized result. */
  invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T>;
  /** Subscribe to a backend push event (bus-updated / bus-select / vault-changed). */
  listen(event: string, cb: (payload: unknown) => void): Promise<Unlisten>;
}

// --- native: the Tauri IPC bridge -------------------------------------------------------------------
// Exactly the pre-refactor behavior — same modules, same call shapes — so the existing vitest mock of
// @tauri-apps/api/core|event keeps intercepting at the same boundary.
export const tauriTransport: Transport = {
  invoke: <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> => tauriInvoke<T>(cmd, args),
  listen: (event, cb) => tauriListen(event, (e) => cb(e.payload)),
};

// --- web: the HTTP client ----------------------------------------------------------------------------
// Wire contract (mirrored by the server's RPC door): one RPC-style
// route per backend command — `POST {base}/api/invoke/{cmd}` with the camelCase args as the JSON body,
// response = the command's JSON result. This is the most literal mirror of commands.rs (one dispatch
// table, allowlisted per command server-side) and keeps parity drift structurally hard: a new command
// is one api.ts wrapper + one server-table row, never a bespoke route shape. It extends the shipped
// bus server (create_app() — same bind, same bearer token): one server, one token, one bind.
export interface HttpTransportOptions {
  /** API origin; "" = same-origin (the served-console case — the server serves the React dist/). */
  base?: string;
  /** Bearer token (the bus server's auth model). Resolved at call time — URL param, storage, or prompt. */
  token?: string;
}

/** Web-mode token bootstrap: `?token=<t>` on first visit → localStorage → stripped from the URL
 * (so the secret never lingers in the address bar / history beyond the bootstrap navigation).
 * The operator opens `http://<node>:8787/?token=$(cat ~/.config/harness/bus-token)` once per
 * device; thereafter localStorage carries it. */
let cachedToken: string | undefined; // session memo — the primary source once bootstrapped
export function bootstrapToken(): string | undefined {
  if (cachedToken) return cachedToken;
  try {
    const url = new URL(window.location.href);
    const fromUrl = url.searchParams.get("token");
    if (fromUrl) {
      cachedToken = fromUrl;
      url.searchParams.delete("token");
      window.history.replaceState(null, "", url.toString());
    } else {
      // storage is BEST-EFFORT persistence across reloads, never load-bearing: environments
      // without it (private browsing, jsdom where Node's experimental stub throws) still work
      // via the ?token= bootstrap + the in-memory memo.
      try {
        cachedToken = window.localStorage.getItem("watchman.token") ?? undefined;
      } catch {
        /* storage unavailable — memo-only session */
      }
    }
    if (cachedToken) {
      try {
        window.localStorage.setItem("watchman.token", cachedToken);
      } catch {
        /* storage unavailable — memo-only session */
      }
    }
    return cachedToken;
  } catch {
    return undefined; // non-browser contexts degrade quietly
  }
}

// ONE prompt per page-load, shared across every concurrent 401 (a dashboard fans out a dozen
// widget calls at once — without the memo they'd stampede a dozen dialogs). Memoized even on
// cancel: the operator said no once; stay degraded quietly until reload. LOUD, not silently
// dark — the token-recovery lesson: case 1 was the MISSING token; case 2 was a WRONG
// token ($-polluted URL paste), which the creation-time prompt never caught. 401-driven
// prompting catches both, plus stale stored tokens after a rotation.
let tokenPrompt: Promise<string | undefined> | null = null;
function promptForToken(reason: string): Promise<string | undefined> {
  tokenPrompt ??= Promise.resolve().then(() => {
    try {
      const entered = window.prompt(reason);
      const tok = entered?.trim() || undefined;
      if (tok) {
        cachedToken = tok;
        try {
          window.localStorage.setItem("watchman.token", tok);
        } catch {
          /* storage unavailable — memo-only session */
        }
      }
      return tok;
    } catch {
      return undefined; // no prompt in this context (jsdom, non-interactive)
    }
  });
  return tokenPrompt;
}
export function resetTokenPrompt(): void {
  tokenPrompt = null; // test hook
}
export function resetTokenMemo(): void {
  cachedToken = undefined; // test hook
}

// The refresh seam: HTTP has no push channel, so web-mode subscriptions are POLLING
// emulations of the native events — bus-updated re-derives from unread_count on the native
// poller's own cadence (30s), vault-changed fires a coarse re-list tick (60s; subscribers re-fetch
// listings, which is what the native fs-watch event makes them do anyway). bus-select stays a
// no-op (it's the tray-menu → window jump — there is no tray in a browser).
const POLL_BUS_MS = 30_000;
const POLL_VAULT_MS = 60_000;

export function httpTransport(opts: HttpTransportOptions = {}): Transport {
  const base = opts.base ?? "";
  // The poll_once parity hook: natively, ack_events triggers an immediate poller
  // pass so the tray badge reflects the ack instantly; web mode's 30s poll made a successful ack
  // look inert (the "read-only on web" misread). The transport re-emits bus-updated right after
  // a successful ack — zones stay untouched, the emulation lives where the events live.
  let busTick: (() => void) | null = null;
  // Token resolves AT CALL TIME (explicit option > the live memo) — a 401-prompted replacement
  // must apply to every in-flight consumer, not just calls made after some future reconstruction.
  const post = async (cmd: string, args?: Record<string, unknown>): Promise<Response> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const tok = opts.token ?? cachedToken;
    if (tok) headers.Authorization = `Bearer ${tok}`;
    return fetch(`${base}/api/invoke/${cmd}`, {
      method: "POST",
      headers,
      body: JSON.stringify(args ?? {}),
    });
  };
  const invoke = async <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
    let res = await post(cmd, args);
    const finish = <R,>(v: R): R => {
      if (cmd === "ack_events") busTick?.();
      return v;
    };
    if (res.status === 401 && !opts.token) {
      // rejected (missing, wrong, or rotated-out) → one shared prompt, then ONE retry
      const replaced = await promptForToken(
        "Watchman bus token was missing or rejected — paste the current token " +
          "(cat ~/.config/harness/bus-token on the serving node):",
      );
      if (replaced) res = await post(cmd, args);
    }
    if (!res.ok) {
      // Mirror the Tauri failure shape (invoke rejects with the command's error string) so callers'
      // catch-paths behave identically on both transports.
      throw new Error(`${cmd}: ${res.status} ${await res.text().catch(() => res.statusText)}`);
    }
    return finish((await res.json()) as T);
  };
  return {
    invoke,
    listen: async (event, cb) => {
      if (event === "bus-updated") {
        const tick = () => void invoke<number>("unread_count").then(cb).catch(() => {});
        busTick = tick; // the ack parity hook re-fires this (poll_once, emulated)
        const id = setInterval(tick, POLL_BUS_MS);
        tick(); // prime immediately — a fresh page shouldn't wait 30s for its badge
        return () => {
          busTick = null;
          clearInterval(id);
        };
      }
      if (event === "vault-changed") {
        const id = setInterval(() => cb(undefined), POLL_VAULT_MS);
        return () => clearInterval(id);
      }
      return () => {}; // bus-select + future native-only events: quiet no-op
    },
  };
}

// --- selection ---------------------------------------------------------------------------------------
// Tauri v2 injects __TAURI_INTERNALS__ into its webview; a plain browser doesn't have it. The vitest
// setup stubs it so jsdom tests keep exercising the (mocked) Tauri path. VITE_TRANSPORT=http|tauri
// overrides detection for dev against a local RPC server without leaving the Tauri shell.
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function resolveTransport(): Transport {
  const forced = import.meta.env?.VITE_TRANSPORT as string | undefined;
  const http = () => {
    // Run the bootstrap for its SIDE EFFECT (ingest ?token= / storage into the memo) but do NOT
    // pass it as an explicit token: an explicit opts.token means "the owner decided — never
    // prompt", which silently disabled 401-recovery for a stored-but-wrong token (case 3:
    // the $-polluted token lived in localStorage, so the prompt never fired). Call-time memo
    // resolution + an undefined opts.token keeps the 401→prompt→retry path live.
    bootstrapToken();
    return httpTransport({ base: import.meta.env?.VITE_API_BASE ?? "" });
  };
  if (forced === "http") return http();
  if (forced === "tauri") return tauriTransport;
  return isTauri() ? tauriTransport : http();
}

// Lazy singleton: resolved on first api.ts call (not at module load), so test setup / env stubs are in
// place before selection runs. setTransport is the test hook for driving api.ts through a fake.
let active: Transport | null = null;
export function transport(): Transport {
  return (active ??= resolveTransport());
}
export function setTransport(t: Transport | null): void {
  active = t;
}
