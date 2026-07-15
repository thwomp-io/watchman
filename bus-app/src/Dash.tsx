// Domain dashboards — DD/APM-style observability pages over harness live data.
// A dashboard = config'd grid of widgets (source × kind × layout). Refresh discipline (operator-
// ratified): NOWHERE NEAR DD-realtime — market10m widgets auto-refresh every 10 min ONLY
// inside US market hours (provider politeness); local60s for bus/file sources; REFRESH ALL
// always available; every widget carries AS-OF + took chrome.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { GridLayout, noCompactor, useContainerWidth } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { listDashboards, listEvents, listVaultDir, onVaultChanged, readDoc, resetDashboard, runWidget, saveDashboard } from "./api";
import ErrorBoundary from "./ErrorBoundary";
import JsonView from "./JsonView";
import type { BusEvent, Dashboard, DirDoc, NewsItem, SurfaceState, Widget, WireDigest } from "./types";
import { fmtNum } from "./viz/common";
import BarChart from "./viz/BarChart";
import Donuts from "./viz/Donut";
import LineChart from "./viz/LineChart";
import Matrix from "./viz/Matrix";
import Radar from "./viz/Radar";
import Sankey from "./viz/Sankey";
import Schedule from "./viz/Schedule";
import FoodBank from "./viz/FoodBank";
import Treemap from "./viz/Treemap";
import VestTimeline from "./viz/VestTimeline";
import Ladder from "./viz/Ladder";
import BeadTree from "./viz/BeadTree";
import { useNav } from "./nav";
import { isTauri } from "./transport";
import { preprocessLinks, VaultImage } from "./VaultZone";

const MARKET_MS = 10 * 60_000;
const LOCAL_MS = 60_000;
const LOCAL30M_MS = 30 * 60_000; // gentle cadence for non-tick sources (the News wire)

// ————— cache + dedupe (the maintainer's first-load lockup feedback) ————————————————————————————————————
// localStorage cache: DASH paints instantly with last-known data (CACHED chip + honest AS-OF),
// then refreshes per policy in the background. In-flight dedupe: widgets sharing a source
// (watch ×2, pulse ×2) share ONE subprocess run.

interface CacheEntry { data: unknown; at: string }

const cacheKey = (lane: string, id: string) => `dash:${lane}:${id}`;

function loadCache(lane: string, id: string): CacheEntry | null {
  try {
    const raw = localStorage.getItem(cacheKey(lane, id));
    return raw ? (JSON.parse(raw) as CacheEntry) : null;
  } catch {
    return null;
  }
}

function saveCache(lane: string, id: string, data: unknown) {
  try {
    localStorage.setItem(cacheKey(lane, id), JSON.stringify({ data, at: new Date().toISOString() }));
  } catch { /* quota — cache is best-effort */ }
}

const inflight = new Map<string, Promise<unknown>>();

function dedupedFetch(sig: string, fetch: () => Promise<unknown>): Promise<unknown> {
  const existing = inflight.get(sig);
  if (existing) return existing;
  const p = fetch().finally(() => inflight.delete(sig));
  inflight.set(sig, p);
  return p;
}

function staleAfter(refresh: string): number {
  return refresh === "market10m" ? MARKET_MS
    : refresh === "local60s" ? LOCAL_MS
    : refresh === "local30m" ? LOCAL30M_MS
    : Infinity;
}

function inMarketWindow(now: Date): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "numeric",
    hour12: false,
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  if (["Sat", "Sun"].includes(get("weekday"))) return false;
  const m = Number(get("hour")) * 60 + Number(get("minute"));
  return m >= 9 * 60 + 30 && m < 16 * 60;
}

function pluck(data: unknown, path?: string | null): unknown {
  if (!path) return data;
  return path.split(".").reduce<unknown>(
    (acc, k) => (acc != null && typeof acc === "object" ? (acc as Record<string, unknown>)[k] : undefined),
    data,
  );
}

function sniffViz(v: Record<string, unknown>): string {
  if (v.windows && v.vests) return "vest-timeline";  // the vest-timeline sell-planning calendar
  // trap-map ladders: top-level symbols[] whose entries carry rungs (disjoint from every other shape)
  if (Array.isArray(v.symbols) && (v.symbols[0] as { rungs?: unknown } | undefined)?.rungs) return "ladder";
  // bead family tree: `beads` + `edges` (deliberately NOT nodes/links, so sankey can't claim it)
  if (Array.isArray(v.beads) && Array.isArray(v.edges)) return "bead-tree";
  if (v.nodes && v.links) return "sankey";
  if (Array.isArray(v.nodes)) return "treemap";
  if (v.pies) return "pies";
  if (v.restaurants) return "food-bank";
  if (v.dayStart && (v.items || v.availability)) return "schedule";
  if (v.axes && v.candidates) return "compare";
  if (v.axes && v.rows) return "matrix";
  // rank-bar: `rows` whose entries carry `parts` (no `axes` — disjoint from matrix/compare).
  if (Array.isArray(v.rows) && (v.rows[0] as { parts?: unknown } | undefined)?.parts) return "rank-bar";
  if (v.rings && v.points) return "unknown"; // radial — static-only
  if (v.points || v.series) return "line";
  return "unknown";
}

