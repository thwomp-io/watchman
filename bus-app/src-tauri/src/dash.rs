//! Domain dashboards — DD/APM-style observability pages, config-not-code.
//! A dashboard is a per-lane JSON file (`~/.config/harness/dashboards/<lane>.json`) holding a
//! grid of widgets; a widget = source × kind × layout. The webview NEVER passes commands —
//! `run_widget(lane, id)` resolves the source from config Rust-side (least-privilege held; the
//! surfaces discipline, one ring up). Bus-type sources are resolved webview-side via the
//! existing list_events command.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum WidgetSource {
    /// A JSON-emitting read-only command (the surfaces engine).
    Command { cmd: String, args: Vec<String>, cwd: String },
    /// A vault file (tracker-relative; containment-checked on read).
    File { path: String },
    /// Bus events query — resolved webview-side through list_events.
    Bus {
        #[serde(default)]
        lane: String,
        #[serde(default)]
        limit: Option<u32>,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Widget {
    pub id: String,
    pub title: String,
    /// stat | table | viz | feed
    pub kind: String,
    pub source: WidgetSource,
    /// manual | market10m | local60s
    #[serde(default = "default_refresh")]
    pub refresh: String,
    #[serde(default = "default_span")]
    pub span: u8,
    /// dot-path pluck into the source JSON before rendering (stat/table)
    #[serde(default)]
    pub value_path: Option<String>,
    #[serde(default)]
    pub prefix: Option<String>,
    #[serde(default)]
    pub suffix: Option<String>,
    /// stat-tile sign formatting opt-out (types.ts `signed?: boolean`): %-suffixed
    /// stats sign-format by default; `signed: false` = a magnitude read (e.g. UNWIND % COMPLETE).
    /// Added after the desktop app DROPPED the key its typed parse didn't know while
    /// the python-served console passed it through — the two-parsers divergence class.
    #[serde(default)]
    pub signed: Option<bool>,
    /// optional dot-path into the source JSON whose value is appended to the title, accented — e.g.
    /// the resolved home city on the weather widget, so the title reflects the loaded persona's home
    /// instead of a hardcoded place. The frontend renders `{title} · {value}` with the value amber.
    #[serde(default)]
    pub title_path: Option<String>,
    /// parameterized widgets: selectable values substituted into the source args as {symbol}
    #[serde(default)]
    pub symbols: Vec<String>,
    /// table widgets: explicit column subset/order (else first-8 auto-derived). Keeps the
    /// directional columns (day move, distance) in view instead of static fields past the cap.
    #[serde(default)]
    pub columns: Vec<String>,
    /// grid-row span — a tall panel (e.g. a centerpiece matrix/viz). Default 1; the frontend adds a
    /// `rows-N` class and the CSS spans the row + fills the tile's height (scrollable). `doc_series`
    /// is tall by its own CSS; this generalizes tallness to any widget.
    #[serde(default = "default_rows")]
    pub rows: u8,
    /// Explicit grid placement (Dashboard Studio) — None = legacy span/rows flow placement.
    #[serde(default)]
    pub layout: Option<Layout>,
}

fn default_refresh() -> String {
    "manual".into()
}
fn default_span() -> u8 {
    2
}
fn default_rows() -> u8 {
    1
}

/// Explicit grid placement (Dashboard Studio) — grid units, not pixels: `x` =
/// column start (0-based), `y` = row start, `w`/`h` = spans. OPTIONAL by construction: a widget
/// without `layout` renders via the legacy span/rows dense-flow path, so every pre-Studio config
/// keeps working untouched; the first unlock+save persists computed positions. Skipping
/// `Option<Layout>` here would silently drop the key on the desktop while the python-served
/// console passes it through — the two-parsers class (cf. `signed`); the
/// kitchen-sink round-trip test pins all three surfaces.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Layout {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
}

