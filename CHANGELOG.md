# Changelog

All notable changes to Watchman, following [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [0.9.0] - 2026-07-16

This release adds a Settings panel, a single user-config overlay (`harness.yaml`), a whole-book
intraday day-G/L surface, and a macOS installer built by CI. Documentation is restructured
Settings-first. See [docs/CLI.md](docs/CLI.md) for the CLI/from-source reference.

### Added
- **⚙ Settings** — an in-app settings panel (grouped rail: General, Connection, Weight packs,
  Producers). Connecting to a served bus is now a form with a **Test** probe; applying a remote
  connection auto-clears an active demo pack, removing the silent-override footgun that previously
  required a hand-edited config file. The pack switcher and theme menu moved into the panel.
- **The user overlay** (`harness.yaml`) — one file for every user-specific setting, resolved
  pack > corpus > packaged template. Per-lane `global_settings` (account display names, tracked
  fund identity, home city/airports, resume render identity); `hn config show` prints the
  resolved state; the Settings panel renders each configured lane as a read-only Personal tab.
  `fund-holdings` reads its fund from the overlay — no flags needed once configured.
- **`hn finance daygl`** — whole-book intraday day gain/loss in one number, decomposed by
  valuation basis: live-quoted positions exact (per account), non-intraday funds estimated via
  the configured proxy basket (coverage stated), static balances flat by definition. Drives a
  six-tile decomposition row on the Finance dashboard. Stat tiles gained sign-aware rendering.
- **macOS installer** — CI builds and attaches a `.dmg` to each release alongside the Windows
  and Linux bundles (unsigned; Gatekeeper right-click-open steps in the README).
- **Config templates are neutral by construction** — the packaged travel weights and news-feed
  roster are now neutral templates with a corpus-resident resolution tier (matching the portfolio
  seed); default dashboards use neutral market symbols. A user's real preferences live in their
  corpus or overlay, never in the engine tree.
- **`console-shot --click`** accepts comma-separated selectors clicked in order (open a modal,
  then a tab — screenshot any nested UI state).

### Changed
- **Documentation is Settings-first**: the README leads with the Quickstart + Settings panel;
  CLI and from-source detail moved to `docs/CLI.md`; configuration prose funnels through the
  panel with hand-edit instructions kept as a labeled fallback. Fresh mobile screenshots and a
  Settings tour join the demo media.

## [0.8.0] - 2026-07-14

This release integrates the [beads](https://github.com/gastownhall/beads)
issue tracker as a first-class console surface — a Backlog tab, an interactive dependency graph,
and per-issue ticket pages — plus user-customizable dashboards, a live GTC-order trap map, and a
console-wide quick-look popup. See [docs/BEADS.md](docs/BEADS.md) for the beads walkthrough.

### Added
- **Beads integration** (`hn beads board`): a read-only board over a beads project's
  `.beads/issues.jsonl` export — open/in-progress/shipped counts, the presence board (which agent
  is on what), an honest ready queue (blocking dependencies + defer dates respected), and the
  export's age surfaced so the board never performs more freshness than its file has. Drives the
  new **Ops ▸ Backlog** tab; every sample persona ships a fictional backlog so it demos
  out-of-the-box.
- **The Beads board viz** — the backlog as an interactive dependency graph: epics with their
  children laid out as an org chart that wraps to the window, standalone work on a shelf beneath,
  blocking dependencies overlaid as dashed edges. Blocks are status-colored with a P1 accent;
  hovering shows a metadata card, hovering a dashed edge explains the relationship
  (blocker/blocked, side by side), and clicking a tile quick-looks the full ticket.
- **Ticket pages** (`hn beads tickets`): every issue renders to a markdown ticket in the
  vault — status chips, details table, description, linked issues as wikilinks (tickets
  cross-navigate), the comment thread, and the close reason as the resolution. Board rows link
  straight into them.
- **Dashboard Studio** — unlock any dashboard and rearrange it: drag/resize widgets on an explicit
  grid, with collision-safe shuffling (widgets never overwrite each other), automatic config
  backups, and a two-click return-to-default. Locked is always the default state.
- **Trap map** (`hn finance trap-map`): every resting GTC buy order drawn as a rung on its
  symbol's price ladder — live price, distance-to-fill, committed dollars, and support shelves
  from recent price history. On the Finance dashboards and the VIZ gallery.
- **Vest timeline on the VIZ gallery** — the sell-planning calendar (vest events, wash-sale
  windows, clean harvest windows) joins the big panel as a live entry.
- **Every visual is browsable on the VIZ tab** — live command-backed entries (trap map, vest
  timeline, beads board, concentration) sit alongside vault-discovered diagrams, each with a
  refresh control.
- **DocPopup quick-look** — a console-wide primitive: any vault doc opens in a modal overlay
  without leaving the current zone, wikilinks navigate within the popup, and "open in VAULT"
  hands off to the full browser with back-button support.
- **`--section` contract pluck** on `beads board` and `finance unwind` — emit one top-level
  section of a JSON contract (the mechanism behind the live viz entries; useful standalone).

### Changed
- Sample personas refreshed to exercise the full console: each ships a fictional beads backlog
  (with a date anchor so the demo stays current forever), a Backlog dashboard, and richer resting
  GTC ladders for the trap map.

## [0.7.1] - 2026-07-08

### Fixed
- **Windows: finance widgets no longer crash on special characters.** Piped spawns on Windows
  hand the engine a legacy-codepage stdio (cp1252), so any widget whose JSON carried a
  non-cp1252 glyph (the `≈` in estimated print dates, the `→` in allocation labels) failed with
  an encoding error — on a fresh Windows install this took out the Day Moves, Upcoming Prints,
  and Allocation panels. The CLI now forces UTF-8 stdio at entry and the console sets UTF-8 mode
  on every spawn, with a regression test pinning both.

## [0.7.0] - 2026-07-07

Push to your phone, a theme system, and wire integrity. The console now reaches you as native
notifications on a phone — self-hosted end to end — and the finance wire gets confirmed earnings
dates and analyst-consensus tracking.

### Added
- **Web push to the phone** — bus events arrive as native banners on an installed PWA
  (iOS/Android), account-free and self-hosted: the Web Push protocol end-to-end (vendor relays see
  ciphertext only). Hard severity gate: only `alert`/`warn` push — the info wire and filings are a
  skim surface by design and never buzz a pocket. A footer bell arms each device (with a TEST
  button to verify end to end); `hn bus push-keys` shows the public key + device inventory. VAPID
  keys auto-generate on first use; dead subscriptions self-prune.
- **Direct TLS on the served console** — `hn bus serve --tls-cert/--tls-key` terminates HTTPS in
  the server itself (no reverse proxy), giving the secure context web push requires while the
  plain port stays available for LAN satellites. Ops recipe in `docs/WEB-CONSOLE.md`.
- **Confirmed earnings dates** — days-to-print now prefers announcement-confirmed dates
  (`= YYYY-MM-DD (confirmed)`) over filing-cadence estimates (`≈ …`), and print-soon flags key off
  the better date. Cadence estimates can miss by weeks; the label tells you which one you're
  reading.
- **The ratings wire** — each held stock's analyst consensus (mean price target + buy/hold/sell
  mix) is diffed daily; material moves surface as `[RATING]` items on the wire and the Inbox. A
  price-target-cut day flags itself instead of hiding in generic headlines.
- **Theme system** — 11 console themes behind a footer menu, including a light "daylight cockpit"
  variant; the dark instrument panel remains the default, and visualization palettes follow the
  active theme.
- **In-app updater (scaffolding)** — the desktop console carries the update plumbing (release
  builds only; signed artifacts via CI). Update checks activate once a signed release is
  published.
- **Illustrated food research (travel)** — restaurant scans pick up thumbnails for free, an
  opt-in keyless pass fetches each place's own hero image, and a finalists verb pulls full photo
  galleries for the shortlist.
- A real favicon — the radar mark, on every tab and bookmark.

### Fixed
- The tray badge counts **urgent** unread only (alert/warn) — a busy catalyst wire no longer
  inflates it into the hundreds.
- iOS push: Apple's push service rejects placeholder VAPID contact claims — the abuse contact now
  resolves from config, and first-delivery is verified on device.
- Footer layout no longer pushes the push bell and theme menu off narrow phone widths.

## [0.6.0] - 2026-07-04

The web console. The same console the desktop app embeds, served over HTTP to any browser on your own
network — laptop, second desktop, or phone.

### Added
- **The web console** — `hn bus serve --console --ui <dist>` serves the console UI from the bus server:
  one server, one bearer token, one bind. The page prompts for the token on first visit (or takes
  `?token=` and cleans the URL); a wrong token re-prompts instead of rendering a broken console. See
  `docs/WEB-CONSOLE.md`.
- **Phones + PWA** — a web-app manifest and standalone ergonomics (safe-area insets, bottom zone tabs):
  Add to Home Screen installs the console like a native app.
- **Variant mounts** — `--ui name=DIR` repeats to serve multiple console builds side by side at
  `/ui/<name>/` (A/B a change or a phone-tuned build without touching the default).
- **The container serves the console** — the published image now carries the built UI at `/app/ui`;
  one `docker run` serves the bus + console from a mounted corpus. New `docs/DOCKER.md` covers volumes,
  the token, version pinning, and running the standing agents from the image.

### Changed
- **Served-console performance** — request handling moved to a worker pool with in-flight spawn dedupe
  and a short server-side cache: a warm dashboard renders in milliseconds; a cold one in under a second.

## [0.5.0] - 2026-07-02

Multi-device. One always-on machine holds the authoritative bus; every other device is a client.

### Added
- **The bus over HTTP** — `hn bus serve` exposes the notification bus on a token-authed API
  (`/api/bus/*`); the token auto-generates to a `0600` file on first run, and `/health` stays open for
  liveness probes. Publish/ack semantics — including idempotent dedup — are identical to the local CLI.
- **Remote mode for the desktop console** — two config keys (`bus_url` + `bus_token`) point a console at
  a served bus instead of the local file. Each device notifies independently; read-state is shared, so
  acking anywhere clears badges everywhere. The footer shows which bus a console is reading.
- **Backlog guard** — a device's first connection to a busy bus summarizes the catch-up in one toast
  instead of a notification storm.

### Fixed
- A configured remote bus with a missing token is an actionable error, never a silent fall-back to an
  empty local bus.

## [0.4.0] - 2026-07-02

The self-contained console. Installers now carry the engine — download, install, and the demo
personas run live with no repository clone. [uv](https://docs.astral.sh/uv/) is the one prerequisite.

### Added
- **The engine ships inside the app** — each installer bundles the engine project; the console runs
  it via uv with an explicitly pinned project and a user-writable environment. First launch prepares
  the environment in the background (about a minute, one time).
- **Demo mode is fully sealed** — while a bundled sample persona is active, every surface (widgets,
  producers, the vault browser, visualizations) renders only that pack. A lane the pack doesn't
  cover reads empty; nothing ever falls back to other data on the machine. Packs you load yourself
  keep blend semantics.
- **Actionable setup errors** — if uv is missing, widgets say exactly that (with the install link)
  instead of a raw spawn error.

### Changed
- **Linux ships the `.deb` only** — the AppImage target is discontinued.
- A development checkout is now only preferred by dev builds; installed (release) builds always run
  their own bundled engine, so an install behaves identically on every machine.

### Fixed
- **The demo seal extends to the notification bus** — while a sample persona is active, the inbox
  and agent-status surfaces render a clean standby state rather than any real bus activity on the
  machine.
- **The settings registry is crash-safe** — saves are atomic (temp-file-then-rename), and an
  unreadable registry is preserved as `.json.bak` instead of being silently replaced.

## [0.3.2] - 2026-07-02

### Fixed
- **The engine now runs on Windows** — added `tzdata` (Python's `zoneinfo` has no system timezone
  database on Windows). If you hit `ZoneInfoNotFoundError`: `git pull` and `uv sync`.
- **Windows installer branding** — the wizard art now renders the app icon.

### Added
- **Windows CI smoke** — every push proves the engine imports and runs a keyless sample-pack
  command on `windows-latest`.

## [0.3.1] - 2026-07-01

Fresh-install polish from the first Windows field test.

### Added
- **Sample personas now ship inside the app** — the PACK dropdown works on a fresh install with no
  cloned repository (packs resolve from the app bundle's resources; a repo checkout still wins for
  development).
- **Branded Windows installers** — the NSIS and MSI wizards now carry the console's noir theme.

### Known limitations
- Live data widgets still require the engine: [uv](https://docs.astral.sh/uv/) plus a repository
  clone (`uv sync`). A guided first-run setup and a fully bundled engine are on the roadmap.

## [0.3.0] - 2026-07-01

Cross-platform: Windows and Linux, first-class. A fresh clone — or a prebuilt installer — now runs the
console and the demo personas on any desktop OS.

### Added
- **Prebuilt installers on Releases**: Windows (`.exe`/`.msi`) and Linux (`.deb`/`.AppImage`), built,
  bundled, and attached by CI on every release tag.
- **README platform-support matrix** with a Windows (PowerShell) quickstart and Linux system-dependency
  notes for building the console from source.

### Fixed
- **Windows home-directory resolution** — the app read only the unix `HOME` env (unset on Windows), so
  every home-derived path (config, bus db, sample packs) silently broke; it now falls back to
  `USERPROFILE`.
- **Windows PATH handling for spawned tools** — the engine-spawn PATH helper split on `:`, which shreds
  Windows drive-lettered entries; it now uses the platform's list separator and Windows-appropriate
  per-user tool dirs (uv's install dir, cargo's bin).

### Changed
- **macOS builds from a fresh clone no longer require a signing identity** — the config no longer pins
  one; Tauri falls back to ad-hoc signing for local builds.
- **Per-file release history** — releases now land as regular commits, so `git log <file>` and diffs
  between releases are browsable.

## [0.2.0] - 2026-07-01

A curated batch of finance-lane depth, a broad news wire, and a second bundled skill.

### Added
- **finance `correlate`** — a daily-return correlation matrix across your holdings, each name's **beta to
  a factor basket**, and the biggest **divergence days** — the hard-data answer to "is this actually a
  diversifier, or more of the same bet?" (measure a diversification claim instead of asserting it).
- **finance `market`** — a bird's-eye market-regime read (index breadth, the 11 SPDR sectors, semis,
  mega-cap dispersion) in one snapshot; drives the Market dashboard.
- **finance `fed`** — the latest FOMC decision (statement, target-rate range, vote) from the Federal
  Reserve's own feed, keyless.
- **Broad-market news wire** — a `feeds.yaml`-driven wire (mainstream markets + geopolitics + tunable
  thesis-topic searches) surfaced in a master-detail **News reader** tab with filter chips, relative
  times, and keyboard nav; a single-name catalyst layer publishes "why is it moving" context to the inbox.
- **Triage inbox** — notifications grouped into severity bands (act / watch / catalyst-wire / filings)
  with an always-on status strip, so an `info` skim-stream never drowns the actionable signals.
- **career `render`** — résumé rendering to a PDF *or* a design-matched Word `.docx`.
- **console-operator skill** (`skills/console-operator/`) — teaches an agent to operate the console and
  drive the lanes; the operate-the-tool companion to `corpus-operator`'s build-the-corpus.

### Changed
- **Core finance dashboard** — a 6-column market-context layout (net worth · fund proxy · SPY · RSP ·
  RSP−SPY · Mag7).
- Grouped, subtabbed dashboards with in-place viz expansion and app-wide back/forward navigation.
- Clearer, generic command naming (`fund-proxy`, `unwind`).

### Fixed
- A machine-enforced version-sync gate; empty-data guards on the viz widgets; sample-pack completeness
  guards so a persona can never silently drop a tab or a data source.
- Single-knob state isolation (`HARNESS_STATE_DIR`) so a sandboxed instance's bus **and** run-log never
  touch the real ones; a never-configured instance's watch-floor now reads a calm "standing by" rather
  than a false "missed" alarm.

## [0.1.0] - 2026-06-20

Initial public release.

### Added
- **`hn` CLI** with three read-only lanes:
  - **finance** — quotes, positions, net worth, SEC EDGAR fundamentals, multiples, news, research, watch,
    and a values screen. Observation only; no trading.
  - **career** — keyless openings scans (Greenhouse/Ashby) with posted comp, company profiles, and viz.
  - **travel** — flight ranking, hotels, events, traffic/ferries, keyless weather/air/quake senses, and
    destination viz.
- **Watchman console** — a resident desktop app (Tauri): domain dashboards that self-refresh from the CLI's
  `--json` verbs, a notification bus for standing agents, and an interactive D3 viz layer.
- **Weight packs** — portable, swappable per-persona data bundles; loading one re-renders the whole console.
  Ships bundled fictional sample personas so a fresh clone runs out of the box.
- **Shared D3 viz engine** with a `noir` theme for public diagrams.
- A single MCP surface composing the lanes' tools.

[0.9.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.9.0
[0.8.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.8.0
[0.7.1]: https://github.com/thwomp-io/watchman/releases/tag/v0.7.1
[0.7.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.7.0
[0.6.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.6.0
[0.5.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.5.0
[0.4.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.4.0
[0.3.2]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.2
[0.3.1]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.1
[0.3.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.0
[0.2.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.2.0
[0.1.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.1.0