const VIZ_COMP: Record<string, React.ComponentType<{ data: never }>> = {
  treemap: Treemap, sankey: Sankey, pies: Donuts, line: LineChart, matrix: Matrix,
  compare: Radar, schedule: Schedule, "food-bank": FoodBank, "vest-timeline": VestTimeline,
  "rank-bar": BarChart, ladder: Ladder, "bead-tree": BeadTree,
};

function StatBody({ data, widget }: { data: unknown; widget: Widget }) {
  const v = pluck(data, widget.value_path);
  const num = typeof v === "number" ? v : Number(v);
  const text = Number.isFinite(num) ? fmtNum(num) : String(v ?? "—");
  // Sign-formatting (the +/− day-change convention) defaults ON for "%" tiles, opt-out via
  // `signed: false` — magnitude reads (e.g. unwind %-complete) aren't deltas; "+" there is noise.
  const signed = widget.signed !== false && widget.suffix === "%";
  const sign = Number.isFinite(num) && signed ? (num > 0 ? "pos" : num < 0 ? "neg" : "") : "";
  return (
    <div className={`stat-big ${sign}`}>
      {Number.isFinite(num) && num > 0 && signed ? "+" : ""}
      {widget.prefix ?? ""}{text}{widget.suffix ?? ""}
    </div>
  );
}

function FeedBody({ events }: { events: BusEvent[] }) {
  if (events.length === 0) return <p className="empty">NO SIGNALS</p>;
  return (
    <ul className="widget-feed">
      {events.map((e) => (
        <li key={e.id} className={`${e.read_at ? "read" : "unread"} sev-${e.severity}`}>
          <span className="led" />
          <span className="row-title">{e.title}</span>
          <span className="row-when">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        </li>
      ))}
    </ul>
  );
}

// RSS summaries are external (untrusted) HTML → render as SANITIZED PLAIN TEXT: strip every tag (never
// inserted into the live DOM) + decode entities via a detached textarea (its content is not parsed as
// elements, so it's XSS-safe + needs no sanitizer dependency). Block-closers become newlines so multi-
// paragraph bodies (feeds that ship full article bodies) stay readable; `.news-detail-body`
// renders them with `white-space: pre-wrap`. The full formatted (often paywalled) article is one
// "open original ↗" click away.
function htmlToText(html: string): string {
  if (!html) return "";
  let t = html
    .replace(/<\/(p|div|li|h[1-6]|blockquote)\s*>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "");
  const ta = document.createElement("textarea");
  ta.innerHTML = t; // entity-decode only; no element parsing → safe
  t = ta.value;
  return t.replace(/\n{3,}/g, "\n\n").replace(/[ \t]+\n/g, "\n").trim();
}

