// Domain dashboards — DD/APM-style observability pages over harness live data.
// A dashboard = config'd grid of widgets (source × kind × layout). Refresh discipline (the maintainer-
// ratified): NOWHERE NEAR DD-realtime — market10m widgets auto-refresh every 10 min ONLY
// inside US market hours (provider politeness); local60s for bus/file sources; REFRESH ALL
// always available; every widget carries AS-OF + took chrome.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listDashboards, listEvents, listVaultDir, onVaultChanged, readDoc, runWidget } from "./api";
import ErrorBoundary from "./ErrorBoundary";
import JsonView from "./JsonView";
import type { BusEvent, Dashboard, DirDoc, SurfaceState, Widget } from "./types";
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
import { useNav } from "./nav";
import { preprocessLinks, VaultImage } from "./VaultZone";

const MARKET_MS = 10 * 60_000;
const LOCAL_MS = 60_000;

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
  return refresh === "market10m" ? MARKET_MS : refresh === "local60s" ? LOCAL_MS : Infinity;
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
  "rank-bar": BarChart,
};

function StatBody({ data, widget }: { data: unknown; widget: Widget }) {
  const v = pluck(data, widget.value_path);
  const num = typeof v === "number" ? v : Number(v);
  const text = Number.isFinite(num) ? fmtNum(num) : String(v ?? "—");
  const sign = Number.isFinite(num) && widget.suffix === "%" ? (num > 0 ? "pos" : num < 0 ? "neg" : "") : "";
  return (
    <div className={`stat-big ${sign}`}>
      {Number.isFinite(num) && num > 0 && widget.suffix === "%" ? "+" : ""}
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

  return (
    <section className={`widget bezel span-${widget.span} rows-${widget.rows ?? 1}`}>
      <header>
        <span className="widget-title">
          {widget.title}
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
  // the panel shows the PREVIOUS persona's docs (e.g. the real openings scan) until a manual ⟳.
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

  // Mirror the current selection in a ref so `load()` can reconcile it without being recreated on
  // every group/lane change (which would re-run the mount effect).
  const selRef = useRef({ group: "", lane: "" });
  useEffect(() => {
    selRef.current = { group, lane };
  }, [group, lane]);

  // (Re)fetch the dashboard LAYOUT — not just the data. A weight pack can now DESCRIBE the dashboards
  // (b15.8 v2), so the tab structure itself changes on a pack swap; refetching only widget data would
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
      {current && (
        <div className="dash-grid" key={current.lane}>
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
