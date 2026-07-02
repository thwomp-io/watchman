# Changelog

All notable changes to Watchman, following [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

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

[0.4.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.4.0
[0.3.2]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.2
[0.3.1]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.1
[0.3.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.3.0
[0.2.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.2.0
[0.1.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.1.0
