// Self-update seam: the one module UpdatePill (the footer affordance) consumes, so tests fake it with
// a vi.mock of THIS module. The updater runs in the desktop shell (Rust); the page asks it through
// the shell's IPC, which only the shell's own local origin has.
//
// Release-only: the shell registers the updater in release builds, and the base tauri.conf.json
// ships an EMPTY endpoint list, so a dev daily-driver points nowhere. Callers treat every rejection
// as a quiet inline state, never a modal: offline, no endpoint and "not the desktop app" all read as
// "couldn't check".

import { shellInvoke, shellListen } from "./transport";

/** What the footer needs to render an available update — plus the handle to fetch+install it. */
export interface AvailableUpdate {
  version: string;
  notes: string | null;
  /** Download + install; reports cumulative progress (0..1, or null while total size is unknown). */
  downloadAndInstall(onProgress: (fraction: number | null) => void): Promise<void>;
}

/** null → already current. Rejects on any check failure (offline, no endpoint, bad manifest). */
export async function checkForUpdate(): Promise<AvailableUpdate | null> {
  const found = await shellInvoke<{ version: string; notes: string | null } | null>("update_check");
  if (!found) return null;
  return {
    version: found.version,
    notes: found.notes,
    downloadAndInstall: async (onProgress) => {
      const stop = await shellListen("update-progress", (p) => onProgress(p as number | null));
      try {
        await shellInvoke("update_install");
        onProgress(1);
      } finally {
        stop();
      }
    },
  };
}

/** Post-install restart. On Windows the NSIS installer already exits the app during install, so
 * this is effectively the macOS/Linux path; harmless if unreachable. */
export const restartApp = (): Promise<void> => shellInvoke("update_restart");

/** Signature-verification failures must read differently from "couldn't check" — a bad signature
 * on a downloaded artifact is a refused install, not a connectivity hiccup. The plugin's minisign
 * errors are string-matched here because it exposes no structured error kind over IPC. */
export function isSignatureError(err: unknown): boolean {
  return /signature|minisign/i.test(String(err));
}
