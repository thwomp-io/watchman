// The self-update affordance — a baseplate pill beside the build stamp (the version is already
// engraved there; this is the verb). Deliberately QUIET: every failure renders as a terse inline
// note in the footer's own register — an offline check must read like weather, never a modal.
// Gating lives in App.tsx ({PUBLISHED && isTauri()}): published builds are the only update channel
// (the dev daily-driver's config points nowhere, and debug builds don't register the plugin at all
// — see lib.rs), and the served browser console updates by redeploying the server, not itself.

import { useCallback, useState } from "react";
import { type AvailableUpdate, checkForUpdate, isSignatureError, restartApp } from "./update";

type UpdateState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "current" }
  | { phase: "available"; update: AvailableUpdate }
  | { phase: "downloading"; fraction: number | null }
  | { phase: "ready" }
  // check-failed = quiet retryable note (offline, unreachable endpoint, unregistered plugin);
  // sig-failed = the download was REFUSED (minisign verify) — worth distinct, sterner wording.
  | { phase: "check-failed" }
  | { phase: "sig-failed" };

export default function UpdatePill() {
  const [state, setState] = useState<UpdateState>({ phase: "idle" });

  const check = useCallback(async () => {
    setState({ phase: "checking" });
    try {
      const update = await checkForUpdate();
      setState(update ? { phase: "available", update } : { phase: "current" });
    } catch {
      setState({ phase: "check-failed" });
    }
  }, []);

  const download = useCallback(async (update: AvailableUpdate) => {
    setState({ phase: "downloading", fraction: null });
    try {
      await update.downloadAndInstall((fraction) => setState({ phase: "downloading", fraction }));
      // On Windows the NSIS installer exits the app during install, so this line may never paint
      // there — macOS/Linux land here and wait for the explicit restart.
      setState({ phase: "ready" });
    } catch (e) {
      setState(isSignatureError(e) ? { phase: "sig-failed" } : { phase: "check-failed" });
    }
  }, []);

  switch (state.phase) {
    case "idle":
      return (
        <span className="updater">
          <button className="updater-btn" onClick={() => void check()}>CHECK FOR UPDATES</button>
        </span>
      );
    case "checking":
      return <span className="updater"><span className="updater-note">CHECKING…</span></span>;
    case "current":
      // sticky, re-checkable — "up to date" stays glanceable but remains the verb
      return (
        <span className="updater">
          <button className="updater-btn" onClick={() => void check()}>UP TO DATE ✓</button>
        </span>
      );
    case "available":
      return (
        <span className="updater">
          <span className="updater-note updater-avail" title={state.update.notes ?? undefined}>
            v{state.update.version} AVAILABLE
          </span>
          <button className="updater-btn updater-go" onClick={() => void download(state.update)}>
            DOWNLOAD
          </button>
        </span>
      );
    case "downloading":
      return (
        <span className="updater">
          <span className="updater-note">
            {state.fraction === null ? "DOWNLOADING…" : `DOWNLOADING ${Math.round(state.fraction * 100)}%`}
          </span>
        </span>
      );
    case "ready":
      return (
        <span className="updater">
          <button className="updater-btn updater-go" onClick={() => void restartApp()}>
            RESTART TO UPDATE
          </button>
        </span>
      );
    case "check-failed":
      return (
        <span className="updater">
          <span className="updater-note updater-dim">UPDATE CHECK FAILED — OFFLINE?</span>
          <button className="updater-btn" onClick={() => void check()}>RETRY</button>
        </span>
      );
    case "sig-failed":
      return (
        <span className="updater">
          <span className="updater-note updater-bad">SIGNATURE REJECTED — UPDATE NOT INSTALLED</span>
          <button className="updater-btn" onClick={() => void check()}>RETRY</button>
        </span>
      );
  }
}
