# Changelog

All notable changes to Watchman, following [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.1.0
