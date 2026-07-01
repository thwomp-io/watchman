# Changelog

All notable changes to Watchman, following [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

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

[0.2.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.2.0
[0.1.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.1.0