// "YYYY-MM-DD HH:MM" (UTC-ish per the wire model) → a relative stamp. The published string carries no
// tz, so parse it as UTC (append Z) — else a user far west of UTC reads every item hours stale. Raw fallback if unparseable.
function relTime(published: string): string {
  if (!published) return "—";
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(published) ? `${published.replace(" ", "T")}:00Z` : published;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return published;
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// The Finance ▸ News master-detail reader (+ v2): filter chips (All / My book /
// category) + keyword search over a scan list (relevance-badged) → the selected item's summary. j/k+arrows
// nav the list. Pure display off the wire JSON (no model in the loop); the determinism contract DASH holds.
function NewsReader({ data }: { data: WireDigest }) {
  const items = useMemo<NewsItem[]>(() => data?.items ?? [], [data]);
  const [chip, setChip] = useState("all");
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      const chipOk = chip === "all" ? true
        : chip === "book" ? (it.holdings_hit?.length ?? 0) > 0
          : it.category === chip;
      return chipOk && (!q || it.title.toLowerCase().includes(q));
    });
  }, [items, chip, query]);

  const chips = useMemo(() => {
    const bookN = items.filter((it) => (it.holdings_hit?.length ?? 0) > 0).length;
    const out = [{ id: "all", label: "All", n: items.length }];
    if (bookN > 0) out.push({ id: "book", label: "My book", n: bookN });
    for (const c of ["markets", "geopolitics", "thesis"]) {
      const n = items.filter((it) => it.category === c).length;
      if (n > 0) out.push({ id: c, label: c[0].toUpperCase() + c.slice(1), n });
    }
    return out;
  }, [items]);

  useEffect(() => { setSel(0); }, [chip, query]); // reset selection when the filter changes

  useEffect(() => { // keyboard nav — j/k + arrows; ignore while typing in the search box
    const onKey = (e: KeyboardEvent) => {
      if ((document.activeElement as HTMLElement | null)?.tagName === "INPUT") return;
      if (e.key === "j" || e.key === "ArrowDown") { setSel((s) => Math.min(s + 1, filtered.length - 1)); e.preventDefault(); }
      else if (e.key === "k" || e.key === "ArrowUp") { setSel((s) => Math.max(s - 1, 0)); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filtered.length]);

  useEffect(() => { listRef.current?.querySelector("li.sel")?.scrollIntoView({ block: "nearest" }); }, [sel, filtered]);

  if (items.length === 0) return <p className="empty">NO HEADLINES</p>;
  const cur: NewsItem | undefined = filtered[Math.min(sel, filtered.length - 1)];
  const body = cur ? htmlToText(cur.summary || "") : "";
  const failed = (data?.notes ?? []).length;

  return (
    <div className="news-reader-v2">
      <div className="news-bar">
        <div className="news-chips">
          {chips.map((c) => (
            <button key={c.id} className={`news-chip${chip === c.id ? " on" : ""}${c.id === "book" ? " book" : ""}`}
              onClick={() => setChip(c.id)}>
              {c.id === "book" && <span className="dot">●</span>}{c.label}<span className="chip-n">{c.n}</span>
            </button>
          ))}
        </div>
        <input className="news-search" placeholder="search headlines…" value={query}
          onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="news-reader">
        <ul className="news-list" ref={listRef}>
          {filtered.map((it, i) => (
            <li key={`${it.url}-${i}`} className={i === sel ? "sel" : ""} onClick={() => setSel(i)}>
              <span className="news-row-top">
                <span className="news-src">{it.source}</span>
                <span className="news-when">{relTime(it.published)}</span>
              </span>
              <span className="news-title">
                {(it.holdings_hit?.length ?? 0) > 0 &&
                  <span className="book-badge" title={it.holdings_hit.join(", ")}>●</span>}
                {it.title}
              </span>
            </li>
          ))}
          {filtered.length === 0 && <li className="news-none">no matches</li>}
        </ul>
        <div className="news-detail">
          {cur ? (
            <>
              <div className="news-detail-meta">
                <span className="news-src">{cur.source}</span>
                {cur.published && <span className="news-when">{relTime(cur.published)}</span>}
                {(cur.holdings_hit?.length ?? 0) > 0 &&
                  <span className="book-tags">{cur.holdings_hit.join(" · ")}</span>}
                {cur.url &&
                  <a className="ext-link" href={cur.url} target="_blank" rel="noreferrer">open original ↗</a>}
              </div>
              <h3 className="news-detail-title">{cur.title}</h3>
              {body
                ? <div className="news-detail-body">{body}</div>
                : <p className="empty">Headline only — open the original for the full story.</p>}
            </>
          ) : <p className="empty">no matches</p>}
        </div>
      </div>
      <div className="news-foot">
        {filtered.length} of {items.length} · {data?.sources_read?.length ?? 0} feeds
        {failed > 0 && <span className="news-foot-fail"> · {failed} feed{failed > 1 ? "s" : ""} failed</span>}
        <span className="news-foot-kbd">j/k or ↑/↓ to move</span>
      </div>
    </div>
  );
}

function WidgetCard({ lane, widget, forceTick }: { lane: string; widget: Widget; forceTick: number }) {
  // parameterized widgets: the selected symbol scopes the run AND the cache
  const [symbol, setSymbol] = useState<string | null>(widget.symbols?.[0] ?? null);
  // Reconcile against the CURRENT widget: a pack swap can replace the symbol list while this component
  // instance (same widget id) keeps its selected-symbol state — snap to a valid symbol so a bars/
  // position widget never queries a ticker the freshly-loaded persona doesn't hold. Driving the run +
  // cache key off effSymbol (not raw state) means the query is always valid even before the state-sync
  // effect below commits.
  const effSymbol = symbol && widget.symbols?.includes(symbol) ? symbol : (widget.symbols?.[0] ?? null);
  const ck = effSymbol ? `${widget.id}:${effSymbol}` : widget.id;
  // boot from cache: instant paint with last-known data, honest CACHED chip
  const [state, setState] = useState<SurfaceState>(() => {
    const c = loadCache(lane, ck);
    return c
      ? { status: "ok", data: c.data, at: new Date(c.at), tookSecs: 0, cached: true }
      : { status: "idle" };
  });
  const [now, setNow] = useState(new Date());
  // seed from the cache timestamp — otherwise the interval treats fresh cache as ancient and
  // re-runs immediately, defeating the cache
  const lastRun = useRef<number>(
    state.status === "ok" && state.cached ? state.at.getTime() : 0,
  );

  const run = useCallback(async () => {
    const startedAt = new Date();
    lastRun.current = startedAt.getTime();
    // while a cached body is showing, refresh WITHOUT blanking it (sweep over stale data)
    setState((s) =>
      s.status === "ok" ? { ...s, refreshing: true } : { status: "running", startedAt },
    );
    try {
      let data: unknown;
      if (widget.source.type === "bus") {
        data = await dedupedFetch(`bus:${widget.source.lane || lane}`, () =>
          listEvents({ lane: (widget.source as { lane?: string }).lane || lane, limit: 12 }));
      } else {
        data = await dedupedFetch(
          `${lane}:${JSON.stringify(widget.source)}:${effSymbol ?? ""}`,
          async () => JSON.parse(await runWidget(lane, widget.id, effSymbol)) as unknown,
        );
      }
      saveCache(lane, ck, data);
      const tookSecs = Math.round((Date.now() - startedAt.getTime()) / 1000);
      setState({ status: "ok", data, at: new Date(), tookSecs });
    } catch (e) {
      setState({ status: "error", message: String(e), at: new Date() });
    }
  }, [lane, widget, effSymbol, ck]);

  useEffect(() => {
    // on mount/symbol-switch: run only if cache is missing or stale per the widget's policy (manual widgets
    // with cache stay cached until the user asks)
    const c = loadCache(lane, ck);
    const age = c ? Date.now() - new Date(c.at).getTime() : Infinity;
    if (c) setState({ status: "ok", data: c.data, at: new Date(c.at), tookSecs: 0, cached: true });
    if (!c || age >= staleAfter(widget.refresh)) void run();
    const t = setInterval(() => {
      setNow(new Date());
      const since = Date.now() - lastRun.current;
      if (widget.refresh === "local60s" && since >= LOCAL_MS) void run();
      if (widget.refresh === "local30m" && since >= LOCAL30M_MS) void run();
      if (widget.refresh === "market10m" && since >= MARKET_MS && inMarketWindow(new Date())) void run();
    }, 15_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, widget.refresh, ck]);

  useEffect(() => {
    if (forceTick > 0) void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceTick]);

  // Keep the selected-symbol STATE valid when the widget's symbol list changes under us (a pack swap).
  // effSymbol already keeps the run/cache key correct; this just snaps the state so the pills highlight
  // the right symbol. ck derives from effSymbol, so this setSymbol won't re-trigger a run.
  useEffect(() => {
    if (symbol !== null && widget.symbols && !widget.symbols.includes(symbol)) {
      setSymbol(widget.symbols[0] ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.symbols]);

  const elapsed = state.status === "running"
    ? Math.max(0, Math.round((now.getTime() - state.startedAt.getTime()) / 1000))
    : 0;
  const clock = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const nav = useNav();
  // viz widgets expand full-bleed on a title click (DASH renders just this widget; the masthead ◀ returns).
  const expandable = widget.kind === "viz";

  return (
    <section className={`widget bezel span-${widget.span} rows-${widget.rows ?? 1} kind-${widget.kind}`}>
      <header>
        <span className={`widget-title${expandable ? " linkable" : ""}`}
              {...(expandable
                ? { role: "button", tabIndex: 0, title: "Expand to full view",
                    onClick: () => nav.navigate({ zone: "dash", widget: widget.id }) }
                : {})}>
          {widget.title}
          {expandable && <span className="expand-glyph"> ⤢</span>}
          {state.status === "ok" && widget.title_path && (() => {
            // surface a value from the data (e.g. the resolved home city) accented in the title, so
            // the title reflects the loaded persona instead of a hardcoded place
            const v = pluck(state.data, widget.title_path);
            return v != null && v !== "" ? <span className="widget-title-accent"> · {String(v)}</span> : null;
          })()}
        </span>
        {(widget.symbols?.length ?? 0) > 0 && (
          <span className="symbol-pills">
            {widget.symbols.map((s) => (
              <button key={s} className={s === effSymbol ? "active" : ""}
                      onClick={() => setSymbol(s)}>{s}</button>
            ))}
          </span>
        )}
        <span className="surface-when">
          {state.status === "ok" && `AS OF ${clock(state.at)} · ${state.tookSecs}S`}
          {state.status === "error" && `FAILED ${clock(state.at)}`}
          {state.status === "running" && `ACQUIRING… ${elapsed}S`}
          {state.status === "ok" && state.cached && <em className="auto-chip cached-chip">CACHED</em>}
          {state.status === "ok" && state.refreshing && <em className="auto-chip">REFRESHING…</em>}
          {state.status === "ok" && widget.refresh !== "manual" && (
            <em className="auto-chip">{widget.refresh === "market10m" ? "AUTO·MKT" : "AUTO"}</em>
          )}
        </span>
        <button onClick={() => void run()}
                disabled={state.status === "running" || (state.status === "ok" && state.refreshing === true)}>⟳</button>
      </header>
      {(state.status === "running" || (state.status === "ok" && state.refreshing)) && <div className="sweep" />}
      {state.status === "error" && <p className="surface-error">{state.message}</p>}
      {state.status === "ok" && (
        <ErrorBoundary resetKey={`${widget.id}`}>
          {widget.kind === "stat" && <StatBody data={state.data} widget={widget} />}
          {widget.kind === "table" && (
            <div className="widget-table"><JsonView data={pluck(state.data, widget.value_path)} columns={widget.columns} /></div>
          )}
          {widget.kind === "feed" && <FeedBody events={state.data as BusEvent[]} />}
          {widget.kind === "news" && <NewsReader data={state.data as WireDigest} />}
          {widget.kind === "viz" && (() => {
            // viz widgets honor value_path like stat/table do — so one rich source (e.g.
            // unwind --json) can feed many viz widgets, each pulling its own sub-shape.
            // No-op for value_path=null (the finance viz widgets whose command output IS the shape).
            const d = pluck(state.data, widget.value_path) as Record<string, unknown>;
            const C = VIZ_COMP[sniffViz(d)];
            return C ? <C data={d as never} /> : <JsonView data={d} />;
          })()}
        </ErrorBoundary>
      )}
    </section>
  );
}

function stripFrontmatter(raw: string): string {
  const m = raw.match(/^---\n[\s\S]*?\n---\n?/);
  return m ? raw.slice(m[0].length) : raw;
}

// The browsable take-series panel: lists a vault dir newest-first and renders one
// markdown take at a time with prev/next. Self-fetches (listVaultDir + readDoc) — it bypasses the
// generic run()/cache pipeline because its "data" is a file list + per-index reads, not one blob.
// Re-lists on vault-changed so a freshly-written take appears (and jumps to front) without a click.
function DocSeriesCard({ widget, forceTick }: { widget: Widget; forceTick: number }) {
  const nav = useNav();
  const dir = widget.source.type === "file" ? widget.source.path : "";
  const [files, setFiles] = useState<DirDoc[]>([]);
  const [idx, setIdx] = useState(0);
  const [raw, setRaw] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);

  const relist = useCallback(() => {
    listVaultDir(dir)
      .then((f) => { setFiles(f); setIdx(0); setErr(null); })
      .catch((e) => setErr(String(e)));
  }, [dir]);

  useEffect(() => {
    relist();
    let un: (() => void) | undefined;
    void onVaultChanged(relist).then((u) => { un = u; });
    return () => un?.();
  }, [relist]);

  // Re-list on a pack swap (forceTick bumps). The source dir path is pack-INVARIANT (resolved
  // pack-aware Rust-side), so neither `relist`'s dep nor onVaultChanged fires on a swap — without this
  // the panel shows the PREVIOUS persona's docs (the real corpus bleeding into a demo) until a manual ⟳.
  useEffect(() => {
    if (forceTick > 0) relist();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceTick]);

  const current = files[idx];
  useEffect(() => {
    if (!current) { setRaw(""); return; }
    readDoc(current.path).then((t) => setRaw(stripFrontmatter(t))).catch((e) => setErr(String(e)));
  }, [current]);

  return (
    <section className={`widget bezel span-${widget.span} doc-series`}>
      <header>
        <span className="widget-title">{widget.title}</span>
        <span className="surface-when">
          {files.length > 0 ? `${idx + 1} / ${files.length}` : "NO TAKES YET"}
          {idx === 0 && files.length > 0 && <em className="auto-chip">LATEST</em>}
        </span>
        <span className="doc-series-nav">
          <button onClick={() => setIdx((i) => Math.min(files.length - 1, i + 1))}
                  disabled={idx >= files.length - 1} title="Older">◀</button>
          <button onClick={() => setIdx((i) => Math.max(0, i - 1))}
                  disabled={idx <= 0} title="Newer">▶</button>
          <button onClick={relist} title="Reload list">⟳</button>
        </span>
      </header>
      {err && <p className="surface-error">{err}</p>}
      {!err && current && (
        <ErrorBoundary resetKey={current.path}>
          <article className="vault-md doc-series-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              // identity transform so the `wiki:` scheme survives react-markdown's sanitizer
              urlTransform={(u) => u}
              components={{
                a({ href, children }) {
                  // a card's [[wikilink]] -> preprocessLinks rewrote it to wiki:<vault-path>; route it
                  // cross-zone to VAULT (the card's panel can't open docs itself). Target carries no
                  // .md; VaultZone matches an exact doc path, so append it.
                  if (href?.startsWith("wiki:")) {
                    const t = decodeURIComponent(href.slice(5)).split("#")[0].trim();
                    const doc = t.endsWith(".md") ? t : `${t}.md`;
                    return (
                      <a
                        className="wikilink"
                        href="#"
                        title={`open ${doc} in VAULT`}
                        onClick={(e) => {
                          e.preventDefault();
                          nav.navigate({ zone: "vault", doc });
                        }}
                      >
                        {children}
                      </a>
                    );
                  }
                  return (
                    <a href={href} target="_blank" rel="noreferrer">
                      {children}
                    </a>
                  );
                },
                // ![[embed]] → preprocessLinks rewrote to vaultimg:<path>; resolve it to a data URI
                // (the same VaultImage the VAULT zone uses) so the scan report's matrix SVG renders
                // here too — the doc_series was link-aware but not image-aware (the broken embed).
                img({ src, alt }) {
                  const dir = current.path.includes("/")
                    ? current.path.slice(0, current.path.lastIndexOf("/"))
                    : "";
                  return <VaultImage src={typeof src === "string" ? src : undefined}
                                     alt={typeof alt === "string" ? alt : ""} docDir={dir} />;
                },
              }}
            >
              {preprocessLinks(raw)}
            </ReactMarkdown>
          </article>
        </ErrorBoundary>
      )}
      {!err && !current && <p className="empty">No takes written yet — ask for a “market take.”</p>}
    </section>
  );
}

export default function Dash({ reloadKey }: { reloadKey?: string } = {}) {
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [group, setGroup] = useState<string>("");
  const [lane, setLane] = useState<string>("");
  const [allTick, setAllTick] = useState(0);
  // Dashboard Studio: edit mode. LOCKED IS ALWAYS THE DEFAULT (operator ruling —
  // browsing must never accidentally drag); any tab or pack change re-locks. The toggle renders
  // only on the real corpus (reloadKey "") — pack dashboards are transients the save command
  // rejects anyway (the demo-seal belt in commands.rs).
  const [unlocked, setUnlocked] = useState(false);
  useEffect(() => setUnlocked(false), [lane, reloadKey]);

  // Mirror the current selection in a ref so `load()` can reconcile it without being recreated on
  // every group/lane change (which would re-run the mount effect).
  const selRef = useRef({ group: "", lane: "" });
  useEffect(() => {
    selRef.current = { group, lane };
  }, [group, lane]);

  // (Re)fetch the dashboard LAYOUT — not just the data. A weight pack can now DESCRIBE the dashboards
  // (v2), so the tab structure itself changes on a pack swap; refetching only widget data would
  // leave a stale layout whose tabs/widgets no longer resolve ("unknown widget"). `preserve` keeps the
  // active group + subtab when they still exist in the new set (sit on Finance, swap personas → stay on
  // Finance Core), else falls back to the first group/lane (a vanished tab like Unwind → Core).
  const load = useCallback((preserve: boolean, bump = false) => {
    void listDashboards().then((ds) => {
      setDashboards(ds);
      if (ds.length === 0) {
        setGroup("");
        setLane("");
      } else {
        const byGroup = new Map<string, Dashboard[]>();
        for (const d of ds) {
          const g = d.group || d.title;
          (byGroup.get(g) ?? byGroup.set(g, []).get(g)!).push(d);
        }
        const { group: pg, lane: pl } = selRef.current;
        const g = preserve && byGroup.has(pg) ? pg : ds[0].group || ds[0].title;
        const inGroup = byGroup.get(g)!;
        const l = preserve && inGroup.some((d) => d.lane === pl) ? pl : inGroup[0].lane;
        setGroup(g);
        setLane(l);
      }
      // Bump the widget-refetch tick HERE — in the same state batch as the new layout — so each
      // WidgetCard's forceTick effect runs against the NEW widget props. Ticking BEFORE the layout
      // lands (the old synchronous bump) refetched with the prior pack's widget closure, so a bars
      // widget queried a symbol the freshly-loaded persona doesn't hold ("<sym> not in config").
      if (bump) setAllTick((t) => t + 1);
    });
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  // group dashboards into nav groups (ungrouped → its own group keyed by title), preserving order
  const groups = useMemo(() => {
    const m = new Map<string, Dashboard[]>();
    for (const d of dashboards) {
      const g = d.group || d.title;
      (m.get(g) ?? m.set(g, []).get(g)!).push(d);
    }
    return [...m.entries()];
  }, [dashboards]);

  const groupDashboards = useMemo(
    () => groups.find(([g]) => g === group)?.[1] ?? [],
    [groups, group],
  );
  const current = useMemo(() => dashboards.find((d) => d.lane === lane), [dashboards, lane]);

  // Expand-in-place: a `widget` ref (set by clicking a viz widget's title) renders just that widget
  // full-bleed; the masthead ◀ pops the ref and restores the grid (the subtab is local state, preserved).
  const nav = useNav();
  const expandedWidget = useMemo(
    () => (nav.current.zone === "dash" && nav.current.widget && current
      ? current.widgets.find((w) => w.id === nav.current.widget)
      : undefined),
    [nav.current, current],
  );

  // Studio commit: fold an RGL layout change back into the dashboard, update state, persist.
  // Auto-save on every drag/resize commit — unlock is the deliberate act, so edits inside it are
  // intentional; the rust side writes temp-then-rename and stamps owner:"user".
  const commitLayout = useCallback((d: Dashboard, rawCells: StudioCell[], activeId: string | null) => {
    // the no-widget-loss invariant: resolve every overlap BEFORE the merge (see resolveOverlaps)
    const cells = resolveOverlaps(d, rawCells, studioCols(d.lane), activeId);
    const byId = new Map(cells.map((c) => [c.i, c]));
    const next: Dashboard = {
      ...d,
      owner: "user",
      widgets: d.widgets.map((w) => {
        const c = byId.get(w.id);
        return c ? { ...w, layout: { x: c.x, y: c.y, w: c.w, h: c.h } } : w;
      }),
    };
    // no-op guard: RGL fires onLayoutChange on mount too — only persist a real move
    const changed = next.widgets.some((w, i) =>
      JSON.stringify(w.layout) !== JSON.stringify(d.widgets[i].layout));
    if (!changed) return;
    setDashboards((ds) => ds.map((x) => (x.lane === next.lane ? next : x)));
    saveDashboard(next).catch((e) => console.error("studio save failed:", e));
  }, []);

  // "Return to default" (two-click confirm — native webviews don't reliably ship window.confirm,
  // and a console-styled arm-then-fire beats a modal anyway). Snaps the lane back to its compiled
  // built-in; the rust side banks the replaced state to .backups/ first, so even this is undoable.
  // Re-locks on completion: you're back at the known-good state, in the known-safe mode.
  const [armReset, setArmReset] = useState(false);
  useEffect(() => setArmReset(false), [lane, unlocked]);
  useEffect(() => {
    if (!armReset) return;
    const t = setTimeout(() => setArmReset(false), 4000); // disarm if not confirmed
    return () => clearTimeout(t);
  }, [armReset]);
  const onReset = useCallback(() => {
    if (!current) return;
    if (!armReset) { setArmReset(true); return; }
    setArmReset(false);
    resetDashboard(current.lane)
      .then((fresh) => {
        setDashboards((ds) => ds.map((x) => (x.lane === fresh.lane ? fresh : x)));
        setUnlocked(false);
        setAllTick((n) => n + 1); // repaint widgets against the restored layout
      })
      .catch((e) => console.error("studio reset failed:", e));
  }, [current, armReset]);

  // Unlock click. A legacy dashboard converts first (synthesized layouts, saved immediately —
  // from that moment it's studio-managed + user-owned); a studio dashboard just unlocks.
  const toggleUnlock = useCallback(() => {
    if (!current) return;
    if (unlocked) { setUnlocked(false); return; }
    if (!isStudioManaged(current)) {
      const migrated = migrateLegacyLayouts(current, studioCols(current.lane));
      setDashboards((ds) => ds.map((x) => (x.lane === migrated.lane ? migrated : x)));
      saveDashboard(migrated).catch((e) => console.error("studio migrate-save failed:", e));
    }
    setUnlocked(true);
  }, [current, unlocked]);

  // On a pack swap (reloadKey changes) re-fetch the LAYOUT (the pack may describe its own dashboards)
  // AND refetch every widget — WITHOUT remounting Dash, so the active group/subtab is preserved when it
  // survives the swap (sit on Finance, swap personas). The widget-refetch tick is bumped INSIDE load,
  // after the new layout commits (see load()). Skip the initial mount.
  const firstReload = useRef(true);
  useEffect(() => {
    if (firstReload.current) {
      firstReload.current = false;
      return;
    }
    load(true, true);
  }, [reloadKey, load]);

  return (
    <div className="dash">
      <div className="strip">
        {groups.map(([g, ds]) => (
          <button key={g} className={g === group ? "active" : ""}
                  onClick={() => { setGroup(g); setLane(ds[0].lane); }}>
            {g}
          </button>
        ))}
        <div className="spacer" />
        <span className="surface-when">
          {inMarketWindow(new Date()) ? "MARKET OPEN — AUTO 10M" : "MARKET CLOSED — MANUAL"}
        </span>
        {/* Studio unlock: native-only until the served door grows a write carve-out
            (save_dashboard sits in WRITE_GATED → a PWA unlock would 403 on save). */}
        {current && !reloadKey && isTauri() && unlocked && (
          <button className={`reset-default${armReset ? " armed" : ""}`} onClick={onReset}
                  title={armReset ? "Click again to confirm — current layout is banked to .backups/ first"
                                  : "Snap this dashboard back to its built-in default"}>
            {armReset ? "↺ CONFIRM RESET?" : "↺ DEFAULT"}
          </button>
        )}
        {current && !reloadKey && isTauri() && (
          <button className={`unlock-toggle${unlocked ? " editing" : ""}`} onClick={toggleUnlock}
                  title={unlocked ? "Lock the layout (edits are already saved)"
                                  : "Unlock: drag + resize widgets; edits save as you go"}>
            {unlocked ? "🔓 EDITING" : "🔒 LOCKED"}
          </button>
        )}
        <button className="primary" onClick={() => setAllTick((n) => n + 1)}>⟳ REFRESH ALL</button>
      </div>
      {groupDashboards.length > 1 && (
        <div className="substrip">
          {groupDashboards.map((d) => (
            <button key={d.lane} className={d.lane === lane ? "active" : ""}
                    onClick={() => setLane(d.lane)}>
              {d.title}
            </button>
          ))}
        </div>
      )}
      {current && expandedWidget && (
        <div className="dash-expanded">
          <button className="expanded-back" onClick={() => nav.back()}>← back to {current.title}</button>
          <WidgetCard key={expandedWidget.id} lane={current.lane} widget={expandedWidget} forceTick={allTick} />
        </div>
      )}
      {current && !expandedWidget && (
        isStudioManaged(current)
          ? <StudioGrid dashboard={current} forceTick={allTick} editable={unlocked}
                        onCommit={(cells, activeId) => commitLayout(current, cells, activeId)} />
          : <div className={`dash-grid lane-${current.lane}`} key={current.lane}>
              {current.widgets.map((w) =>
                w.kind === "doc_series"
                  ? <DocSeriesCard key={w.id} widget={w} forceTick={allTick} />
                  : <WidgetCard key={w.id} lane={current.lane} widget={w} forceTick={allTick} />,
              )}
            </div>
      )}
      {!current && <p className="empty">NO DASHBOARDS CONFIGURED</p>}
    </div>
  );
}

// ————— Dashboard Studio (checkpoint B) ————————————————————————————————————————
// The react-grid-layout render path. A dashboard enters it ONLY when every widget carries an
// explicit `layout` — legacy span/rows configs keep the dense-flow CSS grid above, byte-for-byte
// untouched (zero regression by construction; the migration happens at first unlock, checkpoint C).
// Grid units: rowHeight 110 + 10px margins ≈ h:1 = a stat tile, h:3 ≈ the legacy 340px card.
// noCompactor = explicit placement is honored verbatim, deliberate gaps included — that's the
// Studio's spatial-control contract (dense-flow auto-packing is the legacy path's job).

function isStudioManaged(d: Dashboard): boolean {
  return d.widgets.length > 0 && d.widgets.every((w) => !!w.layout);
}

// mirrors App.css's lane rule: .dash-grid.lane-finance runs 6 cols, everything else 4
export function studioCols(lane: string): number {
  return lane === "finance" ? 6 : 4;
}

// The RGL cell coordinate shape (what onLayoutChange yields per item).
export interface StudioCell { i: string; x: number; y: number; w: number; h: number }

// Grid-occupancy bookkeeping shared by the migration + the overlap resolver.
function makeOccupancy(cols: number) {
  const occupied: boolean[][] = [];
  const fits = (x: number, y: number, w: number, h: number) => {
    if (x < 0 || x + w > cols) return false;
    for (let r = y; r < y + h; r++) {
      for (let c = x; c < x + w; c++) if (occupied[r]?.[c]) return false;
    }
    return true;
  };
  const claim = (x: number, y: number, w: number, h: number) => {
    for (let r = y; r < y + h; r++) {
      occupied[r] = occupied[r] ?? Array(cols).fill(false);
      for (let c = x; c < x + w; c++) occupied[r][c] = true;
    }
  };
  return { fits, claim };
}

// THE NO-WIDGET-LOSS INVARIANT (found via a field bug): widgets are interchangeable
// blocks — a drop may NEVER hide or lose another widget. RGL under noCompactor resolves only the
// FIRST collision (it pushes the occupant one row) and never re-resolves the pushed widget's own
// landing — in the incident the pushed stat tile landed UNDER a 3×3 panel and vanished from view.
// This pass runs on every commit: widgets that didn't move keep their cells; every MOVED cell
// (the user's drag AND RGL's pushes) that overlaps anything is first-fit relocated to the nearest
// free slot, scanning from the top — which lands a displaced tile in the dragged tile's vacated
// cell (swap semantics) in the common case. Count in == count out, by construction.
export function resolveOverlaps(
  prev: Dashboard,
  cells: StudioCell[],
  cols: number,
  activeId?: string | null,
): StudioCell[] {
  const prevById = new Map(prev.widgets.map((w) => [w.id, w.layout]));
  const movedFromPrev = (c: StudioCell) => {
    const p = prevById.get(c.i);
    return !p || p.x !== c.x || p.y !== c.y || p.w !== c.w || p.h !== c.h;
  };
  const { fits, claim } = makeOccupancy(cols);
  const out = new Map<string, StudioCell>();
  for (const s of cells.filter((c) => !movedFromPrev(c))) {
    claim(s.x, s.y, s.w, s.h);
    out.set(s.i, s);
  }
  // The USER-dragged item (activeId, captured at drag/resize start) claims its dropped cell FIRST
  // among the movers — coordinates alone can't distinguish the deliberate drag from an RGL push,
  // and relocating the wrong one silently undoes the user's drag instead of the push.
  const movers = cells.filter(movedFromPrev)
    .sort((a, b) => Number(b.i === activeId) - Number(a.i === activeId));
  for (const m of movers) {
    let { x, y } = m;
    const w = Math.max(1, Math.min(m.w, cols));
    const h = Math.max(1, m.h);
    if (!fits(x, y, w, h)) {
      placed: for (y = 0; ; y++) {
        for (x = 0; x <= cols - w; x++) if (fits(x, y, w, h)) break placed;
      }
    }
    claim(x, y, w, h);
    out.set(m.i, { ...m, x, y, w, h });
  }
  return cells.map((c) => out.get(c.i)!);
}

// Legacy→Studio migration (checkpoint C): synthesize explicit layouts from the span/rows flow
// model via first-fit row-dense placement — mirroring the `grid-auto-flow: row dense` packing the
// legacy CSS grid uses. Heights are a HEURISTIC (legacy rows are content-sized, which a fixed-unit
// grid cannot express): stat = 1 unit (~110px) · doc_series = 6 (its CSS is tall) · everything
// else = 3 units per legacy row-span (≈ the 340px card). The result is a STARTING POINT the user
// drags into shape, not a pixel-faithful copy — say so in any UX copy near this.
export function migrateLegacyLayouts(d: Dashboard, cols: number): Dashboard {
  const { fits, claim } = makeOccupancy(cols);
  const widgets = d.widgets.map((wg) => {
    if (wg.layout) { claim(wg.layout.x, wg.layout.y, wg.layout.w, wg.layout.h); return wg; }
    const w = Math.max(1, Math.min(wg.span || 2, cols));
    const h = wg.kind === "stat" ? 1 : wg.kind === "doc_series" ? 6 : 3 * (wg.rows ?? 1);
    let px = 0, py = 0;
    placed: for (py = 0; ; py++) {
      for (px = 0; px <= cols - w; px++) if (fits(px, py, w, h)) break placed;
    }
    claim(px, py, w, h);
    return { ...wg, layout: { x: px, y: py, w, h } };
  });
  return { ...d, widgets, owner: "user" };
}

function StudioGrid({ dashboard, forceTick, editable, onCommit }: {
  dashboard: Dashboard;
  forceTick: number;
  editable: boolean;
  onCommit: (cells: StudioCell[], activeId: string | null) => void;
}) {
  const { width, containerRef, mounted } = useContainerWidth();
  // the item the user is actually manipulating — captured at drag/resize start, consumed by the
  // overlap resolver so the deliberate move wins over RGL's collision pushes.
  const activeRef = useRef<string | null>(null);
  const cells = dashboard.widgets.map((w) => ({
    i: w.id,
    x: w.layout!.x, y: w.layout!.y, w: w.layout!.w, h: w.layout!.h,
    static: !editable,
  }));
  return (
    <div className={`studio-grid${editable ? " editing" : ""}`} key={dashboard.lane} ref={containerRef}>
      {mounted && (
        <GridLayout
          width={width}
          layout={cells}
          gridConfig={{ cols: studioCols(dashboard.lane), rowHeight: 110,
                        margin: [10, 10], containerPadding: [16, 10] }}
          dragConfig={{ enabled: editable }}
          resizeConfig={{ enabled: editable }}
          compactor={noCompactor}
          onDragStart={(_l, item) => { activeRef.current = item?.i ?? null; }}
          onResizeStart={(_l, item) => { activeRef.current = item?.i ?? null; }}
          onLayoutChange={(layout) => {
            // fires on mount too — commitLayout's no-op guard drops non-moves; only editable
            // sessions ever reach the save path.
            if (editable) onCommit(layout as unknown as StudioCell[], activeRef.current);
          }}
        >
          {dashboard.widgets.map((w) => (
            <div key={w.id} className="studio-cell">
              {w.kind === "doc_series"
                ? <DocSeriesCard widget={w} forceTick={forceTick} />
                : <WidgetCard lane={dashboard.lane} widget={w} forceTick={forceTick} />}
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
