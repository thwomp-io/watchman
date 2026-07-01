// The app-wide navigation primitive: a browser-style history model so any surface can
// deep-link into any zone, and the masthead ◀▶ steps back/forward across zone transitions — restoring
// each zone's sub-location (the scatter you were on, the doc you were reading).
//
// `Ref` is the UNIVERSAL deep-link descriptor — the same shape a viz point, a bus-event payload, a
// dashboard row, or a doc link can carry. `zone` is required; the optional fields target a sub-location
// WITHIN that zone, resolved by the zone itself:
//   doc — an exact vault-relative doc path (VAULT opens it)
//   dir — a vault-relative dir; resolved to its NEWEST .md (research dirs accrete dated reports)
//   viz — a VizEntry.path (VIZ selects it)
// Producers store `dir` (stable); consumers resolve it to `doc` at click-time via `resolveRef`, so the
// history stack always holds a concrete `doc`.

import { createContext, useContext } from "react";
import { listVaultDir } from "./api";

export type Zone = "inbox" | "dash" | "surfaces" | "viz" | "vault";

export interface Ref {
  zone: Zone;
  doc?: string;
  dir?: string;
  viz?: string;
  widget?: string; // a dashboard widget id — DASH renders just that widget full-bleed (expand-in-place)
}

export interface Nav {
  current: Ref;
  navigate: (ref: Ref) => void;
  back: () => void;
  forward: () => void;
  canGoBack: boolean;
  canGoForward: boolean;
  // a zone keeps `current` in sync with its own sub-selection, so a later navigate() (e.g. a tab
  // click) captures the exact spot for back/forward. Updates `current`; never touches the stacks.
  report: (partial: Partial<Ref>) => void;
}

// No-op default: components rendered OUTSIDE a provider (e.g. the renderToString viz review harness,
// or a unit test) must not crash. The interactive app always wraps zones in <NavContext.Provider>.
const NOOP: Nav = {
  current: { zone: "inbox" },
  navigate: () => {},
  back: () => {},
  forward: () => {},
  canGoBack: false,
  canGoForward: false,
  report: () => {},
};

export const NavContext = createContext<Nav | null>(null);

export function useNav(): Nav {
  return useContext(NavContext) ?? NOOP;
}

// Resolve a `dir` ref to a concrete `doc` (the newest report in that dir) before it enters history.
// list_vault_dir already returns a dir's .md files newest-first, so [0] is the latest. A doc-or-viz
// ref passes through unchanged; an empty/unresolvable dir is returned as-is (the zone shows its default).
export async function resolveRef(ref: Ref): Promise<Ref> {
  if (ref.dir && !ref.doc) {
    try {
      const docs = await listVaultDir(ref.dir);
      if (docs.length > 0) return { zone: ref.zone, doc: docs[0].path };
    } catch {
      /* fall through — navigate to the zone without a specific doc */
    }
  }
  return ref;
}

// A bus event's payload may carry a deep-link under `ref`. Validate minimally (a zone is required).
export function payloadRef(payload: unknown): Ref | null {
  if (payload && typeof payload === "object" && "ref" in payload) {
    const r = (payload as { ref: unknown }).ref;
    if (r && typeof r === "object" && "zone" in r && typeof (r as { zone: unknown }).zone === "string") {
      return r as Ref;
    }
  }
  return null;
}