fn default_owner() -> String {
    "default".into()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Dashboard {
    pub lane: String,
    pub title: String,
    /// Nav grouping — dashboards sharing a group render as subtabs under it (e.g. Finance ▸
    /// [Core · Unwind · Market]). Empty = ungrouped top-level. `#[serde(default)]` keeps old
    /// user configs valid (they just land ungrouped until re-seeded).
    #[serde(default)]
    pub group: String,
    /// Ownership metadata (Dashboard Studio): "default" = seeded from a compiled default (safe to
    /// re-seed/migrate); "user" = user-authored or user-edited via the Studio — NEVER overwritten
    /// by seeding or deploys (the fix). Old configs deserialize as "default".
    #[serde(default = "default_owner")]
    pub owner: String,
    pub widgets: Vec<Widget>,
}

fn dash_dir() -> PathBuf {
    // Keyed off harness_home (HARNESS_HOME || $HOME) so a sandboxed test instance reads/seeds an
    // isolated dashboards dir, never the dev daily-driver's ~/.config/harness/dashboards.
    crate::config::harness_home().join(".config/harness/dashboards")
}

fn hn_cmd(args: &[&str]) -> WidgetSource {
    let mut full: Vec<String> = vec!["run".into(), "hn".into()];
    full.extend(args.iter().map(|a| a.to_string()));
    WidgetSource::Command { cmd: "uv".into(), args: full, cwd: ".".into() }
}

fn w(
    id: &str, title: &str, kind: &str, source: WidgetSource, refresh: &str, span: u8,
    value_path: Option<&str>, prefix: Option<&str>, suffix: Option<&str>,
) -> Widget {
    Widget {
        id: id.into(),
        title: title.into(),
        kind: kind.into(),
        source,
        refresh: refresh.into(),
        span,
        value_path: value_path.map(Into::into),
        prefix: prefix.map(Into::into),
        suffix: suffix.map(Into::into),
        signed: None,
        title_path: None,
        symbols: Vec::new(),
        columns: Vec::new(),
        rows: 1,
        layout: None,
    }
}

fn cols(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

/// The default finance dashboard — every source already exists in the toolkit.
fn default_finance() -> Dashboard {
    Dashboard {
        lane: "finance".into(),
        title: "Core".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            w("networth", "Net worth", "stat",
              hn_cmd(&["finance", "networth", "--json"]), "market10m", 1,
              Some("total"), Some("$"), None),
            w("proxy", "Fund proxy (est)", "stat",
              hn_cmd(&["finance", "fund-proxy", "--json"]), "market10m", 1,
              Some("estimate_pct"), None, Some("%")),
            Widget { columns: cols(&["symbol", "price", "prev_close", "day_change_pct", "day_change"]),
                ..w("indexes", "Indexes", "table",
                  hn_cmd(&["finance", "pulse", "--json"]), "market10m", 2,
                  Some("indexes"), None, None) },
            w("trend", "Net worth trend", "viz",
              WidgetSource::File { path: "finance/networth-history.json".into() },
              "local60s", 2, None, None, None),
            Widget { columns: cols(&["symbol", "day_change_pct", "day_gl", "price", "market_value"]),
                ..w("daymoves", "Day moves", "table",
                  hn_cmd(&["finance", "watch", "--no-mark", "--json"]), "market10m", 2,
                  Some("day_moves"), None, None) },
            Widget { columns: cols(&["symbol", "side", "qty", "limit", "price", "distance_pct", "day_pct"]),
                ..w("traps", "Open traps", "table",
                  hn_cmd(&["finance", "pulse", "--json"]), "market10m", 2,
                  Some("orders"), None, None) },
            w("concentration", "Concentration (LIVE)", "viz",
              hn_cmd(&["finance", "concentration", "--json"]), "market10m", 2,
              None, None, None),
            // Allocation current→target — sibling net-worth portrait. The `pies` shape
            // sniffs to the Donut twin; Current is derived live, Target from the allocation_target:
            // config. Self-refreshes on market10m like concentration — no hand-render, never stale.
            w("allocation", "Allocation (current → target)", "viz",
              hn_cmd(&["finance", "allocation", "--json"]), "market10m", 2,
              None, None, None),
            w("signals", "Signals", "feed",
              WidgetSource::Bus { lane: "finance".into(), limit: Some(12) },
              "local60s", 2, None, None, None),
            w("prints", "Upcoming prints", "table",
              hn_cmd(&["finance", "watch", "--no-mark", "--json"]), "market10m", 2,
              Some("prints"), None, None),
            Widget {
                // Neutral, liquid market examples — the compiled default is the no-pack fallback,
                // where the portfolio seed is a neutral template; a user's real symbols come from
                // their pack/corpus dashboards, never this list (generalize-first).
                symbols: ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
                    .iter().map(|s| s.to_string()).collect(),
                ..w("chart", "Position chart — bars + support levels", "viz",
                    hn_cmd(&["finance", "bars", "{symbol}", "--days", "120", "--viz"]),
                    "manual", 4, None, None, None)
            },
        ],
    }
}

// ─── The Travel group — 5 subtabs (tracker facelift) ────────────────────────────────────────────
// Parity with Finance/Career: the old single flat "Command-center" becomes a grouped console.
// Subtab + group order both follow load_all()'s lane-alphabetical sort (there's no order field), so
// the lanes are deliberately prefixed `travel-*`: that keeps the Travel GROUP in its existing slot
// (all five sort together, after the finance/career lanes) AND lands Planning (lane `travel`) first,
// with Calendar/Conditions/Landscape/Visits after it. v1 composes existing `hn travel` verbs + the
// shape-sniffed File-viz (compare radar, matrix) + doc_series; the four missing interactive viz twins
// (calendar grid · weather-strip · schedule · map) + the dynamic active-trip radar resolver are filed
// fast-follows. Determinism contract holds: deterministic data widgets + agent-written doc_series,
// never a model in the render loop.

/// PLANNING — the outbound operator board (lane stays `travel` so the bus signals feed is unchanged).
/// The trip-pipeline spine + the active decision's candidate radar + signals.
fn default_travel() -> Dashboard {
    Dashboard {
        lane: "travel".into(),
        title: "Planning".into(),
        group: "Travel".into(),
        owner: default_owner(),
        widgets: vec![
            w("active", "Active trips", "stat",
              hn_cmd(&["travel", "trips", "--json"]), "local60s", 1,
              Some("count"), None, None),
            // The marquee decision viz — the active trip's compare radar, read off disk (shape-sniffs
            // to a radar from {axes,candidates}). v1 points at a sample trip's decision; a
            // dynamic active-trip resolver (so it never goes stale — the single-trip-staleness guard)
            // is the filed fast-follow.
            // span-2 × rows-2 = a square footprint so the radar (square viewBox) fits without the
            // bottom axes clipping (a wide span-3/rows-1 tile cut the lower half off).
            Widget { rows: 2,
                ..w("decision", "Active decision — candidate radar", "viz",
                  WidgetSource::File {
                      path: "travel/trips/sample-trip/visuals/compare-data.json".into(),
                  }, "local60s", 2, None, None, None) },
            // `doc` renders as an "open ↗" VAULT deep-link (the trip's corpus folder-note).
            Widget { columns: cols(&["when", "date", "status", "destination", "anchor", "who", "doc"]),
                ..w("pipeline", "Trip pipeline — the horizon", "table",
                  hn_cmd(&["travel", "trips", "--kind", "trip", "--json"]), "local60s", 4,
                  Some("trips"), None, None) },
            w("signals", "Travel signals", "feed",
              WidgetSource::Bus { lane: "travel".into(), limit: Some(12) },
              "local60s", 2, None, None, None),
        ],
    }
}

/// LANDSCAPE — the whole researched field at a glance (the travel analog of the finance bench-map /
/// career hiring-map). The `matrix` File-viz over the existing meta-ranking data twin (destinations
/// × characteristic axes) + the how-to-read narrative browsed off disk. Pure compose. (A destinations
/// `map` twin is a filed fast-follow.)
fn default_landscape() -> Dashboard {
    Dashboard {
        lane: "travel-landscape".into(),
        title: "Landscape".into(),
        group: "Travel".into(),
        owner: default_owner(),
        widgets: vec![
            Widget { rows: 3,
                ..w("matrix", "Destination characteristics — the whole corpus", "viz",
                  WidgetSource::File {
                      path: "travel/destinations/_meta/meta-ranking.json".into(),
                  }, "local60s", 4, None, None, None) },
            w("meta", "Meta-ranking — how to read it", "doc_series",
              WidgetSource::File { path: "travel/destinations/_meta".into() }, "local60s", 2,
              None, None, None),
        ],
    }
}

/// CONDITIONS — the live environmental senses + the agent-written conditions-watchman narrative.
/// Home-base weather + local air/smoke (the wildfire-season check) as deterministic tables, beside the
/// `conditions/reports/` doc_series (the determinism-contract narrative panel, off disk). A
/// `weather-strip` viz twin is a filed fast-follow.
fn default_conditions() -> Dashboard {
    Dashboard {
        lane: "travel-conditions".into(),
        title: "Conditions".into(),
        group: "Travel".into(),
        owner: default_owner(),
        widgets: vec![
            // No hardcoded city: `weather` defaults to the active weights' `conditions.home`
            // (pack-aware); `title_path` surfaces the resolved place in the title.
            Widget { title_path: Some("location".into()),
                ..w("weather", "Home-base weather — 7d", "table",
                  hn_cmd(&["travel", "weather", "--from", "{today}", "--to", "{today+6}", "--json"]),
                  "local60s", 2, Some("days"), None, None) },
            Widget { title_path: Some("location".into()),
                columns: cols(&["date", "us_aqi_max", "pm2_5_max", "category"]),
                ..w("air", "Air quality / wildfire smoke", "table",
                  hn_cmd(&["travel", "air", "--from", "{today}", "--to", "{today+4}", "--json"]),
                  "local60s", 2, Some("days"), None, None) },
            // the agent-written conditions-watchman reports — newest-first, browsed off disk
            w("reports", "Conditions watchman — reports", "doc_series",
              WidgetSource::File { path: "travel/conditions/reports".into() }, "local60s", 2,
              None, None, None),
        ],
    }
}

/// CALENDAR — the proactive "when to go" almanac: long-weekends + centerpiece home-team games +
/// recurring key dates + mega-events over the next 180 days (`hn travel reference`). v1 is the almanac
/// TABLE; the headline `calendar` 12-month grid viz needs an interactive twin (filed fast-follow).
fn default_calendar() -> Dashboard {
    Dashboard {
        lane: "travel-calendar".into(),
        title: "Calendar".into(),
        group: "Travel".into(),
        owner: default_owner(),
        widgets: vec![
            Widget { columns: cols(&["local_date", "name", "segment", "tier"]),
                ..w("keydates", "Key dates & sports — next 180d", "table",
                  hn_cmd(&["travel", "reference", "--from", "{today}", "--to", "{today+180}", "--json"]),
                  "manual", 4, None, None, None) },
            // Live ticketed events near home — each row's TM `url` auto-renders as a "post ↗" purchase
            // link (the flat events shape). `manual` refresh (TM events don't move minute-to-minute).
            Widget { columns: cols(&["local_date", "name", "segment", "venue", "url"]),
                ..w("whatson", "What's on near home — next 30 ticketed (45d)", "table",
                  hn_cmd(&["travel", "events", "--from", "{today}", "--to", "{today+45}", "--flat", "--limit", "30", "--json"]),
                  "manual", 4, None, None, None) },
        ],
    }
}

/// VISITS — the inbound/hosting mode (someone visits home), distinct from outbound trips. v1 is the
/// visits-only pipeline (`trips --kind visit`); the `schedule`/`schedule-bank` planner viz twins that
/// make this tab sing are the filed fast-follow.
fn default_visits() -> Dashboard {
    Dashboard {
        lane: "travel-visits".into(),
        title: "Visits".into(),
        group: "Travel".into(),
        owner: default_owner(),
        widgets: vec![
            Widget { columns: cols(&["when", "date", "status", "destination", "anchor", "who", "doc"]),
                ..w("inbound", "Inbound visits — who's coming", "table",
                  hn_cmd(&["travel", "trips", "--kind", "visit", "--json"]), "local60s", 4,
                  Some("trips"), None, None) },
        ],
    }
}

/// The concentration-unwind dashboard — sell-down planning for any concentrated
/// position with grant/vest mechanics (renders from whatever the corpus's `unwind:` lane
/// configures; sample packs supply fictional personas). Every widget shares ONE source
/// (`hn finance unwind --json`), so the dashboard's in-flight dedupe collapses the whole tab to a
/// single subprocess run; each widget just `value_path`s into the contract.
fn default_unwind() -> Dashboard {
    let src = || hn_cmd(&["finance", "unwind", "--json"]);
    Dashboard {
        lane: "unwind".into(),
        title: "Unwind".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            Widget {
                signed: Some(false),  // % COMPLETE is a magnitude, not a delta — no "+"
                ..w("unwind_pct", "Unwind complete", "stat", src(), "market10m", 1,
                    Some("progress.pct_unwound"), None, Some("%"))
            },
            w("price", "Price", "stat", src(), "market10m", 1, Some("price"), Some("$"), None),
            w("day", "Day", "stat", src(), "market10m", 1, Some("day_change_pct"), None, Some("%")),
            w("gl", "Unrealized G/L", "stat", src(), "market10m", 1,
              Some("position.unrealized_gl_pct"), None, Some("%")),
            w("harvest", "Harvestable loss", "stat", src(), "market10m", 1,
              Some("tlh.harvestable_loss"), Some("$"), None),
            w("vests", "Vest calendar — sell-planning timeline", "viz", src(), "market10m", 4,
              Some("vest_timeline"), None, None),
            w("price_chart", "Price vs cost-basis band", "viz", src(), "market10m", 4,
              Some("price_chart"), None, None),
            w("lot_split", "Lots — gain (anytime) vs loss (wash-gated)", "viz", src(), "market10m", 2,
              Some("lot_split"), None, None),
            w("tlh_split", "Sellability split", "viz", src(), "market10m", 2,
              Some("tlh_split"), None, None),
            w("ladder", "Lot ladder", "table", src(), "market10m", 4, Some("lots"), None, None),
        ],
    }
}

