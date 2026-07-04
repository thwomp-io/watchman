// Backend-call wrappers — the webview's only surface onto the backend. Every call routes through the
// transport seam (src/transport.ts): the Tauri IPC bridge in the native console, an HTTP client in the
// browser-served web console. Consumers import these typed wrappers and never touch the
// transport directly — keep it that way; the single door is what keeps the two form-factors in parity.

import { transport, type Unlisten } from "./transport";
import type {
  AppConfig, BusEvent, Dashboard, DirDoc, DistinctMeta, Surface, VaultDoc, VizEntry, WatchmenStatus,
} from "./types";

const invoke = <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> =>
  transport().invoke<T>(cmd, args);

export async function listEvents(opts: {
  unreadOnly?: boolean;
  lane?: string;
  kind?: string;
  limit?: number;
}): Promise<BusEvent[]> {
  return invoke("list_events", {
    unreadOnly: opts.unreadOnly ?? false,
    lane: opts.lane || null,
    kind: opts.kind || null,
    limit: opts.limit ?? 100,
  });
}

export const ackEvents = (ids: number[]): Promise<number> => invoke("ack_events", { ids });
export const unreadCount = (): Promise<number> => invoke("unread_count");
export const distinctMeta = (): Promise<DistinctMeta> => invoke("distinct_meta");
export const appVersion = (): Promise<string> => invoke("app_version");
export const getConfig = (): Promise<AppConfig> => invoke("get_config");
export const runProducer = (id: string): Promise<string> => invoke("run_producer", { id });
export const listSurfaces = (): Promise<Surface[]> => invoke("list_surfaces");
export const runSurface = (id: string): Promise<string> => invoke("run_surface", { id });

// Watch-floor band: reuses the surface seam — the watchmen verb reads the run-log
// (fast, no network), so this is safe to fire on Inbox mount + bus-updated.
export const watchmenStatus = async (): Promise<WatchmenStatus> =>
  JSON.parse(await runSurface("system.watchmen")) as WatchmenStatus;

export const listViz = (): Promise<VizEntry[]> => invoke("list_viz");
export const readViz = (path: string): Promise<string> => invoke("read_viz", { path });

export const listVaultDocs = (): Promise<VaultDoc[]> => invoke("list_vault_docs");
export const readDoc = (path: string): Promise<string> => invoke("read_doc", { path });
export const listVaultDir = (path: string): Promise<DirDoc[]> => invoke("list_vault_dir", { path });
export const readImage = (path: string): Promise<string> => invoke("read_image", { path });

export const listDashboards = (): Promise<Dashboard[]> => invoke("list_dashboards");
export const runWidget = (lane: string, id: string, symbol?: string | null): Promise<string> =>
  invoke("run_widget", { lane, id, symbol: symbol ?? null });

// Weight-pack scenario-switcher: list the bundled sample packs, read/set the active one. Setting it
// persists Rust-side; the webview re-renders the data zones so panels pick up the swap.
export interface PackInfo { name: string; path: string; lanes: string[]; }
export const listPacks = (): Promise<PackInfo[]> => invoke("list_packs");
export const getActivePack = (): Promise<string | null> => invoke("get_active_pack");
export const setActivePack = (pack: string | null): Promise<void> =>
  invoke("set_active_pack", { pack });

// Push-event subscriptions ride the same transport (Tauri events natively; the web console's refresh
// emulation is the polling seam). UnlistenFn-compatible: callers keep treating the result as `() => void`.
export const onBusUpdated = (cb: (unread: number) => void): Promise<Unlisten> =>
  transport().listen("bus-updated", (payload) => cb(payload as number));
export const onBusSelect = (cb: (id: number) => void): Promise<Unlisten> =>
  transport().listen("bus-select", (payload) => cb(payload as number));
export const onVaultChanged = (cb: () => void): Promise<Unlisten> =>
  transport().listen("vault-changed", () => cb());
