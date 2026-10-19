// A per-lane shared selection — how a trip/visit picker widget steers the other widgets on its tab
// (dashboard widgets otherwise never talk to each other). Keyed `lane:scope`; remembered per viewer
// in localStorage (best effort: a private window or blocked storage just starts at the default).

import { useSyncExternalStore } from "react";

const values = new Map<string, string>();
const listeners = new Set<() => void>();
const STORE_PREFIX = "watchman.selection.";

function read(key: string): string | null {
  if (values.has(key)) return values.get(key) ?? null;
  try {
    const v = window.localStorage.getItem(STORE_PREFIX + key);
    if (v) values.set(key, v);
    return v;
  } catch {
    return null;
  }
}

export function setSelection(lane: string, scope: string, value: string): void {
  const key = `${lane}:${scope}`;
  if (values.get(key) === value) return;
  values.set(key, value);
  try {
    window.localStorage.setItem(STORE_PREFIX + key, value);
  } catch {
    /* per-viewer convenience only */
  }
  listeners.forEach((l) => l());
}

export function getSelection(lane: string, scope: string): string | null {
  return read(`${lane}:${scope}`);
}

export function useSelection(lane: string, scope: string | null): string | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => (scope ? read(`${lane}:${scope}`) : null),
  );
}

/** Test helper: forget every selection. */
export function resetSelections(): void {
  values.clear();
  listeners.forEach((l) => l());
}

/** The picker scopes a widget can follow (`symbols_from`): a trip/visit picker's pick, or a company
 * (Career ▸ Openings follows `company`; the Pool's openings chip sets it). */
export const PICKER_SCOPES = new Set(["visit", "trip", "company"]);