/// The MARKET dashboard — a bird's-eye regime page. Data widgets share ONE source
/// (`hn finance market --json`, market10m), so the in-flight dedupe collapses them to a single
/// subprocess run; each plucks its own sub-shape. The `take` widget is a doc-series browser over
/// `finance/market/takes/` (newest-first, prev/next) — the agent-written narrative panel, read off
/// disk so the dashboard never calls a model.
fn default_market() -> Dashboard {
    let src = || hn_cmd(&["finance", "market", "--json"]);
    Dashboard {
        lane: "market".into(),
        title: "Market".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            w("breadth_ewc", "Breadth · RSP − SPY", "stat", src(), "market10m", 1,
              Some("breadth.equal_weight_minus_cap_pct"), None, Some("%")),
            w("mag7_avg", "Mag7 avg", "stat", src(), "market10m", 1,
              Some("breadth.megacap_avg_pct"), None, Some("%")),
            // the browsable take panel — right rail; doc-series self-fetches (bypasses the run pipeline)
            w("take", "Market take", "doc_series",
              WidgetSource::File { path: "finance/market/takes".into() }, "local60s", 2,
              None, None, None),
            w("semis_avg", "Semis avg", "stat", src(), "market10m", 1,
              Some("breadth.semis_avg_pct"), None, Some("%")),
            w("sectors_adv", "Sectors ▲", "stat", src(), "market10m", 1,
              Some("breadth.sectors_advancing"), None, None),
            Widget { columns: cols(&["symbol", "label", "day_change_pct", "day_change", "price"]),
                ..w("indices", "Indexes", "table", src(), "market10m", 2, Some("indices"), None, None) },
            Widget { columns: cols(&["symbol", "label", "day_change_pct", "price"]),
                ..w("sectors", "Sectors", "table", src(), "market10m", 2, Some("sectors"), None, None) },
            Widget { columns: cols(&["symbol", "label", "day_change_pct", "price"]),
                ..w("megacap", "Mega-cap (Mag7)", "table", src(), "market10m", 2,
                  Some("megacap"), None, None) },
            Widget { columns: cols(&["symbol", "label", "day_change_pct", "price"]),
                ..w("semis", "Semis", "table", src(), "market10m", 2, Some("semis"), None, None) },
            Widget { columns: cols(&["symbol", "label", "group", "day_change_pct"]),
                ..w("leaders", "Leaders", "table", src(), "market10m", 1, Some("leaders"), None, None) },
            Widget { columns: cols(&["symbol", "label", "group", "day_change_pct"]),
                ..w("laggards", "Laggards", "table", src(), "market10m", 1,
                  Some("laggards"), None, None) },
        ],
    }
}

