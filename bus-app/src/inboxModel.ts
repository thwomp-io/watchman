// The Inbox's pure model: triage bands, the catalyst payload's article fields, publish-time ages, and
// the WIRE band's story rows (one row per story — the same headline under several tickers collapses
// to one row with every ticker chip; quote/option pages the engine flagged are held back behind the
// band's "n filtered" footer). No React, no I/O — unit-tested on fixtures.

import type { BusEvent } from "./types";

export type Band = "act" | "watch" | "wire" | "filings";
const FILING_KINDS = new Set(["filing_drop", "print_landed", "filing"]);

// ACT/WATCH are the "clear these" inbox; CATALYST WIRE is a skim-stream of single-name catalysts;
// FILINGS is the primary-source rail. Empty bands hide.
export function bandFor(e: BusEvent): Band {
  if (FILING_KINDS.has(e.kind)) return "filings";
  if (e.kind === "catalyst" || e.severity === "info") return "wire";
  return e.severity === "alert" ? "act" : "watch";
}

export const BANDS: { key: Band; label: string; accent: string }[] = [
  { key: "act", label: "ACT", accent: "sev-alert" },
  { key: "watch", label: "WATCH", accent: "sev-warn" },
  { key: "wire", label: "CATALYST WIRE", accent: "sev-info" },
  { key: "filings", label: "FILINGS", accent: "sev-file" },
];

// Urgency = the "needs you" tiers (alert+warn). Info catalysts never drive the badge or the orb.
export const isUrgent = (e: BusEvent): boolean => e.severity !== "info";

/** The catalyst payload's article fields (each optional — older events carry only url/source). */
export interface Article {
  url: string;
  resolved_url?: string;
  source?: string;
  published?: string;
  publisher?: string;
  publisher_url?: string;
  image_url?: string;
  author?: string;
  tags?: string[];
  summary?: string;
  story?: string;
  filtered?: string;
}

export function payloadOf(e: BusEvent): Record<string, unknown> {
  try {
    const p = JSON.parse(e.payload_json || "{}");
    return p && typeof p === "object" ? (p as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function articleOf(e: BusEvent): Article {
  return payloadOf(e) as unknown as Article;
}

/** A feed's publish stamp ("YYYY-MM-DD HH:MM", UTC) → epoch ms; NaN when it isn't one. */
export function publishedMs(stamp: string | undefined): number {
  if (!stamp) return NaN;
  const m = stamp.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? Date.parse(`${m[1]}T${m[2]}:00Z`) : Date.parse(stamp);
}

/** "now" · "12m" · "3h" · "2d" from epoch ms. */
export function ageLabel(ms: number, now: number = Date.now()): string {
  if (Number.isNaN(ms)) return "—";
  const mins = Math.round((now - ms) / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / (60 * 24))}d`;
}

/** The headline without the producer's "SYM — " prefix. */
export function headline(e: BusEvent): string {
  const p = `${e.subject} — `;
  return e.subject && e.title.startsWith(p) ? e.title.slice(p.length) : e.title;
}

/** The outlet to show: the publisher the feed named, else the article link's host. */
export function outlet(a: Article): string {
  if (a.publisher) return a.publisher;
  const link = a.resolved_url || a.url;
  try {
    const host = new URL(link).host.replace(/^www\./, "");
    return host === "news.google.com" ? "" : host;
  } catch {
    return "";
  }
}

export interface StoryRow {
  key: string;
  events: BusEvent[]; // newest first; events[0] is the one the reader shows
  symbols: string[];
  title: string;
  outlet: string;
  publishedMs: number;
  unread: boolean;
  filtered: string; // the junk rule ("" = news)
}

function fallbackStory(e: BusEvent): string {
  return headline(e).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** WIRE band events → story rows, newest publish time first. Rows sharing a story key collapse. */
export function storyRows(events: BusEvent[]): StoryRow[] {
  const byKey = new Map<string, StoryRow>();
  for (const e of events) {
    const a = articleOf(e);
    const key = (a.filtered ? `filtered:${e.id}` : a.story || fallbackStory(e)) || `id:${e.id}`;
    const ms = publishedMs(a.published);
    const row = byKey.get(key);
    if (!row) {
      byKey.set(key, {
        key, events: [e], symbols: e.subject ? [e.subject] : [], title: headline(e), outlet: outlet(a),
        publishedMs: Number.isNaN(ms) ? Date.parse(e.created_at) : ms, unread: !e.read_at,
        filtered: a.filtered ?? "",
      });
      continue;
    }
    row.events.push(e);
    if (e.subject && !row.symbols.includes(e.subject)) row.symbols.push(e.subject);
    row.unread = row.unread || !e.read_at;
    if (!row.outlet) row.outlet = outlet(a);
  }
  return [...byKey.values()].sort((x, y) => (y.publishedMs || 0) - (x.publishedMs || 0));
}
