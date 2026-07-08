// Self-update seam — the ONE module that touches the updater/process plugins, mirroring how
// transport.ts is the one door to the backend. UpdatePill (the footer affordance) consumes this
// narrow surface, so tests fake it with a vi.mock of THIS module instead of reverse-engineering the
// plugins' IPC shapes, and the web build never grows a second @tauri-apps/* import site per feature.
// (Precedent for a native-only plugin import outside transport.ts: plugin-dialog in App.tsx — both
// are gated behind isTauri() at the call site, so the served browser console never invokes them.)
//
// Native-only, release-only: the Rust side registers the updater/process plugins exclusively in
// release builds (lib.rs), and the base tauri.conf.json ships an EMPTY endpoint list — a dev
// daily-driver points nowhere. Callers treat every rejection as a quiet inline state, never a modal:
// an offline check or an unregistered plugin both surface as "couldn't check", by design.

import { check } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

/** What the footer needs to render an available update — plus the handle to fetch+install it. */
export interface AvailableUpdate {
  version: string;
  notes: string | null;
  /** Download + install; reports cumulative progress (0..1, or null while total size is unknown). */
  downloadAndInstall(onProgress: (fraction: number | null) => void): Promise<void>;
}

/** null → already current. Rejects on any check failure (offline, no endpoint, bad manifest). */
export async function checkForUpdate(): Promise<AvailableUpdate | null> {
  const update = await check();
  if (!update) return null;
  return {
    version: update.version,
    notes: update.body ?? null,
    downloadAndInstall: async (onProgress) => {
      let total: number | null = null;
      let received = 0;
      await update.downloadAndInstall((event) => {
        // The plugin streams Started(contentLength?) / Progress(chunkLength) / Finished; fold that
        // into one cumulative fraction so the pill renders a single terse percentage.
        if (event.event === "Started") total = event.data.contentLength ?? null;
        else if (event.event === "Progress") {
          received += event.data.chunkLength;
          onProgress(total ? Math.min(received / total, 1) : null);
        } else if (event.event === "Finished") onProgress(1);
      });
    },
  };
}

/** Post-install restart. On Windows the NSIS installer already exits the app during install, so
 * this is effectively the macOS/Linux path; harmless if unreachable. */
export const restartApp = (): Promise<void> => relaunch();

/** Signature-verification failures must read differently from "couldn't check" — a bad signature
 * on a downloaded artifact is a refused install, not a connectivity hiccup. The plugin's minisign
 * errors are string-matched here because it exposes no structured error kind over IPC. */
export function isSignatureError(err: unknown): boolean {
  return /signature|minisign/i.test(String(err));
}