/// The NEWS dashboard — a Finance ▸ News subtab: the broad-market RSS wire as a
/// "one pane of glass" reader. One full-width master-detail widget (kind `news`): a scan list on the
/// left (time, source, headline; newest-first) and the selected item's summary on the right, plus an
/// open-original link. Data is `hn finance wire --json` (feeds.yaml: mainstream market wires
/// plus world-news feeds) — the SAME determinism contract as the rest of DASH: a `--json` verb on a refresh
/// cadence, NO model and NO standing agent in the loop (display-only by design). The
/// reader renders the `summary` field the wire verb captures (content:encoded else description).
fn default_news() -> Dashboard {
    Dashboard {
        lane: "news".into(),
        title: "News".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            // full-width (span 4) + tall (rows 3); the `news` kind renders the whole master-detail
            // internally off the wire JSON. local30m = a gentle auto-refresh (news isn't market-tick
            // data; re-polling 9 feeds every 60s would be wasteful — 30 min matches "every few hours").
            Widget {
                rows: 3,
                ..w("reader", "Broad-market wire", "news",
                  hn_cmd(&["finance", "wire", "--json"]), "local30m", 4, None, None, None)
            },
        ],
    }
}

/// The TICKETS dashboard — the execution-ticket surface, the Market-take pattern applied
/// to trade tickets. The `ticket` widget is a doc-series browser over `finance/execution/` (newest-
/// first, prev/next) — the agent-written ticket rendered off disk, no model in the loop — sat beside
/// the supplemental data that matters when you execute: the **resting GTC orders** with live distance-
/// to-fill, and the book (positions + net worth). Data widgets share sources → in-flight dedupe.
fn default_tickets() -> Dashboard {
    let positions = || hn_cmd(&["finance", "positions", "--json"]);
    let pulse = || hn_cmd(&["finance", "pulse", "--json"]);
    Dashboard {
        lane: "tickets".into(),
        title: "Tickets".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            // the browsable ticket panel — the centerpiece; doc-series self-fetches (bypasses the run pipeline)
            w("ticket", "Execution ticket", "doc_series",
              WidgetSource::File { path: "finance/execution".into() }, "local60s", 2, None, None, None),
            // resting GTC orders w/ live distance-to-fill — the supplemental that matters mid-execution
            Widget { columns: cols(&["symbol", "side", "qty", "limit", "price", "distance_pct"]),
                ..w("orders", "Resting GTC orders", "table", pulse(), "market10m", 2,
                  Some("orders"), None, None) },
            // the trap-map — per-symbol GTC price ladders (live price + rungs +
            // support shelves): the strategy read beside the raw table. Replaces the earlier
            // nw/live stat tiles, which were a weak use of the space.
            w("trapmap", "Trap map — GTC ladders", "viz",
              hn_cmd(&["finance", "trap-map", "--json"]), "market10m", 2, None, None, None),
            // the book you're trading against
            Widget { columns: cols(&["symbol", "market_value", "day_change_pct", "unrealized_gl_pct"]),
                ..w("positions", "Positions", "table", positions(), "market10m", 2,
                  Some("positions"), None, None) },
        ],
    }
}

/// The COMPARE dashboard — a side-by-side pick-set surface, the doc-series pattern
/// (Market-take / Tickets) applied to stock comparisons. The `comparison` widget browses the
/// agent-written narrative in `finance/research/compares/` (newest-first, prev/next — the *why*, read
/// off disk, no model in the loop); the `metrics` table is the deterministic `hn finance compare`
/// data (valuation × price × screen). The default pick-set is a sample sleeve —
/// EDIT `~/.config/harness/dashboards/compare.json` to compare a different set (multi-select param
/// is a queued follow-up).
fn default_compare() -> Dashboard {
    Dashboard {
        lane: "compare".into(),
        title: "Compare".into(),
        group: "Finance".into(),
        owner: default_owner(),
        widgets: vec![
            // the browsable comparison narrative — the centerpiece; doc-series self-fetches
            w("comparison", "Comparison", "doc_series",
              WidgetSource::File { path: "finance/research/compares".into() }, "local60s", 2,
              None, None, None),
            // the deterministic side-by-side — valuation × price × screen for the standing pick-set
            Widget { columns: cols(&[
                "symbol", "price", "day_change_pct", "ps", "pe", "ev_ebitda", "market_cap", "screen",
            ]),
                ..w("metrics", "Metrics — valuation × price × screen", "table",
                  hn_cmd(&["finance", "compare", "AAPL", "MSFT", "COST", "NVDA", "--json"]),
                  "market10m", 2, Some("rows"), None, None) },
        ],
    }
}

