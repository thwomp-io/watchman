import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { inShell, shellIpc } from "./transport";
import "./App.css";
import Inbox from "./Inbox";
import Chat from "./Chat";
import {
  appVersion, getActivePack, getConfig, listPacks, listSurfaces, type PackInfo, pickPackFolder, runProducer,
  runSurface, setActivePack,
} from "./api";
import JsonView from "./JsonView";
import VizZone from "./viz/VizZone";
import VaultZone from "./VaultZone";
import Dash from "./Dash";
import ErrorBoundary from "./ErrorBoundary";
import PushBell from "./PushBell";
import Settings from "./Settings";
import UpdatePill from "./UpdatePill";
import { NavContext, type Nav, type Ref } from "./nav";
import { clearTheme, setTheme, storedTheme, THEMES, useTheme, type Theme } from "./theme";
import { PUBLISHED } from "./published";
import type { AppConfig, Surface, SurfaceState } from "./types";

function clock(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
  return a.zone === b.zone && a.doc === b.doc && a.viz === b.viz && a.dir === b.dir && a.widget === b.widget
    && a.dash === b.dash && a.follows === b.follows && a.pick === b.pick;
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

// The baseplate theme menu — RESTORED: an earlier chrome de-clutter pass absorbed it
// into Settings; it is kept here for quick switching. Lives in BOTH places now — this menu and
// the Settings General picker drive the same store (src/theme.ts), so they stay in sync for free via useTheme().
// In-window dropdown (native menus off the table for this Accessory app; phones get the native picker).
// AUTO = un-pin and follow the OS; picking a theme pins it.
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
  const [settingsOpen, setSettingsOpen] = useState(false);
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

  // "Load Weight Pack…" (desktop app only): the shell's native folder picker. A personal pack lives
  // outside the app, so the published app never bundles or knows about personal data. On pick, reuse
  // selectPack to persist + reload in place.
  const loadPackFromDir = useCallback(async () => {
    const dir = await pickPackFolder();
    if (dir) await selectPack(dir);
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
        <button className="settings-open" title="Settings" onClick={() => setSettingsOpen(true)}>⚙ SETTINGS</button>
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
          <button className={zone === "chat" ? "active" : ""} onClick={() => navigate({ zone: "chat" })}>
            CHAT
          </button>
        </nav>
        {/* `hn console demo`: the sample personas, switchable from the header (there is no real corpus) */}
        {config?.demo && packs.length > 0 && (
          <label className="demo-pack" title="Sample persona — fictional data">
            <span>DEMO</span>
            <select value={pack} onChange={(e) => void selectPack(e.target.value)}>
              {packs.map((p) => (
                <option key={p.path} value={p.path}>{p.name}</option>
              ))}
            </select>
          </label>
        )}
        <div className="spacer" />
        {(config?.writes ?? []).includes("run_producer") && (
          <button className="primary" onClick={() => void refreshProducers()}>⟳ RUN PRODUCERS</button>
        )}
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
          {zone === "chat" && <Chat />}
        </ErrorBoundary>
      </main>

      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} version={version}
        packs={packs} pack={pack} onSelectPack={selectPack} onLoadPackDir={loadPackFromDir}
        onConfigChanged={() => {
          void getConfig().then(setConfig);
          void getActivePack().then((p) => selectPack(p || "")).catch(() => {});
        }} />

      <footer className="baseplate">
        <span className="led-ok" /> <span>BUS.DB ONLINE</span>
        <span className="path">{config?.bus_source ?? config?.db_path ?? ""}</span>
        <div className="spacer" />
        <span className="status">{status}</span>
        <ThemeMenu />
        {/* web-only: the native console notifies through the OS; the bell is the BROWSER's way
            to arm this device (docs/WEB-CONSOLE.md → Push notifications) */}
        {/* the desktop app delivers native notifications; web push is for browsers and phones */}
        {!inShell() && <PushBell />}
        <span className="build">BUS-APP v{version || "—"}</span>
        {/* self-update: published native builds only — the dev daily-driver's updater config points
            nowhere (and debug builds don't register the plugin), and the served browser console
            updates by redeploying the server, so neither ever shows the affordance */}
        {PUBLISHED && shellIpc() && <UpdatePill />}
      </footer>
    </div>
    </NavContext.Provider>
  );
}
