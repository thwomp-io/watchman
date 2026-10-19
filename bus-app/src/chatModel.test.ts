import { describe, expect, it } from "vitest";
import { derivePill, fmtTokens, groupEntries, resetIn, shortModel } from "./chatModel";
import type { PocketEntry, PocketStatus } from "./generated/contracts";

const e = (seq: number, role: string, text: string, extra: Partial<PocketEntry> = {}): PocketEntry => ({
  seq, ts: "2030-01-01T10:00:00+00:00", role, text, tool: null, id: null, ok: null, truncated: null, ...extra,
});

const status = (over: Partial<PocketStatus> = {}): PocketStatus => ({
  state: "idle", actor: "lane", mode: "live", model: "claude-test-1", effort: null, context_used: 1000, context_max: 10000,
  limits: { five_hour: { utilization: 0.2, resets_at: 2_000_000_000 } }, limit_status: "allowed",
  cost_usd: 0.5, turn_cost_usd: 0.1, duration_ms: 3000, turns: 1, session_id: "s", started_at: null,
  turn_started_at: null, cwd: "/corpus", branch: "main", claude_version: "9", harness_version: "1",
  mcp: {}, seal: { ok: true, checked_at: "2030-01-01", failures: [] }, updated_at: null, ...over,
});

describe("groupEntries", () => {
  it("folds tool calls, results and refusals between messages into one steps row", () => {
    const items = groupEntries([
      e(1, "user", "price?"),
      e(2, "tool", "quote(ACME)", { tool: "finance_quote", id: "a" }),
      e(3, "tool", "touch x", { tool: "Bash", id: "b" }),
      e(4, "result", "$12", { id: "a", ok: true }),
      e(5, "assistant", "ACME is $12."),
      e(6, "denied", "touch x", { tool: "Bash" }),
    ]);
    // the end-of-turn refusal folds back onto its call — no extra row after the reply
    expect(items.map((i) => i.kind)).toEqual(["user", "steps", "assistant"]);
    const steps = items[1].kind === "steps" ? items[1].steps : [];
    expect(steps).toHaveLength(2);
    expect(steps[0]).toMatchObject({ tool: "finance_quote", ok: true, result: "$12" });
    expect(steps[1]).toMatchObject({ tool: "Bash", denied: true, ok: false });
  });

  it("keeps an unmatched refusal as its own step, and never crosses into the previous turn", () => {
    const items = groupEntries([
      e(1, "user", "a"), e(2, "tool", "ls", { tool: "Bash", id: "t" }), e(3, "assistant", "ok"),
      e(4, "user", "b"), e(5, "assistant", "done"), e(6, "denied", "ls", { tool: "Bash" }),
    ]);
    expect(items.map((i) => i.kind)).toEqual(["user", "steps", "assistant", "user", "assistant", "steps"]);
    const first = items[1].kind === "steps" ? items[1].steps : [];
    expect(first[0].denied).toBe(false);
  });

  it("marks the matching call refused when the refusal arrives in the same run", () => {
    const items = groupEntries([e(1, "tool", "rm x", { tool: "Bash", id: "z" }), e(2, "denied", "rm x", { tool: "Bash" })]);
    const steps = items[0].kind === "steps" ? items[0].steps : [];
    expect(steps).toHaveLength(1);
    expect(steps[0].denied).toBe(true);
  });
});

describe("derivePill", () => {
  it("reads ok, warn past 80%, alert when limited or unsealed, offline without a runner", () => {
    expect(derivePill(status(), true)).toMatchObject({ tone: "ok", ctxPct: 10, fivePct: 20, model: "test-1" });
    expect(derivePill(status({ context_used: 8500 }), true).tone).toBe("warn");
    expect(derivePill(status({ limits: { five_hour: { utilization: 0.81, resets_at: 1 } } }), true).tone).toBe("warn");
    expect(derivePill(status({ limit_status: "rejected" }), true)).toMatchObject({ tone: "alert", word: "limited" });
    expect(derivePill(status({ seal: { ok: false, checked_at: "x", failures: ["f"] } }), true).word).toBe("seal failed");
    expect(derivePill(status(), false)).toMatchObject({ tone: "offline", word: "offline" });
  });
});

describe("formatters", () => {
  it("counts down to a reset and abbreviates tokens", () => {
    const now = 1_000_000_000_000;
    expect(resetIn(now / 1000 + 2 * 3600 + 14 * 60, now)).toBe("2h 14m");
    expect(resetIn(now / 1000 + 3 * 86400 + 3600, now)).toBe("3d 1h");
    expect(resetIn(now / 1000 - 5, now)).toBe("now");
    expect(resetIn(null, now)).toBe("—");
    expect(fmtTokens(29677)).toBe("30k");
    expect(fmtTokens(1_000_000)).toBe("1M");
    expect(shortModel("claude-opus-5-5[1m]")).toBe("opus-5-5");
  });
});
