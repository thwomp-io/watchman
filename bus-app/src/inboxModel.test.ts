// The Inbox model: story rows (same story under several tickers = one row, every chip; filtered
// rows never merge), publish-time parsing + ordering, the "SYM — " prefix, the outlet fallback.

import { describe, expect, it } from "vitest";

import { ageLabel, bandFor, headline, outlet, publishedMs, storyRows } from "./inboxModel";
import type { BusEvent } from "./types";

let next = 1;
function ev(subject: string, title: string, payload: Record<string, unknown>, over: Partial<BusEvent> = {}): BusEvent {
  return {
    id: next++, created_at: "2026-10-05T16:33:27+00:00", producer: "finance.pulse", lane: "finance",
    kind: "catalyst", subject, title: `${subject} — ${title}`, body: "", payload_json: JSON.stringify(payload),
    severity: "info", read_at: null, delivered_via: [], ...over,
  };
}

describe("inboxModel", () => {
  it("parses the feed's UTC stamp and ages it", () => {
    const ms = publishedMs("2026-10-05 13:30");
    expect(new Date(ms).toISOString()).toBe("2026-10-05T13:30:00.000Z");
    expect(ageLabel(ms, ms + 3 * 3_600_000)).toBe("3h");
    expect(ageLabel(NaN)).toBe("—");
  });

  it("strips the producer prefix and falls back to the link's host for the outlet", () => {
    const e = ev("ACME", "Acme wins", { url: "https://www.example.com/a" });
    expect(headline(e)).toBe("Acme wins");
    expect(outlet({ url: "https://www.example.com/a" })).toBe("example.com");
    expect(outlet({ url: "https://news.google.com/rss/articles/x" })).toBe("");
    expect(outlet({ url: "x", publisher: "Sample Daily" })).toBe("Sample Daily");
  });

  it("collapses a story across tickers, keeps filtered rows apart, newest publish first", () => {
    const a = ev("AAAA", "Chipmaker hits a high", { url: "u1", story: "chipmaker hits a high", published: "2026-10-05 12:00" });
    const b = ev("BBBB", "Chipmaker hits a high", { url: "u2", story: "chipmaker hits a high", published: "2026-10-05 12:00" },
                 { read_at: "2026-10-05T17:00:00Z" });
    const c = ev("ACME", "Acme wins", { url: "u3", story: "acme wins", published: "2026-10-05 14:00" });
    const j1 = ev("ACME", "ACME Oct 2026 300.000 call", { url: "u4", story: "acme oct", filtered: "option-contract" });
    const j2 = ev("ACME", "ACME Oct 2026 300.000 call", { url: "u5", story: "acme oct", filtered: "option-contract" });
    const legacy = ev("ACME", "Old item without a story key", { url: "u6" });
    const rows = storyRows([a, b, c, j1, j2, legacy]);
    expect(rows.find((r) => r.title === "Chipmaker hits a high")?.symbols).toEqual(["AAAA", "BBBB"]);
    expect(rows.find((r) => r.title === "Chipmaker hits a high")?.unread).toBe(true); // one unread entry is enough
    expect(rows.filter((r) => r.filtered)).toHaveLength(2);
    const dated = rows.filter((r) => r.title === "Acme wins" || r.title === "Chipmaker hits a high");
    expect(dated.map((r) => r.title)).toEqual(["Acme wins", "Chipmaker hits a high"]); // 14:00 before 12:00
    expect(rows.some((r) => r.title === "Old item without a story key")).toBe(true);
  });

  it("bands: catalysts and info are wire, alerts act, warns watch, filings their own", () => {
    expect(bandFor(ev("A", "x", {}))).toBe("wire");
    expect(bandFor(ev("A", "x", {}, { kind: "trap_proximity", severity: "alert" }))).toBe("act");
    expect(bandFor(ev("A", "x", {}, { kind: "day_move", severity: "warn" }))).toBe("watch");
    expect(bandFor(ev("A", "x", {}, { kind: "filing_drop", severity: "alert" }))).toBe("filings");
  });
});
