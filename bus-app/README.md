# Watchman (bus-app)

Menu-bar-resident desktop app for the **harness message bus** — the human delivery layer for
standing agents (Phase 2). Tray badge + native notifications + an inbox window
over `~/.local/state/harness/bus.db`. Producer contract: [`../docs/BUS.md`](../docs/BUS.md).

**Stack**: Tauri v2 shell (Rust: rusqlite + tray + notification/autostart/single-instance
plugins) + React/Vite/TS inbox. ~12MB installed; zero model anywhere in the loop.

## How it works

- A 30s poller reads the bus db (rusqlite, `busy_timeout=5000`, WAL — concurrent with the Python
  producers by design). For every **unread event missing the `"desktop"` marker** it posts a
  native notification under the app's own identity, then appends `"desktop"` to `delivered_via`
  (the db is the single source of delivery truth — reinstalls can't desync; a future ntfy
  transport uses the identical mechanism with its own marker).
- **Tray**: unread count as the badge title; menu = top-5 recent unread (click → open + select)
  · Open Inbox · Refresh now · Quit. Window close = hide (stay resident); Quit is the real exit.
- **Inbox window**: filterable list (lanes/kinds are *data* — `SELECT DISTINCT`, zero hardcoded
  domains), payload drill-down, mark-read, Refresh now.
- **Refresh now** re-runs the producers registered in `~/.config/harness/bus-app.json`
  (created with a default pointing at `hn finance pulse` on first launch). Producers are config,
  not code — adding the career watchman is a JSON edit. Read-rich/execute-gated: a refresh is a
  re-run of a read-only command, never an execution.

## Dev

```bash
cd bus-app
npm install
npm run tauri dev # needs Rust (rustup) + Xcode CLT
```

Dev-mode notifications post under **the launching terminal's identity** (a dev binary isn't a
bundled .app — macOS attributes to the parent process; confirmed live 2026-06-12: Terminal.app
needed whitelisting). Don't judge notification identity/styling until the installed bundle
(Phase 3), which posts as "Watchman" with its own authorization prompt.

> **Crate pin**: `time` is lock-pinned to 0.3.47 — 0.3.48 breaks trait coherence in
> `cookie`/`tauri-utils` (E0119). Re-test before unpinning.

## Install (Phase 3)

```bash
npm run tauri build # → src-tauri/target/release/bundle/macos/Watchman.app
```

Copy to `/Applications`, launch, then the **one-time macOS step**:

> **System Settings → Notifications → Watchman → Allow notifications, style: ⚠️ Alerts**
> (alerts persist on screen until dismissed — the fix for transient-banner blindness; either way
> the tray badge + unread inbox survive sleep by construction).

Autostart-at-login is enabled from the app (tauri-plugin-autostart, LaunchAgent mode).

## Config

`~/.config/harness/bus-app.json`:

```json
{
  "db_path": null,
  "producers": [
    { "id": "finance.pulse", "label": "Finance pulse", "cmd": "uv",
      "args": ["run", "hn", "finance", "pulse", "--notify", "--json"],
      "cwd": "." }
  ]
}
```

`db_path` (or the `HARNESS_BUS_DB` env var) relocates the bus; default
`~/.local/state/harness/bus.db` matches the Python side.

### Remote bus — `bus_url` mode (0.5.0)

A watchman on any device can read a served bus (`hn bus serve` on the always-on node —
docs/BUS.md "Serving the bus over HTTP") instead of a local file:

```json
{
  "bus_url": "http://my-mini.mesh.internal:8787",
  "bus_token": "<the server's ~/.config/harness/bus-token value>"
}
```

Absent/blank `bus_url` = local rusqlite, unchanged. Prefer the MagicDNS name over the tailnet IP
(survives node re-enrollment). The remote console runs the full loop — Inbox, badge, filters,
acks, its own native notifications under a per-device `desktop:{hostname}` marker; acks are
global, so reading on one device clears badges everywhere. A bundled demo pack active always
trumps `bus_url` (the demo seal renders sealed local state, never the mesh). A dead mesh degrades
to a skipped poll tick / an inline Inbox error (4s/10s timeouts), never a hung UI.
