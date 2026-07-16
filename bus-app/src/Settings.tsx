// Settings — the Obsidian-shaped console settings modal: one ⚙ home for the
// controls that used to sprawl across the chrome (theme menu, pack switcher) plus the
// administrative config that used to require hand-editing bus-app.json (the remote-setup pain
// the docs used to warn about).
// Structure: centered modal over a backdrop, grouped left tab rail, right pane of
// label/description/control rows. Writes are field-allowlisted native-only commands — the served
// console renders everything read-only (the door WRITE_GATEs the mutations server-side too).
import { useCallback, useEffect, useState } from "react";

import { load as yamlLoad } from "js-yaml";

import { getConfig, getUserOverlay, setBusConfig, testBusConnection } from "./api";
import { isTauri } from "./transport";
import { clearTheme, setTheme, storedTheme, THEMES, useTheme, type Theme } from "./theme";
import type { PackInfo } from "./api";
import type { AppConfig } from "./types";

type Tab = string; // "general" | "connection" | "packs" | "sources" | a personal lane ("lane:finance")

const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "connection", label: "Connection" },
  { id: "packs", label: "Weight packs" },
  { id: "sources", label: "Producers & surfaces" },
];

function Row({ label, desc, children }: { label: string; desc?: string; children?: React.ReactNode }) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <div className="settings-row-label">{label}</div>
        {desc && <div className="settings-row-desc">{desc}</div>}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

// The theme picker (moved here from the baseplate ThemeMenu — the de-clutter absorb).
// AUTO = un-pin and follow the OS; picking a theme pins it (src/theme.ts persists the choice).
function ThemeControl() {
  const theme = useTheme();
  void theme; // subscribe so the select re-renders on external theme changes
  return (
    <select
      value={storedTheme() ?? "auto"}
      onChange={(e) => (e.target.value === "auto" ? clearTheme() : setTheme(e.target.value as Theme))}
    >
      <option value="auto">AUTO (follow OS)</option>
      {THEMES.map((t) => (
        <option key={t.value} value={t.value}>{t.label}</option>
      ))}
    </select>
  );
}