/// The CAREER dashboard — the role-hunt operator board. The shortlist tiles
/// + table share ONE source (`hn career shortlist --json`), so the in-flight dedupe collapses them to
/// a single subprocess; each plucks its own sub-shape. Shortlist reads the LATEST persisted scan twin
/// (LOCAL, no board scan on refresh — refreshing the data is the deliberate `hn career openings
/// --write` action), so `manual` refresh is right (the hunt isn't real-time). The `scans` widget is a
/// doc-series browser over `role-hunt/discoveries/` (newest-first; list_vault_dir shows only the .md
/// reports — the .json twins + visuals/ are filtered out) — the agent-written scan reports off disk.
fn default_career() -> Dashboard {
    let shortlist = || hn_cmd(&["career", "shortlist", "--json"]);
    Dashboard {
        lane: "career".into(),
        title: "Board".into(),
        group: "Career".into(),
        owner: default_owner(),
        widgets: vec![
            w("openings", "Matched openings", "stat", shortlist(), "manual", 1,
              Some("summary.total"), None, None),
            w("leadership", "Leadership", "stat", shortlist(), "manual", 1,
              Some("summary.leadership"), None, None),
            w("ic_infra", "IC / infra", "stat", shortlist(), "manual", 1,
              Some("summary.ic_infra"), None, None),
            // shortlist (half) ‖ market-shape bar (half) — the actionable list + its at-a-glance shape.
            Widget { columns: cols(&["company", "tier", "shape", "title", "salary", "url"]),
                ..w("shortlist", "Shortlist — high-priority roles", "table", shortlist(), "manual", 2,
                  Some("roles"), None, None) },
            w("shape_bar", "Openings by role shape", "viz", shortlist(), "manual", 2,
              Some("shape_bar"), None, None),
            // pipeline full-width so the tall bottom pair (scans ‖ hiring-map) aligns cleanly.
            Widget { columns: cols(&["company", "role", "stage", "next_step", "updated"]),
                ..w("pipeline", "Pipeline — applications & inbound", "table",
                  hn_cmd(&["career", "applications", "--json"]), "manual", 4,
                  Some("applications"), None, None) },
            // the tall bottom band: the browsable scan reports (half) ‖ the Hiring Map centerpiece
            // (half, full-height + scrollable). Both rows=3 so they sit aligned at the bottom.
            w("scans", "Openings scans", "doc_series",
              WidgetSource::File { path: "role-hunt/discoveries".into() }, "local60s", 2,
              None, None, None),
            Widget { rows: 3,
                ..w("matrix", "Hiring map — company × role shape", "viz", shortlist(), "manual", 2,
                  Some("matrix"), None, None) },
        ],
    }
}

/// The BACKLOG dashboard — the beads coordination bus made browsable: counts, the
/// P1 band, the presence board (in_progress = which agent is on what), an honest ready queue, and
/// shipped-this-week. Read-only over the tracker's `.beads/issues.jsonl` PASSIVE export — the
/// export's age rides the Open tile's title (liveness≠freshness: the board must never perform
/// more currency than its file has). State changes stay with `bd`; this is the operator's glance
/// surface (the ask: browse bead details without asking an agent — full descriptions ride RAW).
fn default_backlog() -> Dashboard {
    let src = || hn_cmd(&["beads", "board", "--json"]);
    Dashboard {
        lane: "backlog".into(),
        title: "Backlog".into(),
        group: "Ops".into(),
        owner: default_owner(),
        widgets: vec![
            Widget { title_path: Some("exported_ago".into()),
                ..w("open", "Open · export", "stat", src(), "local60s", 1, Some("open"), None, None) },
            w("inprog", "In progress", "stat", src(), "local60s", 1, Some("in_progress"), None, None),
            w("p1", "P1 open", "stat", src(), "local60s", 1, Some("p1_count"), None, None),
            w("shipped", "Closed · 7d", "stat", src(), "local60s", 1, Some("closed_7d"), None, None),
            // the family tree — active/recent beads as an org chart (parent-child structure +
            // a blocks-dep overlay); hover a block for metadata + the open-ticket link
            Widget { rows: 3,
                ..w("tree", "Beads board — active + recently shipped", "viz", src(), "local60s", 4,
                  Some("tree"), None, None) },
            Widget { columns: cols(&["id", "assignee", "priority", "title", "ticket"]),
                ..w("presence", "In progress — the presence board", "table", src(), "local60s", 2,
                  Some("presence"), None, None) },
            Widget { columns: cols(&["id", "priority", "type", "title", "labels", "ticket"]),
                ..w("p1s", "P1 — open", "table", src(), "local60s", 2,
                  Some("p1_open"), None, None) },
            Widget { columns: cols(&["id", "priority", "type", "title", "labels", "updated", "ticket"]),
                ..w("ready", "Ready — unblocked, undeferred", "table", src(), "local60s", 4,
                  Some("ready"), None, None) },
            Widget { columns: cols(&["id", "title", "labels", "updated", "ticket"]),
                ..w("shipped7", "Shipped this week", "table", src(), "local60s", 4,
                  Some("shipped_7d"), None, None) },
        ],
    }
}

fn compiled_defaults() -> Vec<(&'static str, Dashboard)> {
    vec![
        ("backlog", default_backlog()),
        ("finance", default_finance()),
        ("travel", default_travel()),
        ("travel-landscape", default_landscape()),
        ("travel-conditions", default_conditions()),
        ("travel-calendar", default_calendar()),
        ("travel-visits", default_visits()),
        ("unwind", default_unwind()),
        ("market", default_market()),
        ("news", default_news()),
        ("tickets", default_tickets()),
        ("compare", default_compare()),
        ("career", default_career()),
    ]
}

