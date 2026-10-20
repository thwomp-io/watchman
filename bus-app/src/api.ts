// Backend-call wrappers — the webview's only surface onto the backend. Data calls route through the
// transport seam (src/transport.ts) to the engine's served console; the handful of desktop-only
// calls (connection settings, the folder picker) go to the shell. Consumers import these typed
// wrappers and never touch the transport directly.

import { shellInvoke, shellListen, transport, type Unlisten } from "./transport";
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
// The BADGE count — alert+warn only (the severity doctrine: the wire never screams from the tray)
export const urgentUnreadCount = (): Promise<number> => invoke("urgent_unread_count");
export const distinctMeta = (): Promise<DistinctMeta> => invoke("distinct_meta");
export const appVersion = (): Promise<string> => invoke("app_version");
// symbols_from resolver: a widget's dynamic chip list off the portfolio seed.
export const listPortfolioSymbols = (lane: string, id: string): Promise<string[]> =>
  invoke("list_portfolio_symbols", { lane, id });
export const getConfig = (): Promise<AppConfig> => invoke("get_config");
/** Desktop app only: point the shell at a remote served console (url + token), or clear it (null)
    to go back to its own local server. The shell reloads the window onto the new server. */
export const setBusConfig = (url: string | null, token: string | null): Promise<void> =>
  shellInvoke("set_bus_config", { url, token });
/** The resolved user overlay (harness.yaml) as raw text — the Personal tabs parse client-side. */
export const getUserOverlay = (): Promise<{ text: string; source: string; path: string | null }> =>
  invoke("get_user_overlay");
/** Desktop app only: probe a remote server before switching to it ("ok — bus reachable, N unread"). */
export const testBusConnection = (url: string, token: string): Promise<string> =>
  shellInvoke("test_bus_connection", { url, token });
/** Desktop app only: the shell's own connection state (local server or a remote URL). */
export interface ShellInfo { mode: "local" | "remote"; bus_url: string | null; bus_token_set: boolean; version: string }
export const shellInfo = (): Promise<ShellInfo> => shellInvoke("shell_info");
/** Desktop app only: a native folder picker; resolves the chosen dir (or null if cancelled). */
export const pickPackFolder = (): Promise<string | null> => shellInvoke("pick_pack_folder");
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

// An opened catalyst's article card (lead image, outlet, dek). Takes the bus EVENT id — the server
// fetches only the link a producer stored on that event. Served console only; the desktop app has no
// such command, so the call rejects there and the reader simply shows no picture.
export interface ArticleCard { url: string; final_url?: string; image?: string; site?: string; dek?: string; error?: string }
export const articleCard = (id: number): Promise<ArticleCard> => invoke("article_card", { id });

export const listDashboards = (): Promise<Dashboard[]> => invoke("list_dashboards");
// Dashboard Studio save. A write: 403 unless the serve enables it (get_config reports `writes`).
export const saveDashboard = (dashboard: Dashboard): Promise<void> =>
  invoke("save_dashboard", { dashboard });
// Snap a lane back to its compiled built-in (banks the replaced state to .backups/ first).
export const resetDashboard = (lane: string): Promise<Dashboard> =>
  invoke("reset_dashboard", { lane });
export const runWidget = (lane: string, id: string, symbol?: string | null): Promise<string> =>
  invoke("run_widget", { lane, id, symbol: symbol ?? null });

// Weight-pack scenario-switcher: list the bundled sample packs, read/set the active one (a write the
// serve must enable). The webview re-renders the data zones so panels pick up the swap.
export interface PackInfo { name: string; path: string; lanes: string[]; }
export const listPacks = (): Promise<PackInfo[]> => invoke("list_packs");
export const getActivePack = (): Promise<string | null> => invoke("get_active_pack");
export const setActivePack = (pack: string | null): Promise<void> =>
  invoke("set_active_pack", { pack });

// Push-event subscriptions ride the same transport (Tauri events natively; the web console's refresh
// emulation is the polling seam). UnlistenFn-compatible: callers keep treating the result as `() => void`.
export const onBusUpdated = (cb: (unread: number) => void): Promise<Unlisten> =>
  transport().listen("bus-updated", (payload) => cb(payload as number));
// The tray's jump-to-event: a shell event (there is no tray in a browser).
export const onBusSelect = (cb: (id: number) => void): Promise<Unlisten> =>
  shellListen("bus-select", (payload) => cb(payload as number));
export const onVaultChanged = (cb: () => void): Promise<Unlisten> =>
  transport().listen("vault-changed", () => cb());

