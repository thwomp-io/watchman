# Changelog

All notable changes to Watchman, following [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [0.17.0] - 2026-11-11

### Security
- The desktop shell's `rusqlite` moves 0.32 to 0.41, which bundles libsqlite3-sys 0.39.1
  (RUSTSEC-2026-0459), and `rustix` moves 1.1.5 to 1.1.6 (RUSTSEC-2026-0486; it is reached through
  the updater's archive extraction).
- The bus server registers every `/api` route through one bearer-token check, with a test that every
  route but `/health` refuses a request without the token. Its token file is created owner-only.
- `pyproject.toml` bounds the libraries the CLI is built on to their current major line (Typer 0.29.x,
  Pydantic 2.x, httpx 0.x, Starlette 1.x), so an install that resolves outside the lockfile cannot
  pick up a breaking release.

### Added
- **`hn finance calendar` reads a printed number from the agency that publishes it.** CPI, PPI,
  payrolls, the unemployment rate and hourly earnings come from the BLS API, GDP and the PCE price
  index from BEA, retail sales from Census, and the number shows the agency beside it
  (`actual 0.4% (bls)`; `actual_source` in `--json`). Nasdaq remains the schedule, the consensus and
  the fallback. A headline figure Nasdaq has no row for is added (`CPI y/y`), and one the agency did
  not publish reads `not published`. Weekly jobless claims stay on Nasdaq.
- **Market-holiday rows on the calendar**, from SIFMA's bond-market schedule and the NYSE's: a close
  in one market names the other as open, and early closes carry their times.
- **`hn calibration`**: a ledger of predictions scored after the fact. `add` logs a claim with a
  probability, a resolve-by date and the test that settles it; `due` lists what has come due;
  `resolve` records the outcome and its Brier score, or voids an entry whose test can't be
  evaluated; `score` reports the mean Brier and five reliability buckets per owner.
- **A history store for the ledger.** `portfolio.yaml` holds the current state; fills, changes made
  against a broker screen and fund-proxy grades are rows in a SQLite database in the state
  directory, exported after every write to `ledger-history/ledger.jsonl` beside the portfolio file
  (the export is what version control carries; the database is rebuilt from it when missing).
  `hn finance ledger-log` reads it, `ledger-note` records a change made by hand, and `ledger-grade`
  records a day's check of the fund proxy against the NAV.
- **`hn finance orders [--json]`** (MCP `finance_orders`): the resting orders against the live price,
  from one quote request.
- **`hn finance watch --only SECTION`** (repeatable): build just the named sections of the digest
  (`moves`, `watchlist`, `drift`, `wash-sale`, `prints`, `news`).
- **`hn career comp --refresh-stale --commit`**: the ladder feed commits what it wrote (the comp
  directory only) and pushes if that fast-forwards. A commit that fails is one bus warning.

### Changed
- **Every CLI failure is one short report on stderr**: `error: <what happened>` and, when there is
  one, `hint: <what to do>`, across every command group. An unexpected exception is named in one
  line and exits 70 (`HARNESS_DEBUG=1` shows its traceback); a command called wrong exits 2. A
  network error reads as a source failure naming the host, never the URL. With
  `HARNESS_ERROR_FORMAT=json` the report is one JSON line; the console sets it and shows a failed
  widget's kind, sentence and hint with a Try again button and the raw output under Details.
- **`hn` starts faster.** A command group is imported when it is asked for: `hn version` went from
  207 ms to 71 ms, and every dashboard widget and agent tool call is one such start.
- **Dashboard widgets ask for what they show.** Day moves, Upcoming prints, Open traps, Resting
  orders and Indexes ran the whole watch or pulse (13 to 19 seconds on a full book) to read one
  field; they now run `watch --only …`, `orders` and `market` (about a second each).
- `hn finance log-fill` writes the new state and nothing else. Prior values, the filled order and the
  cash figures go to the history store as one row; they used to be kept as comments in the file.
- Fed speakers are one pulse flag per day, naming each speaker with their time. The Chair keeps a
  flag of their own, and the gates board still lists every speech.
- The Inbox status strip's wash-sale line reads `wash-sale window · until <date>` or `clear`.
- Internal structure, no behavior change: the three lane CLIs share one layout (a package with one
  module per noun); the travel service, the finance pulse and watch, the portfolio reader and the
  bus HTTP server are split into named modules and steps. Each was run old against new on the same
  inputs and matched.
- Dependencies refreshed within their declared ranges, Playwright to 1.65.0 among them.
- `ARCHITECTURE.md`: how the code is laid out, and why.
- The README leads with how the system works, and its two diagrams are redrawn: they follow the
  reader's light or dark theme and describe the parts in plain terms.

### Fixed
- Every pulse flag reaches the bus. Flags that shared a kind and a subject (two macro releases on one
  day, two filings for one symbol) derived the same once-a-day key, so the bus stored the first and
  dropped the rest as duplicates.
- A dashboard's Open traps and Resting orders tables no longer consume the headline seen-cache; their
  source marked headlines as seen on every refresh.
- Confirmed earnings dates, consensus price targets and the economic calendar are back. Nasdaq began
  refusing the Python HTTP client while answering `curl`, so those requests go through the system
  `curl` when there is one; without it a refusal is one note and dates fall back to estimates.
- A throttled Nasdaq request no longer fails the whole `watch` and `pulse` run; it is a note on the
  digest, and the date is asked again on the next run.
- The ladder feed stops at the first request the site refuses, whatever the status, and a stopped
  run publishes a warning.
- `--pack` works on Typer 0.29.
- A calendar read late in a Pacific evening treats the day's releases as past.

## [0.16.0] - 2026-11-08

### Security
- Every Playwright launch runs with the Chromium sandbox on (Playwright defaults it off): the console
  shot and tour scripts, the e2e suite, and the career apply fill. The apply fill opens third-party
  pages only in a current browser: installed Google Chrome when present, else the bundled Chromium,
  checked against Google's stable version list first. A stale build is refused unless
  `--allow-stale-browser`.
- The container image applies Debian's published updates at build time, which picks up zlib1g
  `+deb13u1` (CVE-2026-76844, CVE-2026-85091) before the upstream Python image is rebuilt.
- block-buffer 0.10 (GHSA-qwgh-2vcv-g2f7) is accepted in `osv-scanner.toml`: it is reached only
  through tauri-codegen's build-time hashing of the app's own assets, and the 0.10 line has no fix.

### Added
- **The demo's Inbox has events.** Each sample persona ships `bus/inbox.json`, fictional alerts,
  watch items and wire stories that fit its book, trips and job search, and `hn console demo`
  seeds them when the persona opens. Switching personas replaces them.
- **`hn finance moves SYM… --from D [--to D] [--groups 'name=A,B;…']`** (MCP `finance_moves`): each
  name's close-to-close move on every session after a base date, its cumulative, and optional group
  means. Split-adjusted SIP daily bars; an OTC name the exchange feed lacks comes from its Yahoo
  daily bars, where a session with no trade is blank.
- **`hn finance crashdays SYM [--threshold -7] [--since D] [--cluster-days N]`** (MCP
  `finance_crashdays`): every session at or below the threshold, its drawdown from the highest prior
  close, forward returns at +5/+20/+60/+120/+250 sessions, and the lowest close in the next 60. A
  horizon not yet reached is blank, never zero. `--cluster-days` folds nearby events into one
  episode on its deepest day.
- **`hn finance session SYM… [--date D] [--bar 5Min|15Min] [--window HH:MM-HH:MM]`** (MCP
  `finance_session`): one regular session's intraday shape: high and low with their ET bar times,
  close vs low/high, the last 15 and 30 minutes, last-15-minute volume against the session's
  average, and the closing auction print.
- **`hn finance daygl --after-hours`**: outside the regular session, the quoted holdings at their
  latest extended-hours trades against the last close. Labeled a thin-print estimate, not a
  forecast.
- **`hn finance log-fill SYM --qty N --price P --screen ID`**: applies one broker fill (a buy) to your
  corpus `portfolio.yaml` as a targeted line edit that keeps comments: shares, average cost and basis
  (checked against `--broker-avg` / `--broker-basis`), the open order marked filled, the cash sweep
  netted with `--pending`, and a dated sync line. It parses the result and restores the original on
  any failure. `--dry-run` prints the diff. It refuses to write a sample pack.
- **`hn finance wire read` reads PDFs**: a regulator's deck or a filing exhibit comes back as
  page-numbered text through poppler's `pdftotext`. `--grep TEXT` prints every match with its page
  number. A page with no article structure (an EDGAR filing) returns its full text.
- On a 401/403 from Reuters or AP, `wire read --match` reads the publisher's licensed syndicated copy
  (Yahoo, Investing.com, AOL) when its headline matches word for word, and names both copies. The
  map is replaceable through `finance.wire.mirrors` in the overlay. Paywalled publishers have no
  entry.
- **`hn finance fund-proxy` exposure fills**: with `holdings_ref` (the fund's N-PORT roster) and
  `fills:` configured, one exposure ETF per region stands in for the roster names the basket can't
  quote. Before, that weight was spread over the quoted names.

### Changed
- `positions`, `networth`, `daygl` and `fund-proxy` value quoted holdings at the official
  regular-session close once the session is over, never an after-hours print. The basis is stated
  in the output (`price_basis` in `--json`).
- `hn finance history` prints intraday bars in local time, and `--feed delayed_sip` works for bars.
  Alpaca errors carry Alpaca's own reason.
- The User-Agent carries the project URL only for providers whose policy requires a contact
  (Wikimedia). SEC's data.sec.gov refuses that form and gets the bare UA. Image downloads no longer
  send a browser UA. A SEC refusal names its cause.

### Fixed
- The Backlog tab's beads board sat at the generic tile height, which cut the tree to one clipped
  row under its own caption. The board now has a height floor, and its caption sits below the tree.
- Switching personas in the demo while on the Inbox left the previous persona's events and status
  strip on screen until the next poll. The Inbox now reloads with the persona.
- On a split's ex-date, quotes and official closes adjust the prior close by the split ratio. A 3:1
  split no longer reads as a −70% day.
- Date-only `--end` values of today no longer get refused for `delayed_sip` bars in the evening.
- `hn travel images` names the HTTP status of a failed subject instead of reporting "No image
  found", and exits non-zero when every subject failed.

## [0.15.0] - 2026-10-24

### Security
- Dependencies patched for advisories published since 0.14.0: Pillow 13.0 (six GHSAs), certifi,
  tokio 1.54.1 (RUSTSEC-2026-0386), rustls-webpki 0.103.16, webpki-roots / webpki-root-certs 1.0.10,
  undici (dev-only). The desktop app's HTTP client moves from ureq 2 to ureq 3, which drops the
  webpki-roots 0.26 compatibility shim.
- The container image moves from Debian 12 (bookworm, now LTS-only) to Debian 13 (trixie), same
  Python 3.12.
- CI no longer forces the uv cache on, so setup-uv's default skips the cache on events a fork could
  poison. The image scan runs Trivy 0.76.1.

### Added
- **`hn places search "<query>" --near <ZIP|place|lat,lon>`** and **`hn places show <place_id>`**
  (MCP `places_search` / `places_show`): local place search for any lane, using Google Maps through
  SerpAPI. A search returns name, distance, open-now or today's hours, rating, address, phone,
  website and place_id. A run that needs a live search is a priced dry run unless `--yes`. `--near`
  is resolved once and cached; `lat,lon` is free.
- **`hn bus logs [--rotate]`**: size-capped rotation for logs nothing else bounds (service-manager
  log files, run logs). Over `cap_mb` (5) a log is gzipped to `.1.gz`, `keep` (5) copies are kept,
  and the file is truncated in place so a running service keeps writing to it. The weekly
  `hn bus purge --policy default` runs it. Configure under `bus.log_rotation` in the overlay.
- **Game weekends**: a *pair* card when two leagues play one destination in one Thu–Mon window,
  naming both games. *Corridor* cards (`nudge.games_corridors`) give one game a night across nearby
  cities. `nudge.convenience_lead_curve` weights how easy a trip is to reach by how soon it is.
  `nudge.games_dead_windows` lists spans to skip, and `nudge.games_per_city` lets one destination
  hold more than one weekend.

### Changed
- `hn bus serve` logs no per-request lines unless `--access-log`, and every line carries a
  timestamp, including the engine's own warnings such as a failed web push.
- Every dependency is current. Notable: TypeScript 7, React 19.3, Vite 8.3, jsdom 30, Starlette
  1.9, uvicorn 0.54, Pydantic 2.14, and Tauri 2.12.5. CI actions are on their current majors.

### Fixed
- Ticket-package listings are no longer counted as games, and promotional text after a team name
  no longer becomes the opponent's name.

## [0.14.0] - 2026-10-20

Watchman is now web-first: the engine's served console is the one backend, and the desktop app is an
optional window over it. Try the whole console in one command with `docker run … console demo`.

### Added
- **`hn console demo`**: the console on the bundled fictional personas, sealed. It stages a scratch
  root (config, state, cache, bus, an empty corpus), opens on the default persona, plays each
  persona's scripted CHAT, and lets you switch personas from the header's DEMO picker (sample packs
  only). Ctrl-C or `docker stop` deletes the scratch root:
  `docker run --rm -p 8787:8787 ghcr.io/thwomp-io/watchman console demo --host 0.0.0.0`.
- **Built-in dashboards and registry defaults ship with the engine.** A machine that never ran the
  desktop app gets the full console: missing dashboards seed into `~/.config/harness/dashboards/`,
  and the default surfaces, live viz and producers apply until you write your own registry.
- **Packs on the served console**: `list_packs`, `get_active_pack`, `set_active_pack`. A sample pack
  seals the vault, every spawn (corpus, state, bus, cache) and the inbox's bus; a personal pack serves
  the lanes it provides and falls back for the rest; a pack's dashboards replace the tab set.
- **`hn bus serve --writes a,b`**: enable named console writes (`set_active_pack`, `save_dashboard`,
  `reset_dashboard`, `run_producer`). The default stays read-only, and connection settings are never
  writable from a browser. `get_config` reports the enabled writes; the Dashboard Studio, the pack
  picker and RUN PRODUCERS appear only where they work.
- The container image ships the sample personas (`HARNESS_PACKS_DIR=/app/samples/packs`).

### Changed
- **The desktop app is a thin shell.** It starts `hn bus serve --console` from its engine on a private
  loopback port (its own token, writes enabled) and shows that page, or opens a remote served console
  (**⚙ Settings → Connection**). It keeps the menu-bar inbox, native notifications, launch at login, the
  folder picker for personal packs and the updater; the server exits with the app, even after a
  force-quit. The Rust copy of the console's commands is gone, so there is one backend.
- The README leads with the demo and the served console; `tailscale serve` is the recommended HTTPS
  path for phones on a tailnet (Headscale and other VPNs bind the private address and pass a
  certificate). The installers are listed as the optional desktop app.
- A widget whose configured working directory doesn't exist on the serving machine runs anyway.

## [0.13.1] - 2026-10-19

v0.13.0's release build failed before the installers were built. 0.13.1 carries the same features
with the build fixed.

### Fixed
- The desktop app passes clippy on Linux (unused macOS-only event bindings) and newer toolchains.
- The generated TypeScript contracts no longer carry trailing whitespace, so the drift check passes.
- The container image scan skips one file: yt-dlp's Shahid extractor ships that site's public API key
  in its source, which the secret scanner reported as an AWS access key.

## [0.13.0] - 2026-10-19

This release adds the pocket agent: the console's CHAT tab and `hn pocket` let you talk to a sealed,
read-only Claude Code agent running on your own machine from any device, and every sample pack ships
a scripted demo conversation. It also redesigns the Career tabs (Campaign · Openings · Pool · Ladders,
with expected comp per role), adds a follow-the-sun market board with a rates rail, a macro release
calendar and a market-take writer, and draws live visit and trip schedules in VIZ.

### Added
- **Pocket agent** — `hn pocket` and the **CHAT** tab: one conversation with streamed replies, tool
  activity folded into one expandable row, and a status card (model, context ring, 5-hour and 7-day
  use with reset countdowns, cost estimate, the seal, a new-conversation button). A runner on the
  serving machine answers each message with your installed Claude Code CLI inside a sealed
  permission set: read and shell built-ins only, write tools denied by name, secret paths denied in
  the rules and in the OS sandbox (no shell network), API keys stripped. It opens no port.
  `hn pocket selftest` checks the seal statically and with a live probe; the runner serves nothing
  while a check fails. Verbs: `run`, `selftest`, `status`, `send`, `tail`, `stop`, `reset`,
  `show-command`. Every turn and refusal is a bus event. Setup and threat model: `docs/POCKET.md`.
- **Pocket replay** — `hn pocket replay`: a scripted, model-free CHAT conversation run through the
  real parser, labeled "scripted demo — no agent". Every sample pack ships `pocket/replay.json`.
- **Market-take writer** — `hn finance take-write` (MCP `finance_take_write`): writes one new take
  to `finance/market/takes/`, naming the file from the clock and owning the frontmatter; an existing
  file or a symlink refuses the write. It is the only write tool the pocket agent can be granted.
- **Career ▸ Campaign** — `hn career campaign`: conversations in flight (stage, next step, last
  touch, stale flags) and career-calendar milestones with countdowns.
- **Career ▸ Openings** — `hn career roles`: every matched opening as a role item, ranked by fit
  (on-profile · adjacent · off-lane), then new, then item level. Each role shows its **expected
  comp**: the likely level on the company's levels.fyi ladder, that level's median total comp, and
  the neighbouring levels as the range. `hn career openings --new` marks openings first seen since
  earlier scans; a board that errored once never reads as new or gone.
- **Career ▸ Pool** — `hn career pool`: levels.fyi leaderboard cuts and cached ladders merged into
  one tile per company, scored against your comp anchor with named modifiers from a corpus catalogue,
  rarity colors, source/cut/level filters, a separate pane for companies your exclusion list
  filters out (the `pool_flags` values listed under `pool_screen_out:` in the watchlist), and links
  to each company's openings.
  A company with no band row reads `unranked`, not poor.
- **Career ▸ Ladders** — `hn career comp <company>`: a company's pay ladder from levels.fyi's
  keyless markdown pages (median TC, YOE and pay mix per level, geo-pinned, cached a week, attributed).
  `--all` feeds the tab, drawn as rarity-colored rung bars against your anchor; `--report [YYYY]`
  adds the year-end pay report as a percentile strip; `--refresh-stale` is a paced, capped refresh
  that stops on the first throttle. `--lca <employer>` queries the DOL LCA filed-wage disclosures.
- **levels.fyi index** — `hn career levels-index`, `families`, `slugs`, `industries`: company slugs,
  job families, locations and leaderboard grammar from the site's sitemaps.
- **Apply pipeline** — `hn career posting <url>` captures a Greenhouse or Ashby posting and its
  application form; `hn career apply <slug>` validates your answers and files against the form and
  writes a fill sheet; `--fill` fills the hosted form in a visible browser and stops for you to click
  Submit; `--submit --receipt` records the submission. Never touches EEO, consent or captcha controls.
  `hn career schools <term>` lists the school names a Greenhouse form accepts.
- **Global market board** — `hn finance global` adds a rates rail (3-month to 30-year yields, the
  2-year note future) and the Treasury par-yield curve with 2s10s / 3m10s / 5s30s spreads, SKEW,
  Bitcoin and ULSD. Every futures row quotes a named dated contract with roll-day notes; every cash
  index row names its session (`LIVE` or the closed session's day) on the exchange's own clock. The Global
  tab gains a ticker strip of commodities, yields, the dollar, VIX and Bitcoin.
- **Macro calendar** — `hn finance calendar`: US economic releases with actual / consensus /
  previous (Nasdaq), the FOMC calendar (Federal Reserve) and Treasury coupon auctions, cached. High-tier
  releases join the gates board; pulse adds `macro_print` when a number is out.
- **Wire reader** — `hn finance wire read <url> | --match TEXT`: the article body behind a wire
  headline, with Google News links resolved to the publisher and paywalls labeled partial.
- **Inbox and news redesign** — standing agents become status-bar chips; the catalyst wire reads one
  row per story with ticker chips, outlet and age; a catalyst opens like an article (outlet, author,
  lead image, **Open original**). Quote and option-chain pages are filtered at ingest (rules extend
  in `feeds.yaml`). `hn finance wire audit` reports what each feed ships.
- **Chart widgets** — a candlestick price chart with support/overhead levels (Core ▸ Position chart)
  and a book correlation heatmap (`hn finance correlate --held`).
- **Market rates rail** — `hn finance market` adds SHY / IEF / TLT / TIP with curve-shape and
  breakeven facts, and KRE on the credit rail.
- **Vest gates** — forward RSU vests rank on the `hn finance gates` board; the Plans tab gains a
  vest-timeline widget.
- **Visit and trip banks** — `hn travel bank`, `schedule`, `banks`: a visit's outings and restaurants
  as photo tiles, a day-by-day schedule grid with weather and availability, and a picker that steers
  both. A new **Trips** tab has the same shape. `hn travel bank-photos` adds photo galleries;
  `hn travel bank-enrich` adds hours, reserve/order/menu links and phone from the provider cache.
- **VIZ ▸ Travel ▸ LIVE** — every visit or trip with a schedule gets a full-size live schedule entry.
- **Travel Planning, rebuilt** — `hn travel upcoming`: an "On the calendar" tile, the open decision's
  radar beside its ranking, and trip nudges with thumbnails. Nudges gain long-lead, horizon, family
  and game-weekend lanes (followed teams' home games on open weekends). The `idea` status parks a trip.
- **Destination search** — `hn travel explore`: cheapest destinations from your origins via Google
  Travel Explore, with fares, dates and hotel prices; `hn travel quota` reads the SerpAPI budget;
  `hn travel hotels --pick "A,B"` shortlists named hotels in one search.
- **Video reading** — `hn media transcript <url>` (timestamped transcript) and `hn media frames <url>`
  (scene-change stills plus a contact sheet) for YouTube videos. Adds `yt-dlp` as a dependency; `frames`
  also needs `ffmpeg` on the PATH.
- **Backups** — `hn backup snapshot` builds an age-encrypted snapshot (git bundles, sqlite copies,
  config tars) into a synced folder with retention; `hn backup status` is the health check.
- **Bus retention** — `hn bus purge --policy default` ages out old read events and rotates logs.
- **Image cap** — travel images are downscaled and EXIF/GPS-stripped on write (`hn travel shrink-images`).
- Sample packs ship data for the Pool, Ladders and CHAT tabs. A dashboard's `order` field sets subtab
  position.

### Changed
- `hn career render` writes one folder per design, the file named by your configured stem alone.
- The role-shape classifier reads title, then domain word, then team, with off-lane buckets.
- News scans run as a bounded, host-paced fan-out; earnings-date misses are cached
  (`watch` about 40s → 8s warm).
- README, architecture diagram and `docs/EXAMPLES.md` cover CHAT and the new Career tabs.
- Windows installs come from the release `.exe` / `.msi` only. The winget package was never
  accepted into the community repository, so the README no longer lists `winget install` and the
  winget workflow is removed.

### Fixed
- `hn finance multiples` / `fundamentals` read SEC EDGAR filings correctly: total debt from one
  balance-sheet date, derived Q4 TTM, revenue tag choice, stale share counts, EV with non-controlling
  interest. `hn finance filing` reaches every document of a large filing.
- `hn finance global` rejects a broken previous close after a long exchange holiday.
- Feeds keep their newest items, not their first; headline times are UTC on every feed.
- A ticker that is a YAML boolean word (`ON`, `NO`, `YES`) no longer vanishes from configs.
- `hn finance correlate --factor` keeps the factor block when no basket member is in the matrix;
  `hn finance bars` lists a broken support as overhead.
- MCP `finance_wire` and `finance_watch_peek` cap results to fit the tool-result limit; the
  installed MCP server no longer fails at import (`mcp` capped below 2).
- One dead job board no longer aborts the openings scan.
- Line charts label the X axis by month; the expanded chart fits the window; Projections scrolls on
  desktop and phone; the schedule widget no longer clips short items.
- `HARNESS_CACHE_DIR` and the sealed `.env` keep a demo console off your real caches and keys.

### Security
- The console door refuses hidden paths and non-text files on document reads, caps event listing at
  500, and refuses request bodies over 64KB. `bus-app.json` is written owner-only (0600).
- Dependency bumps for published advisories: `aiohttp` → 3.14.5, `anyio` → 4.15.1, `mcp` → 1.30.0,
  `multidict` → 6.9.1, `pyjwt` → 2.15.1, `urllib3` → 2.8.0, `rustls` → 0.23.46, `quick-xml` → 0.41,
  `js-yaml` → 5.4.2, `vite` → 8 (drops the dev-only `esbuild` advisory).
- The desktop app moves to Tauri 2.12 and updater 2.14, and builds the updater without its `zip`
  feature: the update artifact is the signed `.exe` installer, so the `zip` crate and its extraction
  advisories (RUSTSEC-2026-0367/0368/0369) are gone from the build.
- **Known, accepted advisory** (no fixed version published): `zbus_names` / `zvariant`
  (RUSTSEC-2026-0370/0371/0372), Linux builds only, under the desktop notification and
  single-instance plugins.

## [0.12.1] - 2026-08-25

### Security
- Dependency bumps for two HIGH advisories the release CI's Trivy scan caught on the v0.12.0 tag
  run: `aiohttp` 3.14.1 → 3.14.3 (CVE-2026-69244, denial of service via malformed HTTP responses)
  and `cryptography` 49.0.0 → 50.0.0 (CVE-2026-69247). No code change. The v0.12.0 installers are
  unaffected by the scan result; the container image for 0.12.0 was not published — pull `0.12.1`.

## [0.12.0] - 2026-08-25

This release adds three Finance console tabs (Projections, Holdings, Plans) and the verbs behind
them, a proactive trip-nudge layer on the Travel Planning tab, two market-regime signals (credit
stress, spot VIX) on the market read, a phone pass over the newer Finance tabs, and retires the
concentration-unwind tab. Every bundled sample persona now ships data for every console tab.

### Added
- **Projections** — `hn finance projections`: scenario-grid risk/reward bands per name per
  horizon (6/12/24/36 months), computed from a params registry you author in your corpus
  (`finance/reference/projection-params.yaml`: forward EPS with its accounting basis, a growth band,
  a multiple band, provenance) against the live quote. Horizons past a year are marked `sketch`;
  params older than about a quarter flag `stale`; the disclaimer ships in the JSON. The
  **Finance ▸ Projections** tab renders one card per name (price, forward multiple, the 12-month
  band, a tier bucketed by the modeled return) with the full per-horizon grid on hover. Names on your
  values screen (a personal exclusion list) are listed below the rest, and on phones one tap opens the grid and a
  second follows the link. Values-screen status rides the contract as an optional `screen_note`
  so screened names render marked, never hidden.
- **Holdings** — `hn finance holdings`: the held book appraised through the same params —
  for every held name with an entry, the modeled 12-month return at the live price *and* at your
  average cost ("how good was the entry?"). Names without an entry render with the reason named,
  never dropped. The **Finance ▸ Holdings** tab shows holding cards in portfolio order with the
  same hover appraisal and click-through to the research artifact.
- **Plans** — `hn finance gates`: every upcoming held-name earnings print (confirmed dates
  where the analyst calendar has them, filing-cadence estimates labeled `est`) plus your
  `macro_events` inside a horizon, imminent-first — no quotes, no wire, so a refresh is cheap.
  The **Finance ▸ Plans** tab pairs your living event-plan documents (`finance/plans/`, newest
  first) with the gates board (ranked countdown bars, hot within two days), the allocation
  current→target view, and the dollars committed by resting orders.
- **Market signals** — `hn finance market` adds a credit-stress rail (HYG / LQD / IEF and a
  high-yield-minus-Treasury day-spread fact); `hn finance global` carries spot VIX as a level
  on the US-risk rail.
- **Trip nudges** — `hn travel nudge`: proactive "you could be in {place} {window}" trip
  suggestions over the next ~6 months, scored from your corpus's destination registry and the
  reference almanac (holiday-extended long weekends), then enriched live for the top pairs
  (ticketed events, forecast inside ~16 days). Every nudge carries its component breakdown. The
  Travel ▸ Planning tab renders them as cards above the trip pipeline; `hn travel decision`
  feeds the tab's compare radar from the soonest live trip.
- **Sample personas ship the full console** — each bundled pack now carries a print-scorecard
  registry, projection params, an event plan, a ranked destinations registry, and a hosted-visit
  entry, and its `dashboards/` describes every console tab, so a fresh install demos all of them.

### Changed
- **Phone pass on the newer Finance tabs** — Prints and Projections render correctly at 390px
  (subtab strip wraps, scorecards no longer clip, tiles go single-column); the News reader stacks
  list-over-detail on narrow screens; the Inbox status bar wraps.
- The console's demo-media and examples tour now cover the current Finance tab set.

### Removed
- **The Finance ▸ Unwind tab.** Concentration unwinding is a verb, not a standing view: the
  `hn finance unwind` verb and the vest-timeline chart (on the VIZ rail) remain; only the
  dedicated tab is gone.

### Fixed
- `hn finance pulse` no longer crashes on a zero-cost or fully-disposed lot.
- `hn finance scorecards` accepts an entry with a `take_doc` but no `card_doc` (a print
  graded straight off the filing) instead of failing the whole registry.
- `hn travel nudge --json` keeps its advisory output off stdout (the registry-empty notice
  broke JSON consumers); the reason trim handles a non-breaking-space-padded event-name suffix;
  Thursday holidays extend windows to the three-night shape.
- Console-shot navigation matches subtab names exactly before falling back to substring matching.

## [0.11.0] - 2026-08-03

This release adds an earnings-print scorecard book to the console, an options-positioning
gauges verb, and fixes a CSS regression that disabled the disabled-state styling on buttons.

### Added
- **Prints scorecard book** — a new **Finance ▸ Prints** console tab: your graded earnings-print
  history as grade-bannered blocks (GREAT → DISASTER, plus PENDING), paginated newest-first;
  click a block to open the full card in the doc popup. Data comes from
  `hn finance scorecards --json` reading the print-scorecard registry in your corpus —
  the grades are written by your operating loop at print time; no model runs in the render path.
- **Options-positioning gauges** — `hn finance gauges SYM`: put/call ratio (session volume
  and open interest), IV30 (median near-the-money implied volatility, 20-45 days out), HV30
  (trailing realized volatility, matching the common brokerage 30-day convention), and the
  IV−HV spread with a braced/neutral/complacent read. The output states its own boundaries:
  the free indicative feed, per-contract IV coverage, the ~1-day open-interest lag, and that
  short interest is not available from this provider. Requires Alpaca keys; MCP twin
  `finance_gauges`.

### Fixed
- **Disabled-state CSS regression** — 11 `:not(:disabled)` selectors in the console stylesheet
  had been corrupted to `:not(disabled)` by a build step, so disabled buttons still
  lit up on hover and depressed on click. The build step is fixed and the selectors ship
  intact again.

## [0.10.1] - 2026-07-27

Security-patch release: the v0.10.0 tag's CI security scan flagged two HIGH-severity
dependency vulnerabilities, both fixed upstream.

### Fixed
- `mcp` 1.27.2 → 1.28.1 (CVE-2026-59950 — WebSocket server transport).
- `js-yaml` 5.2.1 → 5.2.2 (GHSA-pm4m-ph32-ghv5 — flow-collection parsing DoS).

## [0.10.0] - 2026-07-26

This release adds an SEC filing reader, a weather/conditions upgrade with an outdoor-windows
solver, an interactive events calendar, and a second generation of the trip-planning views.

### Added
- **Filing reader** — `hn finance filing SYM` fetches and renders a filing's content from
  SEC EDGAR: it resolves the newest 8-K's press-release exhibit via the filing's document table
  (filename backstop when untyped) and prints it as text. `--list` shows a filing's document
  table, `--accession`/`--form`/`--doc` select a specific filing or exhibit. Requires the SEC
  fair-access contact variable documented in docs/CLI.md; fails loud without it.
- **Conditions v1.5** — the weather surface reads the full forecast response (feels-like, wind,
  humidity, precipitation probability), alerts are feels-like-aware, and an hourly
  **outdoor-windows solver** answers "when today is it actually nice out." The Travel dashboard
  gains an at-a-glance conditions tile row.
- **Events calendar** — `hn travel calendar` merges the travel almanac and ticketed events
  into a month grid; the console renders it as an interactive widget and a full wall-board
  dashboard tab.
- **Trip-planning views, second generation** — the schedule and food-bank views add a
  source-link contract (`links: [{label, url}]` per entry, rendered as ↗ anchors in tooltips and
  detail records), a schedule-bank options panel for the unscheduled layer, and **per-date
  availability bands** — a trip whose home base changes mid-trip now renders the correct band
  per day instead of a weekly pattern.
- **Reviews texture tier** (travel) — an optional reviews provider for destination and
  restaurant research (SerpAPI-keyed; the demo experience stays keyless).
- Sample packs ship schedule + food-bank trip data, so the planner views demonstrate out of the
  box on the VIZ tab.

### Changed
- **VAULT on phones** uses a master-detail layout (list, then document) — the side-by-side
  layout was unusable at phone widths.
- **Trap map redesign** (the price ladder of resting GTC buy orders) — chunky rung bars, per-symbol lane accents (hot rungs
  keep their lane color; heat rides an accent-colored glow), live strips, and distance chips.
- The theme menu returned to the app baseplate alongside its Settings home.

### Fixed
- Filing exhibit resolution uses one uniform type map across list and fetch paths.
- Schedule marker capacity counts the time prefix, so long marker labels no longer overflow.
- `console-shot` expands collapsed rail groups before capture, so screenshots cannot silently
  omit a dashboard group.

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
  export's age shown on the board. Drives the
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
  (with a date anchor so demo dates stay relative to today), a Backlog dashboard, and richer resting
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
  skim surface and do not push. A footer bell arms each device (with a TEST
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
  a factor basket**, and the biggest **divergence days**, to check whether a holding actually diversifies the
  portfolio.
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
    and a values screen (a personal exclusion list). Observation only; no trading.
  - **career** — keyless openings scans (Greenhouse/Ashby) with posted comp, company profiles, and viz.
  - **travel** — flight ranking, hotels, events, traffic/ferries, keyless weather/air/quake senses, and
    destination viz.
- **Watchman console** — a resident desktop app (Tauri): domain dashboards that self-refresh from the CLI's
  `--json` verbs, a notification bus for standing agents, and an interactive D3 viz layer.
- **Weight packs** — portable, swappable per-persona data bundles; loading one re-renders the whole console.
  Ships bundled fictional sample personas so a fresh clone runs out of the box.
- **Shared D3 viz engine** with a `noir` theme for public diagrams.
- A single MCP surface composing the lanes' tools.

[0.13.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.13.0
[0.12.1]: https://github.com/thwomp-io/watchman/releases/tag/v0.12.1
[0.12.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.12.0
[0.11.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.11.0
[0.10.1]: https://github.com/thwomp-io/watchman/releases/tag/v0.10.1
[0.10.0]: https://github.com/thwomp-io/watchman/releases/tag/v0.10.0
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