/// Read every `Dashboard` JSON in a directory (non-recursive). Unparseable files are skipped — a
/// malformed pack dashboard drops that one tab, never the whole console.
fn read_dashboards_dir(dir: &Path) -> Vec<Dashboard> {
    let mut out: Vec<Dashboard> = Vec::new();
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            if e.path().extension().is_some_and(|x| x == "json") {
                if let Ok(text) = fs::read_to_string(e.path()) {
                    if let Ok(d) = serde_json::from_str::<Dashboard>(&text) {
                        out.push(d);
                    }
                }
            }
        }
    }
    out
}

/// Load every dashboard config from disk, then seed any compiled default whose lane isn't already
/// present (fresh machine / newly-added lane). Existing user configs are never overwritten — the
/// disk file is the override, the compiled default is the portable source-of-truth.
pub fn load_all() -> Vec<Dashboard> {
    let dir = dash_dir();
    let _ = fs::create_dir_all(&dir);
    let mut out = read_dashboards_dir(&dir);
    for (name, d) in compiled_defaults() {
        if !out.iter().any(|x| x.lane == d.lane) {
            let _ = fs::write(
                dir.join(format!("{name}.json")),
                serde_json::to_string_pretty(&d).unwrap_or_default(),
            );
            out.push(d);
        }
    }
    out.sort_by(|a, b| a.lane.cmp(&b.lane));
    out
}

/// Persist a Studio-edited dashboard (checkpoint C). Stamps `owner: "user"` — a
/// Studio-saved dashboard is user-owned from that moment on and is never reseeded/migrated over.
pub fn save_dashboard(d: &Dashboard) -> Result<(), String> {
    save_dashboard_to(&dash_dir(), d)
}

/// Pure-core half of the save (testable without env mutation — the `harness_home_from` pattern).
/// Atomic temp-then-rename per the config.rs precedent: an in-place truncate-and-write leaves a
/// zeroed lane file if the process dies mid-save, which reads as a corrupt (skipped) tab on the
/// next launch. The lane becomes the filename, so its charset is validated — no traversal.
/// EVERY save first banks the file being replaced into `.backups/<lane>-<epoch>.json` (pruned to
/// the newest 10 per lane) — the data-integrity layer: a bad edit, a migration you regret, or
/// a future bug is always one file-copy from undone.
/// The FIRST backup a lane ever gets is its pre-Studio legacy config, for free.
pub fn save_dashboard_to(dir: &Path, d: &Dashboard) -> Result<(), String> {
    let lane_ok = !d.lane.is_empty()
        && d.lane.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if !lane_ok {
        return Err(format!("invalid lane '{}' — lowercase letters, digits, hyphens only", d.lane));
    }
    let mut owned = d.clone();
    owned.owner = "user".into();
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("{}.json", owned.lane));
    backup_existing(dir, &owned.lane, &path);
    let body = serde_json::to_string_pretty(&owned).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, body).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

/// Snap a lane back to its compiled built-in default (Dashboard Studio "return to default").
/// The current on-disk state is BANKED to .backups/ first — the reset itself is undoable, same
/// as any save. Lanes without a compiled default (a future user-created tab) get an honest error;
/// their known-good states live in .backups/. Returns the fresh default so the caller can swap
/// state in place. The written file carries owner:"default" — after a reset the dashboard is a
/// stock default again (future built-in migrations may touch it; a later Studio edit re-claims it).
pub fn reset_dashboard(lane: &str) -> Result<Dashboard, String> {
    reset_dashboard_to(&dash_dir(), lane)
}

pub fn reset_dashboard_to(dir: &Path, lane: &str) -> Result<Dashboard, String> {
    let (_, d) = compiled_defaults()
        .into_iter()
        .find(|(_, d)| d.lane == lane)
        .ok_or_else(|| format!("no built-in default for '{lane}' — its history is in .backups/"))?;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("{lane}.json"));
    backup_existing(dir, lane, &path);
    let body = serde_json::to_string_pretty(&d).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, body).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    Ok(d)
}

/// Bank the current on-disk lane file before it's replaced; prune to the newest 10 per lane.
/// Best-effort by design — a failed backup must never block the save itself (the save is atomic
/// regardless); epoch-seconds filenames sort chronologically with zero dependencies.
fn backup_existing(dir: &Path, lane: &str, path: &Path) {
    if !path.exists() {
        return;
    }
    let backups = dir.join(".backups");
    let _ = fs::create_dir_all(&backups);
    let epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|t| t.as_secs())
        .unwrap_or(0);
    let _ = fs::copy(path, backups.join(format!("{lane}-{epoch:011}.json")));
    // prune: newest 10 per lane (lexical sort == chronological, thanks to the zero-pad)
    if let Ok(entries) = fs::read_dir(&backups) {
        let prefix = format!("{lane}-");
        let mut mine: Vec<PathBuf> = entries
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with(&prefix) && n.ends_with(".json"))
            })
            .collect();
        mine.sort();
        while mine.len() > 10 {
            let _ = fs::remove_file(mine.remove(0));
        }
    }
}

/// Load dashboards for the active scenario (pack-DESCRIBED dashboards v2). When a weight
/// pack is active AND ships a non-empty `dashboards/` dir, those dashboards **fully replace** the
/// console tab-set — the persona curates its own console (full-set override): a demo pack can drop
/// tabs a persona doesn't need (e.g. Unwind, Compare) and ship its own chart symbols instead of inheriting the
/// real defaults. Read **transiently** — pack dashboards are NEVER seeded into
/// `~/.config/harness/dashboards/` (a file there, once written, overrides forever and would poison
/// the real console — the config-override-forever trap). No pack, a pack without a
/// `dashboards/` dir, or a dir with no parseable layouts → `load_all()`'s behavior exactly (disk
/// configs + compiled-default seeding), so the real console and existing data-only packs are
/// untouched. Same opt-in shape as the data loader: provide it and the pack owns it; omit it and
/// nothing changes.
pub fn load_all_for(active_pack: Option<&Path>) -> Vec<Dashboard> {
    if let Some(pack_dir) = active_pack {
        let dboards = pack_dir.join("dashboards");
        if dboards.is_dir() {
            let mut out = read_dashboards_dir(&dboards);
            if !out.is_empty() {
                out.sort_by(|a, b| a.lane.cmp(&b.lane));
                return out;
            }
        }
    }
    load_all()
}

