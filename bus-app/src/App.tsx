import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { isTauri } from "./transport";
import "./App.css";
import {
  ackEvents, appVersion, distinctMeta, getActivePack, getConfig, listEvents, listPacks, listSurfaces,
  onBusSelect, onBusUpdated, type PackInfo, runProducer, runSurface, setActivePack, watchmenStatus,
} from "./api";
import JsonView from "./JsonView";
import VizZone from "./viz/VizZone";
import VaultZone from "./VaultZone";
import Dash from "./Dash";
import ErrorBoundary from "./ErrorBoundary";
import PushBell from "./PushBell";
import UpdatePill from "./UpdatePill";
import { NavContext, payloadRef, resolveRef, useNav, type Nav, type Ref } from "./nav";
import { clearTheme, setTheme, storedTheme, THEMES, useTheme, type Theme } from "./theme";
import { PUBLISHED } from "./published";
import type {
  AgentHealth, AppConfig, BusEvent, DistinctMeta, Surface, SurfaceState, WatchmenStatus,
} from "./types";

type Health = "green" | "amber" | "red";

const SEV: Record<string, string> = { alert: "sev-alert", warn: "sev-warn", info: "sev-info" };

// ————— Triage bands — the Inbox groups by the verdict the data ALREADY carries
// (severity + kind), promoting it from a dot to a hierarchy. ACT/WATCH are the "clear these" inbox;
// CATALYST WIRE is a skim-stream of single-name catalysts (info `catalyst` events, bus option B);
// FILINGS is the primary-source rail. Empty bands hide.
type Band = "act" | "watch" | "wire" | "filings";
const FILING_KINDS = new Set(["filing_drop", "print_landed", "filing"]);
function bandFor(e: BusEvent): Band {
  if (FILING_KINDS.has(e.kind)) return "filings";
  if (e.kind === "catalyst" || e.severity === "info") return "wire";
  return e.severity === "alert" ? "act" : "watch";
}
const BANDS: { key: Band; label: string; accent: string }[] = [
  { key: "act", label: "ACT", accent: "sev-alert" },
  { key: "watch", label: "WATCH", accent: "sev-warn" },
  { key: "wire", label: "CATALYST WIRE", accent: "sev-info" },
  { key: "filings", label: "FILINGS", accent: "sev-file" },
];
// Urgency = the "needs you" tiers (alert+warn). Info catalysts are wire — a skim-stream, not a
// to-do — so they never drive the tab badge or the ambient health (decision A).
const isUrgent = (e: BusEvent): boolean => e.severity !== "info";

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const mins = Math.round((Date.now() - then) / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / (60 * 24))}d`;
}

function clock(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ————— Watch-floor: vitals band (A) + default digest (B) + ambient health (E) ————————————————————

interface Agenda {
  prints: { symbol: string; days_out: number }[];
  wash: { today_poisoned: boolean; next_clean_start: string | null } | null;
}

// The ambient resting state: worst of the watchmen's own health and the unread signal severity.
// Green is the calm default — a healthy, quiet harness; amber/red only when something earns it.
function harnessHealth(wm: WatchmenStatus | null, unread: BusEvent[]): Health {
  if (wm?.overall === "red") return "red";
  if (unread.some((e) => e.severity === "alert")) return "red";
  if (unread.some((e) => e.severity === "warn")) return "amber";
  return "green";
}

function Cadence({ agent }: { agent: AgentHealth }) {
  return (
    <span className="cadence" title={`${agent.runs_today}/${agent.expected_by_now} runs today`}>
      {agent.cadence.map((t, i) => (
        <i key={i} className={`tick tick-${t.state}`} title={`${t.at} · ${t.state}`} />
      ))}
    </span>
  );
}

function agentStatusWord(a: AgentHealth): string {
  if (a.state === "standby") return "STANDING BY";
  if (a.missed > 0) return `${a.missed} MISSED`;
  if (!a.market_day) return "MARKET CLOSED";
  return "ON SCHEDULE";
}

function WatchmenBand({ wm, health }: { wm: WatchmenStatus | null; health: Health }) {
  const overall = !wm ? "ACQUIRING…"
    : wm.overall === "red" ? "ATTENTION"
    : wm.overall === "standby" ? "STANDING BY" : "ALL SYSTEMS NOMINAL";
  return (
    <section className={`watchmen bezel glow-${health}`}>
      <header className="watchmen-head">
        <span className="watchmen-title">WATCHMEN</span>
        <span className={`watchmen-overall over-${health}`}>{overall}</span>
      </header>
      <div className="watchmen-body">
        {wm?.agents.map((a) => (
          <div className="vital" key={a.id}>
            <div className="vital-id">
              <span className={`vital-led led-${a.state}`} />
              <div className="vital-id-text">
                <span className="vital-name">{a.label}</span>
                <span className={`vital-status status-${a.state}`}>{agentStatusWord(a)}</span>
              </div>
            </div>
            <div className="vital-stat"><span className="stat-label">LAST</span>
              <span className="stat-val">{a.last_run_rel ?? "—"}</span></div>
            <div className="vital-stat"><span className="stat-label">NEXT</span>
              <span className="stat-val">{a.next_run ?? "—"}</span></div>
            <div className="vital-stat"><span className="stat-label">TODAY</span>
              <span className="stat-val">{a.state === "standby" || !a.market_day ? "—" : `${a.runs_today}/${a.expected_by_now}`}</span></div>
            <div className="vital-stat vital-stat-cadence"><span className="stat-label">CADENCE</span>
              <Cadence agent={a} /></div>
          </div>
        ))}
      </div>
    </section>
  );
}

// The always-on status header — promotes the standing-state summary (next pulse /
// print / vest wash / last flag + the urgent headline) out of the "nothing selected" inspector into
// a persistent strip, so system state is glanceable without clearing the pane.
function StatusStrip({ wm, agenda, health, urgent }: {
  wm: WatchmenStatus | null; agenda: Agenda | null; health: Health;
  urgent: { alert: number; warn: number };
}) {
  const pulse = wm?.agents.find((a) => a.id === "pulse");
  const nextPrint = agenda?.prints?.[0];
  const headline = urgent.alert > 0 ? `${urgent.alert} NEED${urgent.alert === 1 ? "S" : ""} YOU`
    : urgent.warn > 0 ? `${urgent.warn} WATCH` : "ALL QUIET";
  return (
    <div className={`statusbar glow-${health}`}>
      <span className={`status-orb orb-${health}`} />
      <span className="status-headline">{headline}</span>
      <dl className="status-agenda">
        <div><dt>next pulse</dt><dd>{pulse?.next_run ?? "—"}</dd></div>
        {nextPrint && <div><dt>next print</dt><dd>{nextPrint.symbol} · {nextPrint.days_out}d</dd></div>}
        {agenda?.wash && (
          <div><dt>vest wash</dt><dd>{agenda.wash.today_poisoned
            ? `poisoned → ${agenda.wash.next_clean_start ?? "—"}` : "clear"}</dd></div>
        )}
        {pulse?.last_flags && <div className="status-lastflag"><dt>last flag</dt><dd>{pulse.last_flags.split(";")[0]}</dd></div>}
      </dl>
    </div>
  );
}

// ————— Inbox zone ————————————————————————————————————————————————————————————————————————————

function Inbox({ onUnread }: { onUnread: (n: number) => void }) {
  const nav = useNav();
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [meta, setMeta] = useState<DistinctMeta>({ lanes: [], kinds: [] });
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [lane, setLane] = useState("");
  const [kind, setKind] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [wm, setWm] = useState<WatchmenStatus | null>(null);
  const [agenda, setAgenda] = useState<Agenda | null>(null);

  const reload = useCallback(async () => {
    const evs = await listEvents({ unreadOnly, lane, kind });
    setEvents(evs);
    setMeta(await distinctMeta());
    // badge = unread URGENT only (alert+warn) — wire catalysts are a skim-stream, not a to-do
    onUnread(evs.filter((e) => !e.read_at && isUrgent(e)).length);
  }, [unreadOnly, lane, kind, onUnread]);

  // Watchmen vitals — fast (reads the run-log); refresh with the feed so a missed run shows promptly.
  const loadWm = useCallback(() => void watchmenStatus().then(setWm).catch(() => setWm(null)), []);

  useEffect(() => {
    void reload();
    loadWm();
    const unsubs = [
      onBusUpdated(() => { void reload(); loadWm(); }),
      onBusSelect((id) => setSelectedId(id)),
    ];
    return () => {
      unsubs.forEach((p) => void p.then((u) => u()));
    };
  }, [reload, loadWm]);

  // Digest agenda (prints + wash) — finance.watch is a live call (~seconds), so load once, lazily,
  // and let it fill in behind the instant watchmen-driven digest. Not refreshed per bus-update.
  useEffect(() => {
    void runSurface("finance.watch")
      .then((raw) => {
        const d = JSON.parse(raw) as { prints?: Agenda["prints"]; wash_sale?: Agenda["wash"] };
        setAgenda({ prints: d.prints ?? [], wash: d.wash_sale ?? null });
      })
      .catch(() => setAgenda(null));
  }, []);

  const selected = useMemo(
    () => events.find((e) => e.id === selectedId) ?? null,
    [events, selectedId],
  );
  // a signal may carry a deep-link in its payload (producer-set `ref`) → a "go to" jump into context
  const payload = useMemo(
    () => (selected ? JSON.parse(selected.payload_json || "{}") : {}),
    [selected],
  );
  const ref = useMemo(() => payloadRef(payload), [payload]);
  const unread = events.filter((e) => !e.read_at);
  // health ignores info catalysts (they're a skim-stream) — only alert/warn move the ambient orb
  const health = harnessHealth(wm, unread.filter(isUrgent));
  const grouped = useMemo(() => {
    const g: Record<Band, BusEvent[]> = { act: [], watch: [], wire: [], filings: [] };
    for (const e of events) g[bandFor(e)].push(e);
    return g;
  }, [events]);
  const urgentCounts = {
    alert: unread.filter((e) => e.severity === "alert").length,
    warn: unread.filter((e) => e.severity === "warn").length,
  };

  const ack = async (ids: number[]) => {
    await ackEvents(ids);
    await reload();
  };

  return (
    <div className="inbox">
      <div className="strip">
        <label className="toggle">
          <input type="checkbox" checked={unreadOnly}
                 onChange={(e) => setUnreadOnly(e.target.checked)} />
          <span>UNREAD ONLY</span>
        </label>
        <select value={lane} onChange={(e) => setLane(e.target.value)}>
          <option value="">LANE · ALL</option>
          {meta.lanes.map((l) => <option key={l} value={l}>{l.toUpperCase()}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">KIND · ALL</option>
          {meta.kinds.map((k) => <option key={k} value={k}>{k.toUpperCase()}</option>)}
        </select>
        <div className="spacer" />
        <button disabled={unread.length === 0}
                onClick={() => void ack(unread.map((e) => e.id))}>
          MARK ALL READ
        </button>
      </div>

      <StatusStrip wm={wm} agenda={agenda} health={health} urgent={urgentCounts} />

      <div className="split">
        <div className="feed bezel">
          {events.length === 0 && <div className="empty">ALL QUIET — THE WATCHMEN ARE STANDING BY</div>}
          {BANDS.map(({ key, label, accent }) => {
            const rows = grouped[key];
            if (rows.length === 0) return null;
            const unreadN = rows.filter((e) => !e.read_at).length;
            return (
              <section key={key} className={`band band-${key}`}>
                <header className={`band-head ${accent}`}>
                  <span className="band-led" />
                  <span className="band-label">{label}</span>
                  <span className="band-count">{unreadN > 0 ? `${unreadN}/${rows.length}` : rows.length}</span>
                </header>
                <ul className="band-rows">
                  {rows.map((e) => (
                    <li key={e.id}
                        className={[
                          e.id === selectedId ? "selected" : "",
                          e.read_at ? "read" : "unread",
                          SEV[e.severity] ?? "sev-info",
                        ].join(" ")}
                        onClick={() => setSelectedId(e.id)}>
                      <span className="led" />
                      {e.lane !== "finance" && <span className="lane-tag">{e.lane}</span>}
                      <span className="row-title">{e.title}</span>
                      <span className="row-when">{relTime(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>

        <aside className="detail bezel">
          {selected ? (
            <>
              <div className={`detail-rule ${SEV[selected.severity] ?? "sev-info"}`} />
              <h2>{selected.title}</h2>
              <p className="detail-meta">
                {selected.producer} · {selected.kind}
                {selected.subject ? ` · ${selected.subject}` : ""} · {selected.created_at}
                {selected.delivered_via.length > 0 && ` · VIA ${selected.delivered_via.join(",").toUpperCase()}`}
              </p>
              {selected.body && <p className="detail-body">{selected.body}</p>}
              <JsonView data={payload} />
              <div className="detail-actions">
                {ref && (
                  <button className="goto"
                          onClick={() => void resolveRef(ref).then(nav.navigate)}>
                    GO TO →
                  </button>
                )}
                {!selected.read_at && (
                  <button className="primary" onClick={() => void ack([selected.id])}>MARK READ</button>
                )}
              </div>
            </>
          ) : (
            <div className="detail-empty">
              <span className={`digest-orb orb-${health}`} />
              <p>select a signal to inspect its payload</p>
            </div>
          )}
        </aside>
      </div>

      <WatchmenBand wm={wm} health={health} />
    </div>
  );
}

// ————— Surfaces zone ——————————————————————————————————————————————————————————

function SurfaceCard({ surface, state, now, onRun }: {
  surface: Surface; state: SurfaceState; now: Date; onRun: () => void;
}) {
  // honest elapsed counter — a 30s multi-board scan must read as "chugging", never "crashed"
  // (first-run feedback: the sweep alone wasn't enough signal for long acquisitions)
  const elapsed = state.status === "running"
    ? Math.max(0, Math.round((now.getTime() - state.startedAt.getTime()) / 1000))
    : 0;
  return (
    <section className={`surface bezel ${state.status}`}>
      <header>
        <span className="lane-tag">{surface.lane}</span>
        <span className="surface-label">{surface.label}</span>
        <span className="surface-when">
          {state.status === "ok" && `AS OF ${clock(state.at)} · ${state.tookSecs}S`}
          {state.status === "error" && `FAILED ${clock(state.at)}`}
          {state.status === "running" && `ACQUIRING… ${elapsed}S`}
        </span>
        <button onClick={onRun} disabled={state.status === "running"}>
          {state.status === "idle" ? "ACQUIRE" : "⟳ REFRESH"}
        </button>
      </header>
      {state.status === "running" && <div className="sweep" />}
      {state.status === "ok" && <JsonView data={state.data} />}
      {state.status === "error" && <p className="surface-error">{state.message}</p>}
      {state.status === "idle" && (
        <p className="empty">STANDBY — ACQUIRE TO RUN `{surface.cmd} {surface.args.join(" ")}`</p>
      )}
    </section>
  );
}

function Surfaces() {
  const [surfaces, setSurfaces] = useState<Surface[]>([]);
  const [states, setStates] = useState<Record<string, SurfaceState>>({});
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    void listSurfaces().then(setSurfaces);
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const run = async (id: string) => {
    const startedAt = new Date();
    setStates((s) => ({ ...s, [id]: { status: "running", startedAt } }));
    try {
      const raw = await runSurface(id);
      const tookSecs = Math.round((Date.now() - startedAt.getTime()) / 1000);
      setStates((s) => ({
        ...s, [id]: { status: "ok", data: JSON.parse(raw), at: new Date(), tookSecs },
      }));
    } catch (e) {
      setStates((s) => ({ ...s, [id]: { status: "error", message: String(e), at: new Date() } }));
    }
  };

  const lanes = Array.from(new Set(surfaces.map((s) => s.lane)));
  return (
    <div className="deck">
      {lanes.map((lane, i) => (
        <div className="deck-lane" key={lane} style={{ animationDelay: `${i * 60}ms` }}>
          <span className="deck-lane-label">{lane.toUpperCase()} /</span>
          {surfaces.filter((s) => s.lane === lane).map((s) => (
            <SurfaceCard key={s.id} surface={s}
                         state={states[s.id] ?? { status: "idle" }} now={now}
                         onRun={() => void run(s.id)} />
          ))}
        </div>
      ))}
      {surfaces.length === 0 && <p className="empty">NO SURFACES REGISTERED — SEE BUS-APP.JSON</p>}
    </div>
  );
}

// ————— Navigation history ——————————————————————————————————————————————————————————
// A browser-style back/forward model over `Ref` locations. `navigate` pushes the current spot and
// clears the forward stack (browser semantics); `report` keeps `current` synced with the active zone's
// own sub-selection so a later navigate captures the exact spot.

interface NavState { current: Ref; back: Ref[]; forward: Ref[]; }
type NavAction =
  | { t: "navigate"; ref: Ref }
  | { t: "back" }
  | { t: "forward" }
  | { t: "report"; partial: Partial<Ref> };

function sameRef(a: Ref, b: Ref): boolean {
  return a.zone === b.zone && a.doc === b.doc && a.viz === b.viz && a.dir === b.dir && a.widget === b.widget;
}

function navReducer(s: NavState, a: NavAction): NavState {
  switch (a.t) {
    case "navigate":
      if (sameRef(s.current, a.ref)) return s; // clicking the active spot is a no-op (no dup history)
      return { current: a.ref, back: [...s.back, s.current], forward: [] };
    case "back": {
      if (s.back.length === 0) return s;
      const prev = s.back[s.back.length - 1];
      return { current: prev, back: s.back.slice(0, -1), forward: [s.current, ...s.forward] };
    }
    case "forward": {
      if (s.forward.length === 0) return s;
      const next = s.forward[0];
      return { current: next, back: [...s.back, s.current], forward: s.forward.slice(1) };
    }
    case "report": {
      // only the ACTIVE zone reports its sub-selection; never touches the stacks
      if (a.partial.zone && a.partial.zone !== s.current.zone) return s;
      const merged = { ...s.current, ...a.partial };
      return sameRef(merged, s.current) ? s : { ...s, current: merged };
    }
  }
}

// The theme menu — lives on the baseplate (the low-collision chrome strip). A native <select>
// like the weight-pack loader (the in-window-dropdown pattern; native menus are off the table
// for this Accessory app, and phones render a native picker — better than any custom popover).
// AUTO = un-pin and follow the OS; picking a theme pins it (src/theme.ts persists the choice).
function ThemeMenu() {
  const theme = useTheme();
  const GLYPHS: Record<string, string> = {
    dark: "☾", bright: "☀", paper: "▤", phosphor: "▚", redwatch: "◉",
    fjord: "❆", outrun: "◢", abyss: "≋", dusk: "☽", solar: "✹", mono: "◼",
  };
  const glyph = GLYPHS[theme] ?? "☾";
  return (
    <label className="theme-menu" title="Theme — AUTO follows the OS; picking one pins it">
      <span className="glyph">{glyph}</span>
      <select
        value={storedTheme() ?? "auto"}
        onChange={(e) => (e.target.value === "auto" ? clearTheme() : setTheme(e.target.value as Theme))}
      >
        <option value="auto">AUTO</option>
        {THEMES.map((t) => (
          <option key={t.value} value={t.value}>{t.label}</option>
        ))}
      </select>
    </label>
  );
}

// ————— Shell ——————————————————————————————————————————————————————————————————————————————————

export default function App() {
  const [navState, dispatch] = useReducer(navReducer, { current: { zone: "inbox" }, back: [], forward: [] });
  const [unread, setUnread] = useState(0);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [now, setNow] = useState(new Date());
  const [status, setStatus] = useState("");
  const [version, setVersion] = useState("");
  // scenario-switcher: the available sample packs + the active one ("" = the user's real corpus).
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [pack, setPack] = useState("");

  useEffect(() => {
    void getConfig().then(setConfig);
    void appVersion().then(setVersion);
    void listPacks().then(setPacks).catch(() => {});
    void getActivePack().then((p) => setPack(p || "")).catch(() => {});
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // Swap the active weight pack (from the in-window dropdown): persist it Rust-side, drop the DASH
  // localStorage cache so panels don't paint the prior scenario's data, then set local state (the
  // data zones re-key + refetch).
  const selectPack = useCallback(async (path: string) => {
    await setActivePack(path || null);
    for (const k of Object.keys(localStorage)) {
      if (k.startsWith("dash:")) localStorage.removeItem(k);
    }
    setPack(path);
  }, []);

  // "Load Weight Pack…": a native folder picker (the dialog plugin — a modal, NOT a menu, so it's safe
  // for this Accessory app; a Builder menu is not). Browse to ANY pack dir — a user's own
  // pack lives OUTSIDE the app (set_active_pack accepts any path), so the published app never has to
  // bundle or be aware of personal data. On pick, reuse selectPack to persist + reload in place.
  const loadPackFromDir = useCallback(async () => {
    const dir = await openDialog({ directory: true, multiple: false, title: "Load Weight Pack" });
    if (typeof dir === "string") await selectPack(dir);
  }, [selectPack]);

  // Stabilize the context value + callbacks so the once-a-second clock re-render does NOT churn the
  // nav context — otherwise every consumer (incl. memoized VaultZone) re-renders each tick, reviving
  // the markdown/image "loading flash" (keep rendered subtrees referentially stable). `now` is deliberately NOT in this memo.
  const navigate = useCallback((ref: Ref) => dispatch({ t: "navigate", ref }), []);
  const back = useCallback(() => dispatch({ t: "back" }), []);
  const forward = useCallback(() => dispatch({ t: "forward" }), []);
  const report = useCallback((partial: Partial<Ref>) => dispatch({ t: "report", partial }), []);
  const nav: Nav = useMemo(() => ({
    current: navState.current,
    navigate, back, forward, report,
    canGoBack: navState.back.length > 0,
    canGoForward: navState.forward.length > 0,
  }), [navState, navigate, back, forward, report]);

  const zone = navState.current.zone;

  const refreshProducers = async () => {
    if (!config) return;
    setStatus("RUNNING PRODUCERS…");
    for (const p of config.producers) setStatus((await runProducer(p.id)).toUpperCase());
  };

  return (
    <NavContext.Provider value={nav}>
    <div className="console">
      <header className="masthead">
        <div className="nameplate">
          <span className="mark">⬢</span>
          <h1>WATCHMAN</h1>
          <span className="model-no">WATCHMAN CONSOLE · v{version || "—"}</span>
        </div>
        {isTauri() && (
        <div className={"pack-switch" + (pack ? " active" : "")}
             title="Load a weight pack (a scenario) — a bundled sample, your own folder, or your real data">
          <label>PACK</label>
          <select value={pack} onChange={(e) => void selectPack(e.target.value)}>
            {/* "Real data" (no pack → your real corpus) is a dev-only affordance; the published app
                centers on packs (a bundled demo or your own via Load Weight Pack…) + never surfaces the
                implicit real-corpus toggle. */}
            {!PUBLISHED && <option value="">Real data</option>}
            {packs.map((p) => (
              <option key={p.path} value={p.path}>{p.name}</option>
            ))}
            {pack && !packs.some((p) => p.path === pack) && (
              <option value={pack}>{pack.split("/").filter(Boolean).pop()} (loaded)</option>
            )}
          </select>
          <button className="pack-load" onClick={() => void loadPackFromDir()}
                  title="Load Weight Pack — browse to any folder containing a pack">Load…</button>
        </div>
        )}
        <div className="navhist">
          <button className="navhist-btn" disabled={!nav.canGoBack} onClick={back} title="Back">◀</button>
          <button className="navhist-btn" disabled={!nav.canGoForward} onClick={forward} title="Forward">▶</button>
        </div>
        <nav className="zones">
          <button className={zone === "inbox" ? "active" : ""} onClick={() => navigate({ zone: "inbox" })}>
            INBOX{unread > 0 && <em>{unread}</em>}
          </button>
          <button className={zone === "dash" ? "active" : ""} onClick={() => navigate({ zone: "dash" })}>
            DASH
          </button>
          <button className={zone === "surfaces" ? "active" : ""}
                  onClick={() => navigate({ zone: "surfaces" })}>
            SURFACES
          </button>
          <button className={zone === "viz" ? "active" : ""} onClick={() => navigate({ zone: "viz" })}>
            VIZ
          </button>
          <button className={zone === "vault" ? "active" : ""} onClick={() => navigate({ zone: "vault" })}>
            VAULT
          </button>
        </nav>
        <div className="spacer" />
        <button className="primary" onClick={() => void refreshProducers()}>⟳ RUN PRODUCERS</button>
        <span className="clock">{clock(now)}</span>
      </header>

      <main>
        <ErrorBoundary resetKey={JSON.stringify(navState.current)}>
          {zone === "inbox" && <Inbox onUnread={setUnread} />}
          {/* pack swap: Dash refetches IN PLACE (reloadKey → forceTick) so the active tab is kept;
              surfaces remount (no tab to preserve there). */}
          {zone === "dash" && <Dash reloadKey={pack} />}
          {zone === "surfaces" && <Surfaces key={pack || "real"} />}
          {zone === "viz" && <VizZone target={navState.current.viz} />}
          {zone === "vault" && <VaultZone target={navState.current.doc} />}
        </ErrorBoundary>
      </main>

      <footer className="baseplate">
        <span className="led-ok" /> <span>BUS.DB ONLINE</span>
        <span className="path">{config?.bus_source ?? config?.db_path ?? ""}</span>
        <div className="spacer" />
        <span className="status">{status}</span>
        <ThemeMenu />
        {/* web-only: the native console notifies through the OS; the bell is the BROWSER's way
            to arm this device (docs/WEB-CONSOLE.md → Push notifications) */}
        {!isTauri() && <PushBell />}
        <span className="build">BUS-APP v{version || "—"}</span>
        {/* self-update: published native builds only — the dev daily-driver's updater config points
            nowhere (and debug builds don't register the plugin), and the served browser console
            updates by redeploying the server, so neither ever shows the affordance */}
        {PUBLISHED && isTauri() && <UpdatePill />}
      </footer>
    </div>
    </NavContext.Provider>
  );
}
