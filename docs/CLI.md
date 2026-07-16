# The engine CLI — lanes, packs, and keys from the shell

**This is the advanced/contributor path.** The console installers are the first-class way to use
Watchman (README Quickstart + **⚙ Settings**); this page is for running the engine directly —
development, scripting, headless boxes, or building the console from source.

## Run any lane against a bundled sample persona

No personal data required: the repo ships **fictional sample personas** so a fresh clone runs
immediately.

```bash
uv sync # Python env + deps
cp .env.example .env # optional — every API key is independently optional

# a fictional ~$1M household:
uv run hn finance networth --pack samples/packs/demo-investor
uv run hn finance positions --pack samples/packs/demo-investor
uv run hn career openings --pack samples/packs/demo-growth # an early-career job hunter

# or set it once for the session:
export WEIGHTS_PACK=samples/packs/demo-investor
uv run hn finance networth
```

On Windows (PowerShell), the env-var syntax is the only difference:

```powershell
uv sync
uv run hn finance networth --pack samples/packs/demo-investor
# or for the session:
$env:WEIGHTS_PACK = "samples/packs/demo-investor"
uv run hn finance networth
```

## Keys — each independently optional

Many senses are **keyless** (weather, air quality, earthquakes, geocoding; finance
**fundamentals** via SEC EDGAR) and need no setup. Lanes that call paid providers (SerpAPI
flights/hotels, Alpaca market data) only activate when you add their key to `.env`.

## Your own corpus + the user overlay

- **`hn init <dir>`** scaffolds a corpus (dirs + template weights, non-destructive); point the
  engine at it with `TRACKER_PATH`.
- **`hn config show`** prints the resolved **user overlay** (`config/harness.yaml` — per-lane
  `global_settings`: display names, fund identity, home city/airports). Precedence: an active
  pack's copy > your corpus-resident file > the packaged neutral template. The overlay renders
  read-only in **⚙ Settings → Personal**; the file is the interface.

## The desktop console from source

```bash
cd bus-app && npm install
npm run tauri dev # needs Rust (rustup) + platform build tools
```

**Linux** needs the Tauri v2 system libraries before `npm run tauri dev`/`build`
(Debian/Ubuntu: `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev
libsoup-3.0-dev patchelf`; the tray icon wants an appindicator extension on GNOME).