/// Resolve a widget by (lane, id) within the active scenario's dashboards — pack-described when a
/// pack supplies them, the real console otherwise — so `run_widget` resolves the pack's OWN widget
/// definitions (its chart symbols, its sources), not the compiled defaults.
pub fn find_widget_for(active_pack: Option<&Path>, lane: &str, id: &str) -> Option<Widget> {
    load_all_for(active_pack)
        .into_iter()
        .find(|d| d.lane == lane)
        .and_then(|d| d.widgets.into_iter().find(|w| w.id == id))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// THE CONFIG-SCHEMA CONTRACT TEST — kills the two-parsers
    /// divergence class mechanically: typed serde DROPS unknown keys silently (the `signed` key
    /// vanished on desktop for 3 days while the python-served console passed it through). This
    /// kitchen-sink JSON carries EVERY schema field; the round-trip asserts none are dropped.
    /// Adding a field to dashboards JSON? It goes in dash.rs + types.ts + this fixture, one commit.
    /// (Python's side is a raw-json passthrough pinned by tests/test_console_api.py's twin.)
    #[test]
    fn kitchen_sink_widget_survives_the_typed_round_trip() {
        let kitchen_sink = r#"{
            "lane": "contract", "title": "Kitchen Sink", "group": "Test", "owner": "user",
            "widgets": [{
                "id": "everything", "title": "Every field", "kind": "stat",
                "source": {"type": "command", "cmd": "uv", "args": ["run"], "cwd": "~"},
                "refresh": "market10m", "span": 3, "value_path": "a.b", "prefix": "$",
                "suffix": "%", "signed": false, "title_path": "c.d", "symbols": ["AAA"],
                "columns": ["x"], "rows": 2, "layout": {"x": 1, "y": 2, "w": 3, "h": 4}
            }]
        }"#;
        let d: Dashboard = serde_json::from_str(kitchen_sink).unwrap();
        let back: serde_json::Value =
            serde_json::from_str(&serde_json::to_string(&d).unwrap()).unwrap();
        assert_eq!(back["owner"], "user", "Dashboard.owner must round-trip");
        let w = &back["widgets"][0];
        assert_eq!(w["signed"], false, "signed must round-trip (the dropped-unknown-key regression)");
        assert_eq!(w["layout"]["x"], 1, "layout.x must round-trip");
        assert_eq!(w["layout"]["h"], 4, "layout.h must round-trip");
        assert_eq!(w["rows"], 2);
        assert_eq!(w["columns"][0], "x");
        // legacy config (no layout/owner) still parses, with honest defaults
        let legacy: Dashboard = serde_json::from_str(
            r#"{"lane":"old","title":"Old","widgets":[]}"#).unwrap();
        assert_eq!(legacy.owner, "default");
    }

    /// Studio save: stamps owner:"user", writes atomically, validates the lane
    /// (it becomes the filename — traversal must be impossible). Hermetic temp dir.
    #[test]
    fn save_dashboard_stamps_user_owner_and_rejects_bad_lanes() {
        let dir = std::env::temp_dir().join(format!("harness-studio-save-{}", std::process::id()));
        let d = Dashboard {
            lane: "my-board".into(),
            title: "Mine".into(),
            group: "Finance".into(),
            owner: default_owner(), // saving flips it to "user"
            widgets: vec![],
        };
        save_dashboard_to(&dir, &d).unwrap();
        let saved: Dashboard =
            serde_json::from_str(&fs::read_to_string(dir.join("my-board.json")).unwrap()).unwrap();
        assert_eq!(saved.owner, "user", "a Studio save stamps user ownership");
        assert!(!dir.join("my-board.json.tmp").exists(), "temp file renamed away (atomic)");

        for bad in ["../evil", "Evil", "a b", ""] {
            let mut e = d.clone();
            e.lane = bad.into();
            assert!(save_dashboard_to(&dir, &e).is_err(), "lane '{bad}' must be rejected");
        }
        let _ = fs::remove_dir_all(&dir);
    }

    /// Reset: snaps to the compiled default, banks the replaced state first, honest error for
    /// lanes with no built-in.
    #[test]
    fn reset_dashboard_restores_the_compiled_default_and_banks_the_old_state() {
        let dir = std::env::temp_dir().join(format!("harness-studio-reset-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        // a user-customized finance.json on disk
        let mut custom = default_finance();
        custom.owner = "user".into();
        custom.widgets.truncate(2);
        fs::write(dir.join("x"), "").err(); // ensure parent creation is exercised via save
        save_dashboard_to(&dir, &custom).unwrap();

        let fresh = reset_dashboard_to(&dir, "finance").unwrap();
        assert_eq!(fresh.owner, "default");
        assert!(fresh.widgets.len() > 2, "the full compiled default came back");
        let on_disk: Dashboard =
            serde_json::from_str(&fs::read_to_string(dir.join("finance.json")).unwrap()).unwrap();
        assert_eq!(on_disk.owner, "default");
        assert_eq!(on_disk.widgets.len(), fresh.widgets.len());
        // the customized state was banked before the reset overwrote it
        let banked = fs::read_dir(dir.join(".backups")).unwrap().count();
        assert!(banked >= 1, "reset banks the replaced state");
        // honest error for a lane with no compiled default
        assert!(reset_dashboard_to(&dir, "no-such-lane").is_err());
        let _ = fs::remove_dir_all(&dir);
    }

    /// Every save banks the replaced file into .backups/ (the data-integrity layer: a bad
    /// drag/edit is always one file-copy from undone) — the first backup a lane gets is its
    /// pre-Studio legacy config.
    #[test]
    fn save_dashboard_banks_a_backup_of_the_replaced_file() {
        let dir = std::env::temp_dir().join(format!("harness-studio-bak-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let d = Dashboard {
            lane: "board".into(),
            title: "B".into(),
            group: String::new(),
            owner: default_owner(),
            widgets: vec![],
        };
        save_dashboard_to(&dir, &d).unwrap(); // first save: nothing to bank
        let backups = dir.join(".backups");
        assert!(!backups.exists() || fs::read_dir(&backups).unwrap().count() == 0);
        save_dashboard_to(&dir, &d).unwrap(); // second save: banks the first
        let count = fs::read_dir(&backups).unwrap().count();
        assert_eq!(count, 1, "the replaced file is banked");
        let bak = fs::read_dir(&backups).unwrap().next().unwrap().unwrap().path();
        let banked: Dashboard = serde_json::from_str(&fs::read_to_string(bak).unwrap()).unwrap();
        assert_eq!(banked.lane, "board");
        let _ = fs::remove_dir_all(&dir);
    }

    /// The v2 invariant: a pack shipping a non-empty `dashboards/` dir FULLY overrides the console
    /// tab-set (the persona curates its own console) — `load_all_for` returns exactly the pack's
    /// dashboards, never the compiled defaults, and `find_widget_for` resolves the pack's own widgets.
    /// Hermetic: a throwaway temp dir, never `~/.config`.
    #[test]
    fn pack_dashboards_full_override() {
        let base = std::env::temp_dir().join(format!("harness-dash-v2-{}", std::process::id()));
        let dboards = base.join("dashboards");
        fs::create_dir_all(&dboards).unwrap();
        let only = Dashboard {
            lane: "finance".into(),
            title: "Pack Core".into(),
            group: "Finance".into(),
            owner: default_owner(),
            widgets: vec![w(
                "nw", "Net worth", "stat",
                hn_cmd(&["finance", "networth", "--json"]), "market10m", 1,
                Some("total"), Some("$"), None,
            )],
        };
        fs::write(dboards.join("finance.json"), serde_json::to_string_pretty(&only).unwrap()).unwrap();

        let loaded = load_all_for(Some(&base));
        assert_eq!(loaded.len(), 1, "pack dashboards fully override the compiled defaults");
        assert_eq!(loaded[0].lane, "finance");
        assert_eq!(loaded[0].title, "Pack Core");

        let widget = find_widget_for(Some(&base), "finance", "nw");
        assert_eq!(widget.map(|w| w.title), Some("Net worth".to_string()),
                   "find_widget_for resolves the pack's own widget by id");

        let _ = fs::remove_dir_all(&base);
    }

    /// Swap the compiled defaults' chart/compare symbols for a pack's own FICTIONAL set: the
    /// position-chart's `symbols` list + the `compare` widget's ticker args (`… compare <SYMS> --json`).
    /// Everything else in the defaults is generic (`value_path` plucks) and ships unchanged.
    fn retarget(d: &mut Dashboard, chart: &[&str], compare: &[&str]) {
        for widget in &mut d.widgets {
            if !widget.symbols.is_empty() {
                widget.symbols = chart.iter().map(|s| s.to_string()).collect();
            }
            if let WidgetSource::Command { args, .. } = &mut widget.source {
                if let (Some(start), Some(end)) =
                    (args.iter().position(|a| a == "compare"), args.iter().position(|a| a == "--json"))
                {
                    if end > start + 1 {
                        let mut rebuilt = args[..=start].to_vec();
                        rebuilt.extend(compare.iter().map(|s| s.to_string()));
                        rebuilt.extend_from_slice(&args[end..]);
                        *args = rebuilt;
                    }
                }
            }
        }
    }

    /// MAINTAINER TOOL (not a CI test — `#[ignore]`d): regenerate each bundled demo pack's full
    /// `dashboards/` console from `compiled_defaults()`, retargeted to FICTIONAL symbols. Keeps the
    /// sample-pack consoles faithful to the real compiled defaults (every tab) while ensuring a loaded
    /// demo pack never inherits the compiled-default chart/compare symbols. The persona swaps the WHOLE
    /// console (pack full-override v2), so all tabs must be present — `test_sample_packs.py` guards it.
    /// Run: `cargo test emit_pack_dashboards -- --ignored --nocapture`.
    #[test]
    #[ignore = "maintainer tool: regenerates sample-pack dashboards from compiled defaults"]
    fn emit_pack_dashboards() {
        // Per-pack fictional symbol sets — aligned with each pack's portfolio.yaml holdings/watchlist;
        // broadly-diversified household names, illustrative only.
        let packs: [(&str, &[&str], &[&str]); 4] = [
            ("demo-investor",
             &["AAPL", "MSFT", "COST", "VTI", "VXUS", "BND", "SCHD"],
             &["AAPL", "MSFT", "COST", "NVDA"]),
            ("demo-growth",
             &["NVDA", "CRWD", "DDOG", "NET", "SHOP", "TTD", "MDB"],
             &["NVDA", "CRWD", "DDOG", "SHOP"]),
            ("early-retiree",
             &["VTI", "SCHD", "VYM", "JNJ", "PG", "KO", "JPM"],
             &["JNJ", "PG", "KO", "JPM"]),
            ("college-grad",
             &["VTI", "VXUS", "NVDA", "DDOG", "SCHD", "VOO", "SPY"],
             &["VTI", "VXUS", "NVDA", "DDOG"]),
        ];
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../samples/packs");
        for (name, chart, compare) in packs {
            let dir = root.join(name).join("dashboards");
            fs::create_dir_all(&dir).unwrap();
            for (lane, mut d) in compiled_defaults() {
                retarget(&mut d, chart, compare);
                let json = serde_json::to_string_pretty(&d).unwrap();
                fs::write(dir.join(format!("{lane}.json")), format!("{json}\n")).unwrap();
                eprintln!("emit: {name}/dashboards/{lane}.json");
            }
        }
    }

    /// A pack dir WITHOUT a `dashboards/` subdir falls through to the real console (`load_all`), so a
    /// data-only pack is unaffected by v2.
    #[test]
    fn pack_without_dashboards_dir_falls_through() {
        let base = std::env::temp_dir().join(format!("harness-dash-v2-nodir-{}", std::process::id()));
        fs::create_dir_all(&base).unwrap();
        // No `dashboards/` subdir → load_all_for delegates to load_all(), which always yields the
        // compiled-default lanes (finance among them).
        let loaded = load_all_for(Some(&base));
        assert!(loaded.iter().any(|d| d.lane == "finance"),
                "a pack with no dashboards/ dir keeps the real console");
        let _ = fs::remove_dir_all(&base);
    }
}