export default function Settings({
  open, onClose, version, packs, pack, onSelectPack, onLoadPackDir, onConfigChanged,
}: {
  open: boolean;
  onClose: () => void;
  version: string;
  packs: PackInfo[];
  pack: string;
  onSelectPack: (path: string) => void | Promise<void>;
  onLoadPackDir: () => void | Promise<void>;
  onConfigChanged: () => void;
}) {
  const [tab, setTab] = useState<Tab>("general");
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const native = isTauri();

  // Connection editor state (native only)
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [probe, setProbe] = useState("");
  const [busy, setBusy] = useState(false);

  const [overlay, setOverlay] = useState<Record<string, unknown> | null>(null);
  const [overlaySource, setOverlaySource] = useState("");

  const refresh = useCallback(() => {
    void getConfig().then((c) => {
      setCfg(c);
      setUrl(c.bus_url ?? "");
    }).catch(() => setCfg(null));
    // The Personal tabs: the RESOLVED user overlay as raw text — parsed HERE
    // (one js-yaml parser client-side; native Rust + the served door both ship text only).
    void getUserOverlay().then((o) => {
      setOverlaySource(o.source);
      const parsed = o.text ? yamlLoad(o.text) : null;
      setOverlay(parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null);
    }).catch(() => setOverlay(null));
  }, []);

  useEffect(() => {
    if (open) { setProbe(""); setToken(""); refresh(); }
  }, [open, refresh]);

  if (!open) return null;

  const test = async () => {
    setBusy(true); setProbe("");
    try {
      setProbe(await testBusConnection(url, token));
    } catch (e) {
      setProbe(`failed: ${String(e)}`);
    } finally { setBusy(false); }
  };

  const apply = async (nextUrl: string | null) => {
    setBusy(true); setProbe("");
    try {
      const c = await setBusConfig(nextUrl, token || null);
      setCfg(c); setUrl(c.bus_url ?? ""); setToken("");
      setProbe(nextUrl ? "saved — remote bus configured (demo pack cleared)" : "saved — back to local mode");
      onConfigChanged();
    } catch (e) {
      setProbe(`failed: ${String(e)}`);
    } finally { setBusy(false); }
  };

  const mode = cfg?.mode ?? (native ? "local" : "served");

  return (
    <div className="doc-popup-backdrop settings-backdrop" onClick={onClose}>
      <div className="settings-modal" role="dialog" aria-label="Settings" onClick={(e) => e.stopPropagation()}>
        <div className="settings-rail">
          <div className="settings-rail-group">Console</div>
          {TABS.map((t) => (
            <button key={t.id} className={"settings-tab" + (tab === t.id ? " active" : "")}
                    onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
          {overlay && Object.keys(overlay).length > 0 && (
            <>
              <div className="settings-rail-group">Personal</div>
              {Object.keys(overlay).map((lane) => (
                <button key={lane} className={"settings-tab" + (tab === `lane:${lane}` ? " active" : "")}
                        onClick={() => setTab(`lane:${lane}`)}>
                  {lane}
                </button>
              ))}
            </>
          )}
        </div>

        <div className="settings-pane">
          <button className="doc-popup-close settings-close" onClick={onClose} title="Close">✕</button>

          {tab === "general" && (
            <>
              <h2>General</h2>
              <Row label="Version" desc="This console build.">
                <span className="settings-value">BUS-APP v{version || "—"}</span>
              </Row>
              <Row label="Theme" desc="AUTO follows the OS; picking one pins it (per device).">
                <ThemeControl />
              </Row>
              <Row label="Updates" desc="Published native builds self-update — the pill appears in the footer when one is available. The served console updates when the host redeploys.">
                <span className="settings-value dim">automatic</span>
              </Row>
            </>
          )}

          {tab === "connection" && (
            <>
              <h2>Connection</h2>
              <Row label="Mode" desc="Where this console reads its bus.">
                <span className={`settings-badge mode-${mode}`}>{mode.toUpperCase()}</span>
              </Row>
              <Row label="Bus source" desc="What the Inbox is actually reading right now.">
                <span className="settings-value">{cfg?.bus_source ?? "—"}</span>
              </Row>
              {native ? (
                <>
                  <h3>Online bus</h3>
                  <Row label="Bus URL" desc="An `hn bus serve` endpoint — prefer the MagicDNS name over a raw IP. Leave empty and Apply to return to local mode.">
                    <input type="text" value={url} placeholder="http://bus-host.tailnet.example:8787"
                           onChange={(e) => setUrl(e.target.value)} />
                  </Row>
                  <Row label="Bearer token" desc={cfg?.bus_token_set
                    ? "A token is stored. Leave blank to keep it (Test requires re-entering it)."
                    : "The server's bus-token value. Required for a remote bus."}>
                    <input type="password" value={token} placeholder={cfg?.bus_token_set ? "•••••• (stored)" : "token"}
                           onChange={(e) => setToken(e.target.value)} />
                  </Row>
                  <Row label="" desc="Connecting clears any active demo pack — a seeded pack silently overrides the remote bus otherwise.">
                    <span className="settings-actions">
                      <button disabled={busy || !url || !token} onClick={() => void test()}>Test</button>
                      <button disabled={busy || !url} onClick={() => void apply(url)}>Connect</button>
                      <button disabled={busy || mode !== "remote"} onClick={() => void apply(null)}>Disconnect</button>
                    </span>
                  </Row>
                  {probe && <div className={"settings-probe" + (probe.startsWith("failed") ? " bad" : " ok")}>{probe}</div>}
                </>
              ) : (
                <Row label="Online bus" desc="Connection settings are managed on the console that owns them — a served browser can't rewrite the host's bus wiring.">
                  <span className="settings-value dim">read-only here</span>
                </Row>
              )}
              <h3>Paths</h3>
              <Row label="Tracker (corpus)"><span className="settings-value">{cfg?.tracker_path ?? "—"}</span></Row>
              <Row label="Bus database"><span className="settings-value">{cfg?.db_path ?? "—"}</span></Row>
              {cfg?.config_path && (
                <Row label="Config file" desc="The install config this panel edits (bus-app.json).">
                  <span className="settings-value">{cfg.config_path}</span>
                </Row>
              )}
            </>
          )}

          {tab === "packs" && (
            <>
              <h2>Weight packs</h2>
              <Row label="Active pack" desc="A pack is a scenario bundle — a demo persona or your own folder. None = your real corpus.">
                <span className="settings-value">{pack ? pack.split("/").filter(Boolean).pop() : "none (real corpus)"}</span>
              </Row>
              {native ? (
                <>
                  <Row label="Switch pack">
                    <select value={pack} onChange={(e) => void onSelectPack(e.target.value)}>
                      <option value="">Real data</option>
                      {packs.map((p) => (
                        <option key={p.path} value={p.path}>{p.name}</option>
                      ))}
                      {pack && !packs.some((p) => p.path === pack) && (
                        <option value={pack}>{pack.split("/").filter(Boolean).pop()} (loaded)</option>
                      )}
                    </select>
                  </Row>
                  <Row label="Load from folder" desc="Browse to any folder containing a pack — personal packs live outside the app.">
                    <button onClick={() => void onLoadPackDir()}>Load…</button>
                  </Row>
                </>
              ) : (
                <Row label="Packs" desc="The served console renders the host's real corpus — packs are the native console's affordance.">
                  <span className="settings-value dim">not available here</span>
                </Row>
              )}
            </>
          )}

          {tab.startsWith("lane:") && overlay && (() => {
            const lane = tab.slice(5);
            const block = overlay[lane];
            const gs = block && typeof block === "object"
              ? (block as Record<string, unknown>).global_settings ?? {} : {};
            const rows: Array<[string, string]> = [];
            const walk = (node: unknown, prefix: string) => {
              if (node && typeof node === "object" && !Array.isArray(node)) {
                for (const [k, v] of Object.entries(node as Record<string, unknown>)) walk(v, prefix ? `${prefix}.${k}` : k);
              } else {
                const shown = node == null || (Array.isArray(node) && node.length === 0) ? "unset"
                  : Array.isArray(node) ? node.join(", ") : String(node);
                rows.push([prefix, shown]);
              }
            };
            walk(gs, "");
            return (
              <>
                <h2>{lane} · personal settings</h2>
                {rows.map(([k, v]) => (
                  <Row key={k} label={k}>
                    <span className={"settings-value" + (v === "unset" ? " dim" : "")}>{v}</span>
                  </Row>
                ))}
                <div className="settings-row-desc" style={{ marginTop: 10 }}>
                  From the {overlaySource} — read-only here; the file is the interface:
                  edit <code>config/harness.yaml</code> in your vault and reopen.
                </div>
              </>
            );
          })()}

          {tab === "sources" && (
            <>
              <h2>Producers & surfaces</h2>
              <Row label="Producers" desc="Headless publishers the RUN PRODUCERS button fires." />
              <table className="settings-table">
                <tbody>
                  {(cfg?.producers ?? []).map((p) => (
                    <tr key={p.id}><td>{p.label}</td><td className="dim">{p.id}</td></tr>
                  ))}
                </tbody>
              </table>
              <Row label="Surfaces" desc="On-demand data pulls on the SURFACES tab." />
              <table className="settings-table">
                <tbody>
                  {(cfg?.surfaces ?? []).map((s) => (
                    <tr key={s.id}><td>{s.label}</td><td className="dim">{s.lane} · {s.id}</td></tr>
                  ))}
                </tbody>
              </table>
              <Row label="Live viz" desc="The VIZ tab's live entries." />
              <table className="settings-table">
                <tbody>
                  {(cfg?.live_viz ?? []).map((v) => (
                    <tr key={v.id}><td>{v.label}</td><td className="dim">{v.lane} · {v.id}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="settings-row-desc">Rosters are read-only here — they live in the config file (Connection → Paths).</div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
