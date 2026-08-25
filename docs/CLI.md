# The engine CLI — lanes, packs, and keys from the shell

**This is the advanced/contributor path.** The console installers are the first-class way to use
Watchman (README Quickstart + **⚙ Settings**); this page is for running the engine directly —
development, scripting, headless boxes, or building the console from source.

## Run any lane against a bundled sample persona

No personal data required: the repo ships **fictional sample personas** so a fresh clone runs
immediately.

```bash
uv sync                                      # Python env + deps
cp .env.example .env                         # optional — every API key is independently optional

# a fictional ~$1M household:
uv run hn finance networth --pack samples/packs/demo-investor
uv run hn finance positions --pack samples/packs/demo-investor
uv run hn career openings   --pack samples/packs/demo-growth   # an early-career job hunter

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
**fundamentals** via SEC EDGAR) and need no setup. Lanes that call keyed providers (SerpAPI
flights/hotels/restaurant ratings + reviews, Alpaca market data, Ticketmaster events) only
activate when you add their key to `.env`.

### `HARNESS_SEC_CONTACT` — required for the filing reader

Verbs that fetch documents from the EDGAR **Archives** (`www.sec.gov`) — `hn finance filing` and
the fund N-PORT path — must send a contact address in the User-Agent per SEC fair-access policy.
Set it in `.env`:

```
HARNESS_SEC_CONTACT=you@example.com
```

- Unset, these verbs **fail loud** with instructions — there is no anonymous-UA fallback.
- Discovery calls (`fundamentals`, the submissions JSON) stay on the open `data.sec.gov` host and
  need no contact.

## Notable verbs

The full lane/verb surface is in the [README](../README.md); these are the flag shapes worth
knowing from the shell:

- **`hn finance filing SYM [--list] [--form 8-K] [--accession N] [--doc FILE] [--json]`** — read a
  filing's content from SEC EDGAR. Default: the newest 8-K's press-release exhibit, resolved via
  the filing's document table (filename backstop when untyped). `--list` shows the filing's
  document inventory with types; `--accession` re-aims at an older filing; `--doc` reads a
  specific document from it. Requires `HARNESS_SEC_CONTACT` (above).
- **`hn finance gauges SYM [--json]`** — options-positioning gauges from the Alpaca Options API
  (free indicative feed): put/call ratio (session volume + open interest), IV30 (median implied
  vol, 20-45d out, near-the-money), HV30 (trailing realized vol, broker-card convention), and the
  IV−HV spread with a braced/neutral/complacent read. Sentiment thermometers, not signals — the
  output names its own boundaries (IV coverage, OI lag, and that short interest isn't available
  from this provider). Requires Alpaca keys.
- **`hn finance scorecards [--json]`** — the graded earnings-print registry
  (`finance/reference/print-scorecards.yaml` in your corpus): every print you've graded as a
  grade-bannered, newest-first list; the data behind the console's Finance ▸ Prints tab. The
  registry is appended by your operating loop at grade time — the verb (and the tab) only read it.
- **`hn finance projections [--json]`** — scenario-grid risk/reward bands per name per horizon
  (6/12/24/36 months) from the params registry you author (`finance/reference/projection-params.yaml`:
  forward EPS + a growth band + a multiple band + provenance per name) against the live quote. Horizons
  past a year are marked `sketch`; params older than ~a quarter flag `stale`. The data behind the
  console's Finance ▸ Projections tab — and a lens, never a forecast (the disclaimer ships in the JSON).
- **`hn finance holdings [--json]`** — the held book appraised through the same params: for every
  held name with an entry, the modeled 12-month return at the live price AND at your average cost
  ("how good was the entry?"). Names without an entry render with the reason named, never dropped.
  The data behind the Finance ▸ Holdings tab.
- **`hn finance gates [--horizon N] [--json]`** — upcoming held-name earnings prints (confirmed dates
  where the analyst calendar has them, filing-cadence estimates labeled `est` otherwise) plus your
  `macro_events` inside the horizon, imminent-first. No quotes, no wire — the fast calendar read
  behind the Finance ▸ Plans tab's gates board.
- **`hn travel calendar --from YYYY-MM-DD --to YYYY-MM-DD [--city C] [--variant grid|big]
  [--json]`** — the reference almanac + live ticketed events merged into per-day buckets; the data
  behind the console's Calendar tab and the static `calendar` SVG. Live events use
  `TICKETMASTER_KEY`; without it the calendar renders almanac-only and the subtitle says so.
  `--variant big` is the one-month wall-board layout.
- **`hn travel nudge [--weeks N] [--top N] [--no-live] [--include-taken] [--json]`** — proactive
  trip nudges: scores upcoming weekends × your destinations against the corpus nudge registry
  (`travel/weights.yaml destination_nudge:`) and surfaces the top pairs with the reason assembled
  from the scored components. Scoring is corpus + almanac and keyless; `--live` (on by default) adds
  free-tier Ticketmaster events and the keyless Open-Meteo forecast for in-horizon windows —
  `--no-live` skips it. The data behind the Planning tab's nudge cards.
- **Conditions** (`hn travel weather` / `hn travel pulse`) — the forecast carries feels-like, UV,
  gust, and sunrise/sunset fields; pulse heat alerts are feels-like-aware, with `uv` and `wind`
  flag kinds (thresholds `conditions.thresholds.uv` / `.gusts_mph` in `travel/weights.yaml`). The
  hourly **outdoor-windows** solver prints a plan line ("best outdoor window 07:00–12:00 · avoid
  12:00–19:00") using comfort bounds from the user overlay — `travel.global_settings.outdoor`:
  `feels_min_f` / `feels_max_f` / `precip_prob_max` / `gusts_max_mph` / `day_start_hour` /
  `day_end_hour`.
- **`hn travel food --near PLACE --yelp [--yelp-query LENS]`** — the optional reviews tier: merges Yelp review
  snippets, neighborhoods, and Yelp's own rating (kept separate from Google's — divergence is
  signal) onto the eatery list. Needs `SERPAPI_KEY` and spends one search per run (day-cached);
  the keyless OSM tier works without it.

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
npm run tauri dev          # needs Rust (rustup) + platform build tools
```

**Linux** needs the Tauri v2 system libraries before `npm run tauri dev`/`build`
(Debian/Ubuntu: `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libayatana-appindicator3-dev
libsoup-3.0-dev patchelf`; the tray icon wants an appindicator extension on GNOME).
