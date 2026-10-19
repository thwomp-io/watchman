// Shared hover-card helpers for the tile widgets (a tooltip portal anchored beside a tile).

export interface TipPos {
  left: number;
  top: number;
}

// Beside the tile like an inventory hover: right side by default, flipped left near the viewport
// edge, clamped vertically. Fixed positioning keeps it clear of any ancestor overflow.
export function tipPosition(rect: DOMRect, width = 320, height = 380): TipPos {
  const pad = 10;
  let left = rect.right + pad;
  if (left + width > window.innerWidth - pad) left = Math.max(pad, rect.left - width - pad);
  const top = Math.min(rect.top, Math.max(pad, window.innerHeight - height));
  return { left, top };
}

// Touch has no hover. Queried per call so a desktop touchscreen flip mid-session behaves.
export function coarsePointer(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)")?.matches === true;
}
