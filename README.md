# Watchman

**A personal agentic harness — a corpus of who you are, and tools that act on live data and reason against it.**

Watchman renders live, deterministic views over your own data — finance, career, travel — through a CLI
and a resident desktop console. It's built on one thesis:

> **The corpus is the product.** Every output — a net-worth read, a role shortlist, a market take, a
> ghost-written note — is bespoke *only to the degree the corpus knows you*. The corpus is a **narrative**:
> your stories, the *why* behind your decisions, your preferences and their emotional texture, **in your own
> voice**. That voice is the moat — it's what makes the output sound like *you* instead of generic AI. The
> machine-readable weights the tools read are a thin derived projection of it. The dashboards are just a surface.

**Three layers** make it work — the diagram below traces how they connect:

- **Corpus** — *who you are.* Your stories, decisions, and preferences, in your own voice. The product;
  everything else is downstream. The tools read a thin, legible projection of it — `portfolio.yaml`,
  `watchlist.yml`, `weights.yaml` ([real examples below](#the-weight-packs)).
- **Harness** (`hn`) — *the tools.* A CLI of domain lanes — finance · career · travel — that read the corpus
  + live data and reason against it. No model in the loop.
- **Watchman** — *the console.* A resident desktop app (dashboards + a notification bus) that renders the
  harness's output, live and always-on.

![How Watchman works](docs/assets/architecture.svg)

## See it move

A weight **pack is a persona** — a complete sample profile across every lane. Loading one swaps the *whole*
console; the dashboards are just weights over the corpus.

![Persona swap on the finance console](docs/assets/demo-finance.gif)

_Finance console: swap the active pack and the whole view reprojects — net worth, the trend chart, the
concentration treemap, positions — pulling **live, deterministic data** as the widgets refresh. (Recorded
on the bundled sample packs — no real data.)_

![Persona swap on the career console](docs/assets/demo-career.gif)

## What's in the box

- **`hn` CLI** — one root binary, three mountable lanes:
  - `hn finance` — read-only market data: quotes · positions · net worth · **market** (regime/breadth) ·
    fundamentals (SEC EDGAR) · multiples · **correlate** (diversification/beta vs. a factor) · news · wire
    (broad-market headlines) · research · watch · screen. **No trading; observation only.**
  - `hn career` — a read-only role-hunt lane: keyless openings scans (Greenhouse/Ashby) with posted comp,
    company profiles, and D3 visuals.
  - `hn travel` — live-travel research hands: flight ranking · hotels · events · traffic · ferries · weather
    /air/quake senses · destination viz.
- **Watchman console** — a small resident desktop app (Tauri): domain **dashboards** that self-refresh from
  the CLI's `--json` verbs, a **notification bus** for standing agents, and an interactive **viz** layer.
  No model is ever in the render loop.
- **A D3 viz engine** — one Python↔Node renderer shared across lanes (the diagram above is rendered by it).

## Quickstart — runs out of the box on bundled sample packs

No personal data required: Watchman ships **fictional sample personas** so a fresh clone runs immediately.

```bash
uv sync # Python env + deps
cp .env.example .env # optional — every API key is independently optional

# run any lane against a bundled sample persona (a fictional ~$1M household):
uv run hn finance networth --pack samples/packs/demo-investor
uv run hn finance positions --pack samples/packs/demo-investor
uv run hn career openings --pack samples/packs/demo-growth # an early-career job hunter

# or set it once for the session:
export WEIGHTS_PACK=samples/packs/demo-investor
uv run hn finance networth
```

Many senses are **keyless** (weather, air quality, earthquakes, geocoding; finance **fundamentals** via SEC
EDGAR) and need no setup. Lanes that call paid providers (SerpAPI flights/hotels, Alpaca market data) only
activate when you add their key — each is independently optional.

The desktop console:

```bash
cd bus-app && npm install
npm run tauri dev # needs Rust (rustup) + platform build tools
```

In the console, use the **PACK dropdown** in the masthead to load a sample persona, then swap to another —
the whole dashboard set re-renders from the pack. Use **Load Weight Pack…** to point it at your own.

### Platform support

| Platform | Engine (`hn` CLI) | Desktop console |
|---|---|---|
| **macOS** | ✅ | ✅ build from source (`npm run tauri build`) |
| **Windows 10/11** | ✅ (uv is first-class on Windows) | ✅ prebuilt `.exe`/`.msi` installers on [Releases](../../releases) (v0.3.0+) — or build from source |
| **Linux** | ✅ (or use the container, below) | ✅ prebuilt `.deb`/`.AppImage` on [Releases](../../releases) (v0.3.0+) — or build from source |

**Windows quickstart** (PowerShell): install [uv](https://docs.astral.sh/uv/) and run the same commands —
the env-var syntax is the only difference:

```powershell
uv sync
uv run hn finance networth --pack samples/packs/demo-investor
# or for the session:
$env:WEIGHTS_PACK = "samples/packs/demo-investor"
uv run hn finance networth
```

For the console on Windows, grab the installer from Releases and use the **PACK dropdown** — no toolchain
needed. Building from source needs Node + Rust with the MSVC build tools rustup installs (WebView2 ships
with Windows 10/11).

**Linux console** needs the Tauri v2 system libraries before `npm run tauri dev`/`build`
(Debian/Ubuntu: `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev
libsoup-3.0-dev patchelf`; the tray icon wants an appindicator extension on GNOME).

> Platform notes: the **standing agents** (scheduled headless runs) are macOS-`launchd` today —
> Task Scheduler / systemd ports are on the roadmap. On Windows/Linux the console renders a calm
> *standby* state for them until configured. All state lives under `~/.local/state/harness` and
> `~/.config/harness` on every platform (yes, dot-dirs on Windows too — one convention everywhere).

## Run as a container

The **headless engine** (the `hn` CLI + standing agents + bus) also ships as a container image — a portable
way to run any `hn` command without a local Python setup, and the foundation for running the standing
agents as a service. Your corpus is **mounted**, never baked in. *(The desktop console is a native app — it
ships as platform bundles, not in the image; a fully browser-based console is on the roadmap.)*

```bash
# pull the published image (built + scanned + smoke-tested in CI, published to GHCR)
docker run --rm ghcr.io/thwomp-io/watchman --help

# run a lane against your own mounted corpus (defaults to /corpus inside the container):
docker run --rm -v "$PWD/corpus:/corpus" ghcr.io/thwomp-io/watchman finance networth

# or build it yourself
docker build -t watchman .
```

## A pack is a persona

```
samples/packs/<persona>/
  pack.yaml # identity: name, title, description, lanes, default
  finance/ # narrative + machine weights (portfolio.yaml, networth-history.json)
  career/ # the role-hunt root (watchlist.yml, applications.yaml, discoveries/)
  travel/ # preferences + weights.yaml, trips/
  dashboards/ # (optional) a curated console the pack describes for itself
```

A pack only needs subdirs for the lanes it covers. Your **real** corpus lives **outside** this repo
(config-pointed via `TRACKER_PATH` / `WEIGHTS_PACK`), so nothing personal is ever committed. Scaffold your
own with **`hn init <dir>`** — it lays down the dirs + template weights to fill in (non-destructive). The
bundled packs are the warm start; the [corpus-operator skill](skills/corpus-operator/SKILL.md) teaches an AI
agent to build *your* corpus — passively, from natural conversation — and keep it current in your voice.

## The weight packs

The "weights" aren't a black box — they're plain, legible config: the criteria each lane scores against.
A taste from the bundled `demo-investor` persona (fictional data):

`finance/portfolio.yaml` — holdings, a watchlist, and the thresholds that drive day-flags:

```yaml
holdings:
  - {symbol: VTI, account: taxable, shares: 900, avg_cost: 238.40, type: etf}
  - {symbol: AAPL, account: taxable, shares: 200, avg_cost: 168.25, type: stock}
watchlist:
  - {symbol: NVDA, note: "megacap semis — AI bellwether"}
pulse: # standing-watch thresholds
  day_move_pct: 5.0 # flag a holding moving more than this on the day
  index_move_pct: 1.5
```

`career/watchlist.yml` — which companies to scan + what counts as a match:

```yaml
companies:
  - {name: GitLab, ats: greenhouse, token: gitlab, tier: "Tier 1"}
filters:
  title_any: [infrastructure, platform, reliability, sre]
  seniority_any: [staff, principal, director, "head of"]
  title_none: [sales, intern]
```

`travel/weights.yaml` — home base + what makes an event worth a trip:

```yaml
conditions:
  home: "Minneapolis, MN"                # drives the conditions watch + flight ranking
events:
  centerpiece_subgenres: ["NBA", "NFL"] # leagues worth planning a trip around
```

Change a number, reload, and every dashboard reprojects. An [AI agent maintains these *for*
you](skills/corpus-operator/SKILL.md) from conversation — but they stay plain files you own and can read.

## Posture

- **Privacy by construction.** Every outbound call carries a tool-only `User-Agent` (no name/email). Your
  real corpus stays out of the repo. The finance lane reads public prices and a local file — no brokerage
  credentials, no account in the loop.
- **Corpus is the source of truth; live data disciplines it.** The machine-readable weights are kept in
  *manual* sync with the human-edited narrative; the prose is never parsed for numbers.
- **Deterministic core, agent periphery.** Detection, thresholds, and dashboards are model-free at runtime;
  an agent designs, narrates, and writes artifacts out-of-band. No model sits in a render or alert loop.

## Docs

- [`skills/corpus-operator/SKILL.md`](skills/corpus-operator/SKILL.md) — the heart of the project: how an AI
  agent builds and maintains *your* narrative corpus, in your voice.
- [`skills/console-operator/SKILL.md`](skills/console-operator/SKILL.md) — the companion: how an agent
  *operates* the console and drives each lane (operate-the-tool vs. build-the-corpus).
- [`docs/BUS.md`](docs/BUS.md) — the notification-bus producer contract (publish events from any language).
- [`SECURITY.md`](SECURITY.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md)

## License

[Apache-2.0](LICENSE).
