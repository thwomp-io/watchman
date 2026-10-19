// The Chat tab's pure logic — grouping the transcript into bubbles and deriving the status pill.
// No React, no I/O, so vitest pins it directly. The server shapes come from the generated contracts
// (PocketSnapshot), never hand-written twins.

import type { PocketEntry, PocketStatus } from "./generated/contracts";

export interface Step { tool: string; text: string; ok: boolean | null; result: string; denied: boolean }

export type ChatItem =
  | { kind: "user"; seq: number; ts: string; text: string }
  | { kind: "assistant"; seq: number; ts: string; text: string }
  | { kind: "steps"; seq: number; steps: Step[] }
  | { kind: "error"; seq: number; ts: string; text: string };

/** Tool calls, their results and any refusals between two messages collapse into ONE "working…
    (N steps)" row — the phone never shows a terminal dump. A result pairs with its call by id. A
    refusal is reported at the END of a turn (the CLI's final result lists them), so it is folded back
    onto the matching call anywhere in the same turn instead of opening a new row after the reply. */
export function groupEntries(entries: PocketEntry[]): ChatItem[] {
  const out: ChatItem[] = [];
  let steps: Step[] | null = null;
  let stepsSeq = 0;
  const byId = new Map<string, Step>();
  let turnSteps: Step[] = []; // every step since the last user message
  const flush = () => {
    if (steps && steps.length) out.push({ kind: "steps", seq: stepsSeq, steps });
    steps = null;
  };
  for (const e of entries) {
    if (e.role === "denied") {
      const s = turnSteps.find((x) => x.tool === e.tool && x.text === e.text && !x.denied);
      if (s) { s.denied = true; s.ok = false; continue; }
    }
    if (e.role === "tool" || e.role === "result" || e.role === "denied") {
      if (!steps) { steps = []; stepsSeq = e.seq; }
      if (e.role === "tool") {
        const s: Step = { tool: e.tool ?? "tool", text: e.text, ok: null, result: "", denied: false };
        steps.push(s);
        turnSteps.push(s);
        if (e.id) byId.set(e.id, s);
      } else if (e.role === "result") {
        const s = (e.id && byId.get(e.id)) || steps[steps.length - 1];
        if (s) { s.ok = e.ok ?? null; s.result = e.text; }
      } else {
        // a refusal: mark the matching call (same tool + text) or add it as its own step
        const s = steps.find((x) => x.tool === e.tool && x.text === e.text && !x.denied);
        if (s) { s.denied = true; s.ok = false; }
        else steps.push({ tool: e.tool ?? "tool", text: e.text, ok: false, result: "", denied: true });
      }
      continue;
    }
    flush();
    if (e.role === "user") turnSteps = [];
    if (e.role === "user" || e.role === "assistant" || e.role === "error") {
      out.push({ kind: e.role, seq: e.seq, ts: e.ts, text: e.text });
    }
  }
  flush();
  return out;
}

export type Tone = "ok" | "warn" | "alert" | "offline";

export interface Pill {
  tone: Tone;
  model: string;      // short model label ("opus-5-5")
  ctxPct: number | null;
  fivePct: number | null;
  sevenPct: number | null;
  word: string;       // the state word on the pill ("idle", "working", "offline", "limited")
}

const pct = (v: number | null | undefined): number | null =>
  v == null || Number.isNaN(v) ? null : Math.round(v * 100);

export function shortModel(model: string | null | undefined): string {
  if (!model) return "—";
  return model.replace(/^claude-/, "").replace(/\[.*\]$/, "");
}

/** amber past ~80% of context or of a plan window; red when the plan says rejected, the seal failed,
    or the runner is gone. The thresholds are the status line's "am I near a wall?" intent. */
export function derivePill(status: PocketStatus, online: boolean): Pill {
  const ctxPct = status.context_used != null && status.context_max
    ? Math.round((status.context_used / status.context_max) * 100) : null;
  const fivePct = pct(status.limits?.five_hour?.utilization);
  const sevenPct = pct(status.limits?.seven_day?.utilization);
  const model = shortModel(status.model);
  if (!online) return { tone: "offline", model, ctxPct, fivePct, sevenPct, word: "offline" };
  if (status.seal && status.seal.checked_at && !status.seal.ok) {
    return { tone: "alert", model, ctxPct, fivePct, sevenPct, word: "seal failed" };
  }
  if (status.limit_status === "rejected") {
    return { tone: "alert", model, ctxPct, fivePct, sevenPct, word: "limited" };
  }
  const hot = [ctxPct, fivePct, sevenPct].some((v) => v != null && v >= 80)
    || status.limit_status === "allowed_warning";
  return { tone: hot ? "warn" : "ok", model, ctxPct, fivePct, sevenPct, word: status.state ?? "idle" };
}

/** "2h 14m" until a unix-seconds reset; "now" once passed; "—" when unknown. */
export function resetIn(resetsAt: number | null | undefined, nowMs: number): string {
  if (!resetsAt) return "—";
  const s = Math.round(resetsAt - nowMs / 1000);
  if (s <= 0) return "now";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

export function fmtDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}
