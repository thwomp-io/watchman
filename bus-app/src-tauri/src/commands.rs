//! Webview-invokable commands — thin over bus.rs/config.rs. The app's only world-touching
//! action is `run_producer`, which re-runs a CONFIG-registered read-only `hn` command
//! (read-rich/execute-gated: a refresh is a re-run, never an execution).

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::Serialize;
use tauri::{AppHandle, Runtime};

use crate::{backend, bus, config, dash, poller, viz};

/// Build a console-spawned tool Command with the shared spawn environment (augmented PATH +
/// the corpus/state seal — see [`run_json_command`] for the sealing rationale).
///
/// `uv run` invocations get the engine project pinned explicitly (`--project`): discovering the
/// project from the working directory is launch-context fragile, and an installed console has no
/// repo checkout at all — there the engine resolves to the copy bundled in the app resources,
/// `--frozen` against its shipped lockfile, with the venv redirected to the user-writable state
/// dir (`UV_PROJECT_ENVIRONMENT`). uv creates that venv on first use.
pub(crate) fn tool_command(cmd: &str, args: &[String], cwd: &str) -> Command {
    let mut command = Command::new(cmd);
    let mut args = args.to_vec();
    if cmd == "uv" && args.first().map(String::as_str) == Some("run") {
        if let Some(engine) = config::engine() {
            let mut pin = vec!["--project".to_string(), engine.project_dir.display().to_string()];
            if engine.bundled {
                // --no-dev must match the warm-up sync exactly: `uv run` defaults to the dev
                // group, and a flag mismatch makes the first widget spawn re-sync the venv.
                pin.splice(0..0, ["--frozen".to_string(), "--no-dev".to_string()]);
                command.env("UV_PROJECT_ENVIRONMENT", config::engine_venv_dir());
            }
            args.splice(1..1, pin);
        }
    }
    // Console-subsystem children (uv/python) must not flash a console window per spawn
    // (CREATE_NO_WINDOW) — self-refreshing dashboards spawn constantly.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .args(&args)
        .current_dir(config::resolve_cwd(cwd))
        .env("PATH", config::augmented_path())
        // Piped spawns on Windows give Python a legacy-codepage stdio (cp1252); the engine
        // emits UTF-8 glyphs (≈, →) in JSON labels, and one unencodable char fails the whole
        // command. UTF-8 mode makes stdio encoding platform-independent (harmless elsewhere).
        .env("PYTHONUTF8", "1")
        .env("TRACKER_PATH", config::harness_home().join("projects/corpus"))
        .env("HARNESS_STATE_DIR", config::harness_home().join(".local/state/harness"));
    // Demo-pack full seal: while a BUNDLED sample persona is active, every spawn — widget,
    // live viz, producer — reads the pack and nothing else. TRACKER_PATH points at the pack
    // itself, so a lane the pack doesn't provide resolves empty instead of falling back to a
    // real corpus that may exist on this machine. Personal packs keep lane-fallback semantics
    // (applied per-widget in run_json_command, never to producers).
    if let Some(pack) = config::active_bundled_pack(&config::load()) {
        let seal_state = config::demo_seal_state_dir();
        command
            .env("TRACKER_PATH", &pack)
            .env("WEIGHTS_PACK", &pack)
            .env("HARNESS_STATE_DIR", &seal_state)
            .env("HARNESS_BUS_DB", seal_state.join("bus.db"));
    }
    command
}

/// Spawn-failure text the widget error zones render. uv missing gets an actionable message —
/// it's the console's one runtime prerequisite.
pub(crate) fn spawn_error(cmd: &str, e: &std::io::Error) -> String {
    if e.kind() == std::io::ErrorKind::NotFound && cmd == "uv" {
        return "uv not found — the console's one prerequisite. Install it from \
                https://docs.astral.sh/uv/ and restart Watchman."
            .into();
    }
    format!("{cmd} failed to start: {e}")
}

#[tauri::command]
pub fn list_events(
    unread_only: bool,
    lane: Option<String>,
    kind: Option<String>,
    limit: Option<i64>,
) -> Result<Vec<bus::Event>, String> {
    let handle = backend::open(&config::load())?;
    handle.list_events(unread_only, lane.as_deref(), kind.as_deref(), limit.unwrap_or(100))
}

#[tauri::command]
pub fn ack_events<R: Runtime>(app: AppHandle<R>, ids: Vec<i64>) -> Result<usize, String> {
    let handle = backend::open(&config::load())?;
    let n = handle.ack(&ids)?;
    drop(handle);
    poller::poll_once(&app); // badge/menu reflect the ack immediately
    Ok(n)
}

#[tauri::command]
pub fn unread_count() -> Result<i64, String> {
    backend::open(&config::load())?.unread_count()
}

#[tauri::command]
pub fn urgent_unread_count() -> Result<i64, String> {
    backend::open(&config::load())?.urgent_unread_count()
}

#[tauri::command]
pub fn distinct_meta() -> Result<bus::DistinctMeta, String> {
    backend::open(&config::load())?.distinct_meta()
}

/// The running binary's version — `env!` reads `CARGO_PKG_VERSION` at COMPILE time, so it is
/// always exactly the version that built this binary (Cargo.toml = the single source of truth;
/// it cannot drift from what's actually running, unlike a value read from a separate file).
#[tauri::command]
pub fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[tauri::command]
pub fn get_config() -> Result<serde_json::Value, String> {
    let cfg = config::load();
    // bus_source: what the Inbox is actually reading — the remote URL in bus_url mode, else the
    // local db path. Surfaced (token never included) so a remote-mode console is self-evident.
    let (mode, bus_source) = match config::bus_endpoint(&cfg) {
        config::BusEndpoint::Remote { url, .. } => ("remote", format!("remote: {url}")),
        config::BusEndpoint::Local(path) => ("local", path.to_string_lossy().into_owned()),
    };
    // The Settings panel's read model. `bus_url` is the CONFIGURED value (may differ from the
    // effective mode when a bundled demo pack seals the console local); the token is NEVER
    // returned — only whether one is set.
    Ok(serde_json::json!({
        "db_path": config::db_path(&cfg).to_string_lossy(),
        "bus_source": bus_source,
        "producers": cfg.producers,
        "mode": mode,
        "bus_url": cfg.bus_url.as_deref().map(str::trim).filter(|s| !s.is_empty()),
        "bus_token_set": cfg.bus_token.as_deref().map(str::trim).is_some_and(|s| !s.is_empty()),
        "active_pack": cfg.active_pack,
        "tracker_path": config::tracker_path(&cfg).to_string_lossy(),
        "surfaces": cfg.surfaces,
        "live_viz": cfg.live_viz,
        "config_path": config::config_file().to_string_lossy(),
    }))
}

/// The user overlay (harness.yaml) as RAW TEXT for the Settings panel's Personal tabs — the
/// webview parses (js-yaml); Rust stays yaml-dep-free. Precedence mirrors the engine: an active
/// pack's config/harness.yaml > the tracker-resident file. Read-only — the file is the interface.
#[tauri::command]
pub fn get_user_overlay() -> Result<serde_json::Value, String> {
    let cfg = config::load();
    let tracker = config::tracker_path(&cfg).join("config").join("harness.yaml");
    let pack_copy = cfg.active_pack.as_deref().map(str::trim).filter(|s| !s.is_empty())
        .map(|p| Path::new(p).join("config").join("harness.yaml"));
    let path = match pack_copy {
        Some(p) if p.is_file() => p,
        _ => tracker,
    };
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    Ok(serde_json::json!({
        "text": text,
        "source": if text.is_empty() { "none (scaffold <tracker>/config/harness.yaml)" } else { "user overlay" },
        "path": path.to_string_lossy(),
    }))
}

/// Set (or clear) the remote-bus connection — the Settings panel's "Connect to online bus" flow.
/// Field-allowlisted write (the set_active_pack pattern): never a raw config patch. Enabling
/// remote AUTO-CLEARS the active pack — a fresh install's seeded demo pack silently overrides
/// `bus_url` (a fresh install's seeded demo pack silently overrides a remote config); the one
/// flow that enables remote kills it at the source.
#[tauri::command]
pub fn set_bus_config(url: Option<String>, token: Option<String>) -> Result<serde_json::Value, String> {
    let mut cfg = config::load();
    let url = url.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let token = token.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    match url {
        Some(u) => {
            if !(u.starts_with("http://") || u.starts_with("https://")) {
                return Err("bus URL must start with http:// or https://".into());
            }
            let effective_token = token.or_else(|| {
                cfg.bus_token.as_deref().map(str::trim).map(String::from).filter(|s| !s.is_empty())
            });
            if effective_token.is_none() {
                return Err("a bearer token is required for a remote bus (the server's bus-token value)".into());
            }
            cfg.bus_url = Some(u);
            cfg.bus_token = effective_token;
            cfg.active_pack = None; // the footgun kill: a seeded demo pack would silently override remote
        }
        None => {
            // clearing the URL returns the console to local mode; the token goes with it
            cfg.bus_url = None;
            cfg.bus_token = None;
        }
    }
    config::save(&cfg)?;
    get_config()
}

/// Probe a remote bus BEFORE saving it — hits the served stats endpoint with the given bearer.
/// Returns a human line ("ok — N unread") so the Settings panel can show the result inline.
#[tauri::command]
pub fn test_bus_connection(url: String, token: String) -> Result<String, String> {
    let remote = crate::remote::RemoteBus::new(url.trim().to_string(), token.trim().to_string())?;
    let unread = remote.unread_count()?;
    Ok(format!("ok — bus reachable, {unread} unread"))
}

#[tauri::command]
pub fn run_producer<R: Runtime>(app: AppHandle<R>, id: String) -> Result<String, String> {
    let cfg = config::load();
    let producer = cfg
        .producers
        .iter()
        .find(|p| p.id == id)
        .ok_or_else(|| format!("unknown producer: {id}"))?
        .clone();
    let out = tool_command(&producer.cmd, &producer.args, &producer.cwd)
        .output()
        .map_err(|e| spawn_error(&producer.cmd, &e))?;
    poller::poll_once(&app); // deliver anything the run just published
    Ok(format!(
        "{} → exit {}",
        producer.label,
        out.status.code().map(|c| c.to_string()).unwrap_or_else(|| "?".into())
    ))
}

/// Substitute {today} / {today+N} arg tokens at spawn time (surfaces need live dates a static
/// config can't express). Date math via the same epoch-civil conversion bus.rs uses. Range covers
/// up to a year ahead so planning-horizon windows work (e.g. the travel key-dates almanac uses
/// {today}..{today+180}); the closing brace in each token prevents +1 matching inside +15.
fn substitute_dates(args: &[String]) -> Vec<String> {
    args.iter().map(|a| {
        let mut out = a.clone();
        if out.contains("{today") {
            for n in 0..=400 {
                let token = if n == 0 { "{today}".to_string() } else { format!("{{today+{n}}}") };
                if out.contains(&token) {
                    out = out.replace(&token, &crate::bus::civil_date_offset(n as i64));
                }
            }
        }
        out
    }).collect()
}

#[tauri::command]
pub fn list_surfaces() -> Result<Vec<config::Surface>, String> {
    Ok(config::load().surfaces)
}

#[tauri::command]
pub async fn run_surface(id: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = config::load();
        let surface = cfg
            .surfaces
            .iter()
            .find(|s| s.id == id)
            .ok_or_else(|| format!("unknown surface: {id}"))?
            .clone();
        run_json_command(&surface.cmd, &surface.args, &surface.cwd, &surface.label)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Spawn a registered read-only command and return its (validated) JSON stdout — the shared
/// engine behind surfaces AND live viz. A registered command MUST emit JSON; config errors
/// surface loudly, never as blank panels.
fn run_json_command(cmd: &str, args: &[String], cwd: &str, label: &str) -> Result<String, String> {
    let args = substitute_dates(args);
    let cfg = config::load();
    // tool_command seals the corpus to harness_home: the spawned `hn` (Python) does NOT honor
    // HARNESS_HOME (it's a Rust concept), so without the seal it falls back to the REAL
    // ~/projects/corpus even in a sandbox — the "Real data"/no-pack path then reads the real
    // corpus on a machine that has one. `hn` honors `TRACKER_PATH` (the one place every lane
    // resolves the corpus root), so it's set explicitly: a sandboxed instance reads its OWN
    // (absent → empty) corpus, the published app never reaches real data. Behavior-preserving in
    // dev (harness_home == $HOME). TRACKER_PATH is the only corpus-root env the Python side
    // honors; HARNESS_HOME is a Rust-side concept.
    let mut command = tool_command(cmd, &args, cwd);
    // Scenario-switcher: when a weight pack is active, every on-demand panel read (`hn … --json`)
    // renders that pack's data. `hn` honors WEIGHTS_PACK lane-by-lane (a pack only overrides the
    // lanes it provides; others fall back to the real corpus), so it's safe to set globally here.
    if let Some(pack) = cfg.active_pack.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        command.env("WEIGHTS_PACK", config::expand_home(pack));
    }
    let out = command.output().map_err(|e| spawn_error(cmd, &e))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("{label} exited {:?}: {}", out.status.code(),
                           err.chars().take(800).collect::<String>()));
    }
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let trimmed = stdout.trim();
    serde_json::from_str::<serde_json::Value>(trimmed)
        .map_err(|e| format!("{label} did not emit valid JSON: {e}"))?;
    Ok(trimmed.to_string())
}

#[tauri::command]
pub fn list_dashboards() -> Result<Vec<dash::Dashboard>, String> {
    // Pack-described dashboards (v2): an active pack that ships a `dashboards/` dir owns the
    // whole tab-set (full-set override). No pack → the real console's compiled defaults.
    let cfg = config::load();
    let pack = cfg
        .active_pack
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(config::expand_home);
    Ok(dash::load_all_for(pack.as_deref()))
}

/// Dashboard Studio: persist a user-edited dashboard layout. Guarded two ways:
/// (1) while a BUNDLED demo pack is active, dashboards are pack-described TRANSIENTS — persisting
/// one would write the demo layout into ~/.config and poison the real console forever (the
/// config-override-forever trap), so the save is rejected (the frontend also hides the unlock
/// toggle under a demo pack; this is the belt to that suspender). (2) dash-side lane validation
/// (the lane becomes the filename). Ownership stamping happens in dash::save_dashboard.
#[tauri::command]
pub fn save_dashboard(dashboard: dash::Dashboard) -> Result<(), String> {
    let cfg = config::load();
    if config::active_bundled_pack(&cfg).is_some() {
        return Err("a demo pack is active — its dashboards are transient and cannot be saved".into());
    }
    dash::save_dashboard(&dashboard)
}

/// Dashboard Studio "return to default": snap a lane back to its compiled built-in. Same demo-pack
/// guard as save (pack dashboards are transients); the replaced state is banked to .backups/ first.
#[tauri::command]
pub fn reset_dashboard(lane: String) -> Result<dash::Dashboard, String> {
    let cfg = config::load();
    if config::active_bundled_pack(&cfg).is_some() {
        return Err("a demo pack is active — its dashboards are transient and cannot be reset".into());
    }
    dash::reset_dashboard(&lane)
}

/// A bundled sample weight pack — the scenario-switcher's choices.
#[derive(Serialize)]
pub struct PackInfo {
    pub name: String,
    pub path: String,
    pub lanes: Vec<String>,
}

/// List the bundled sample weight packs (dirs under samples/packs/ holding a pack.yaml). `lanes` is
/// inferred from which lane subdirs exist — enough to label the choice without a YAML parser.
#[tauri::command]
pub fn list_packs() -> Result<Vec<PackInfo>, String> {
    let dir = config::samples_packs_dir();
    let mut out: Vec<PackInfo> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for entry in entries.flatten() {
            let p = entry.path();
            if !p.join("pack.yaml").is_file() {
                continue;
            }
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
            let lanes = ["finance", "travel", "career"]
                .iter()
                .filter(|lane| p.join(lane).is_dir())
                .map(|lane| lane.to_string())
                .collect();
            out.push(PackInfo { name, path: p.to_string_lossy().to_string(), lanes });
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

/// The active weight pack (a dir path), or None for the real corpus.
#[tauri::command]
pub fn get_active_pack() -> Option<String> {
    config::load().active_pack
}

/// Set (or clear, with None/blank) the active weight pack; persists across launches. The webview
/// re-renders the data zones after this so panels pick up the swap.
#[tauri::command]
pub fn set_active_pack(pack: Option<String>) -> Result<(), String> {
    let mut cfg = config::load();
    cfg.active_pack = pack.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    config::save(&cfg)
}

/// Run a dashboard widget's source — resolved from CONFIG by (lane, id); the webview never
/// passes commands or paths (least privilege, the surfaces discipline).
#[tauri::command]
pub async fn run_widget(
    lane: String, id: String, symbol: Option<String>,
) -> Result<String, String> {
    // Resolve the active pack BEFORE the blocking closure: a pack-described dashboard's widgets are
    // the pack's own (its chart symbols, its sources), so the lookup must consult the active scenario.
    let pack = config::load()
        .active_pack
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(config::expand_home);
    // spawn_blocking: subprocess work must NEVER sit on the main thread — sync Tauri commands
    // run on the event loop, and nine widgets queueing there beachballed the whole window
    // on first load. Async + blocking pool = true widget concurrency.
    tauri::async_runtime::spawn_blocking(move || {
        let widget = dash::find_widget_for(pack.as_deref(), &lane, &id)
            .ok_or_else(|| format!("unknown widget: {lane}/{id}"))?;
        // parameterized widgets: the symbol must be one the CONFIG declares — the webview
        // selects from a closed list, it never injects arguments
        let sym = match symbol {
            Some(s) if widget.symbols.iter().any(|w| w == &s) => Some(s),
            Some(s) => return Err(format!("symbol {s} not in widget config")),
            None => None,
        };
        match &widget.source {
            dash::WidgetSource::Command { cmd, args, cwd } => {
                let args: Vec<String> = args
                    .iter()
                    .map(|a| match &sym {
                        Some(s) => a.replace("{symbol}", s),
                        None => a.clone(),
                    })
                    .collect();
                run_json_command(cmd, &args, cwd, &widget.title)
            }
            dash::WidgetSource::File { path } => read_vault_file(path),
            dash::WidgetSource::Bus { .. } => Err("bus sources resolve webview-side".into()),
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Containment-checked tracker-relative read (shared by viz file entries + dashboard file
/// sources). Read-only, always.
/// Resolve a vault-relative path to a real, canonicalized path that is GUARANTEED inside the vault
/// (the containment check shared by every vault read — text, viz, and images). Symlinks/`..` that
/// would escape are rejected here.
/// Map a tracker-relative path to its active-pack base when a weight pack is loaded AND provides the
/// lane that path belongs to (leading segment: `finance/`/`travel/`/`role-hunt/`; the pack's `career/`
/// IS the role-hunt root, so the infix is dropped). Returns `(lane_dir, rel_within_lane)`, or None to
/// use the real vault. This is what makes File/dir dashboard sources (net-worth trend, openings scan)
/// follow the loaded persona instead of reading the user's real corpus.
fn active_pack_base(cfg: &config::AppConfig, path: &str) -> Option<(PathBuf, String)> {
    let pack = cfg.active_pack.as_deref().map(str::trim).filter(|s| !s.is_empty())?;
    let pack_dir = config::expand_home(pack);
    let (lane, rest) = if let Some(r) = path.strip_prefix("finance/") {
        ("finance", r)
    } else if let Some(r) = path.strip_prefix("travel/") {
        ("travel", r)
    } else if let Some(r) = path.strip_prefix("role-hunt/") {
        ("career", r)
    } else {
        return None;
    };
    let lane_dir = pack_dir.join(lane);
    // Only when the pack ACTUALLY provides the lane. Once it does, reads stay in the pack even if a
    // file is absent (a missing pack file errors / lists empty — it never leaks the real corpus).
    lane_dir.is_dir().then(|| (lane_dir, rest.to_string()))
}

fn resolve_vault_path(path: &str) -> Result<PathBuf, String> {
    let cfg = config::load();
    // vault_root, not tracker_path: while a bundled demo pack is active the fallback base is the
    // pack itself (demo-pack full seal) — a lane the pack lacks reads empty, never the real vault.
    let (base, rel) = match active_pack_base(&cfg, path) {
        Some(pair) => pair,
        None => (config::vault_root(&cfg), path.to_string()),
    };
    let canon = base.join(&rel).canonicalize().map_err(|e| format!("{path}: {e}"))?;
    let base_canon = base.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&base_canon) {
        return Err("path escapes the vault".into());
    }
    Ok(canon)
}

fn read_vault_file(path: &str) -> Result<String, String> {
    std::fs::read_to_string(resolve_vault_path(path)?).map_err(|e| format!("{path}: {e}"))
}

#[tauri::command]
pub fn list_viz() -> Result<Vec<viz::VizEntry>, String> {
    let cfg = config::load();
    let mut entries: Vec<viz::VizEntry> = cfg
        .live_viz
        .iter()
        .map(|lv| viz::VizEntry {
            path: format!("live:{}", lv.id),
            doc: format!("{} · LIVE", lv.lane),
            name: lv.label.clone(),
            viz_type: lv.viz_type.clone(),
            title: lv.label.clone(),
            supported: true,
        })
        .collect();
    entries.extend(viz::discover(&config::vault_root(&cfg)));
    Ok(entries)
}

#[tauri::command]
pub async fn read_viz(path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || read_viz_blocking(path))
        .await
        .map_err(|e| e.to_string())?
}

fn read_viz_blocking(path: String) -> Result<String, String> {
    let cfg = config::load();
    // live entries run their registered command — the data is derived fresh per read
    if let Some(id) = path.strip_prefix("live:") {
        let lv = cfg
            .live_viz
            .iter()
            .find(|l| l.id == id)
            .ok_or_else(|| format!("unknown live viz: {id}"))?;
        return run_json_command(&lv.cmd, &lv.args, &lv.cwd, &lv.label);
    }
    // vault-relative path from list_viz only (read-only, containment-checked)
    read_vault_file(&path)
}

// ————— VAULT zone: read-only corpus browser —————————————————————————————————————

/// Dirs the corpus browser never descends into (build/tooling noise). Any dotdir is skipped too.
/// `visuals/` stays out — those rendered diagram SVGs are the VIZ zone's domain; `assets/` (report
/// photos) IS browsable by design (operator ruling), so it's no longer skipped.
const VAULT_SKIP: &[&str] = &["node_modules", "__pycache__", "target", "dist", "screenshots", "tmp"];
const VAULT_MAX_DEPTH: usize = 8;
/// Image files the browser surfaces as standalone, viewable tree entries (Obsidian-style).
const IMG_EXT: &[&str] = &["jpg", "jpeg", "png", "gif", "webp", "avif", "svg"];

/// A browsable vault entry — a markdown doc OR a standalone image (the webview builds the tree).
#[derive(Serialize)]
pub struct VaultDoc {
    path: String,  // vault-relative (the read key)
    area: String,  // first path segment — the lane/area the tree groups by
    dir: String,   // containing dir, vault-relative — the sub-group within the area
    name: String,  // file stem
    title: String, // doc: first H1 line, else stem · image: the filename
    kind: String,  // "doc" (markdown) | "image"
}

fn walk_docs(dir: &Path, depth: usize, vault: &Path, out: &mut Vec<VaultDoc>) {
    if depth > VAULT_MAX_DEPTH {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        // never follow symlinks (the `memory` symlink escapes the vault; its canonical path would
        // fail the containment check — so a listed-but-unreadable entry is worse than absent)
        if entry.file_type().map(|ft| ft.is_symlink()).unwrap_or(true) {
            continue;
        }
        let p = entry.path();
        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if p.is_dir() {
            if !name.starts_with('.') && !VAULT_SKIP.contains(&name) {
                walk_docs(&p, depth + 1, vault, out);
            }
            continue;
        }
        let ext = p.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase);
        let is_md = ext.as_deref() == Some("md");
        let is_img = ext.as_deref().is_some_and(|e| IMG_EXT.contains(&e));
        if !is_md && !is_img {
            continue;
        }
        let rel = p.strip_prefix(vault).unwrap_or(&p).to_string_lossy().to_string();
        let area = rel.split('/').next().unwrap_or("").to_string();
        let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("?").to_string();
        let dir_rel = p
            .parent()
            .and_then(|d| d.strip_prefix(vault).ok())
            .map(|d| d.to_string_lossy().to_string())
            .unwrap_or_default();
        let (title, kind) = if is_md {
            let h1 = std::fs::read_to_string(&p)
                .ok()
                .and_then(|t| {
                    t.lines()
                        .find(|l| l.starts_with("# "))
                        .map(|l| l[2..].trim().to_string())
                })
                .filter(|t| !t.is_empty())
                .unwrap_or_else(|| stem.clone());
            (h1, "doc")
        } else {
            (name.to_string(), "image") // image label = the filename (with extension)
        };
        out.push(VaultDoc { path: rel, area, dir: dir_rel, name: stem, title, kind: kind.into() });
    }
}

#[tauri::command]
pub async fn list_vault_docs() -> Result<Vec<VaultDoc>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let cfg = config::load();
        let vault = config::vault_root(&cfg);
        let mut out = Vec::new();
        walk_docs(&vault, 0, &vault, &mut out);
        out.sort_by(|a, b| a.path.cmp(&b.path));
        out
    })
    .await
    .map_err(|e| e.to_string())
}

/// Read a vault markdown doc's raw text (containment-checked). The webview passes only a path
/// that came from `list_vault_docs` — read-only, the same guard `read_viz`/widgets use.
#[tauri::command]
pub async fn read_doc(path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || read_vault_file(&path))
        .await
        .map_err(|e| e.to_string())?
}

/// A markdown doc directly inside one vault dir — the doc-series widget's list item.
#[derive(Serialize)]
pub struct DirDoc {
    path: String,  // vault-relative read key (feed to read_doc)
    name: String,  // file stem
    title: String, // first H1, else stem
}

/// List the markdown files DIRECTLY inside a vault dir (non-recursive), NEWEST-FIRST by filename
/// (timestamped take files sort lexically → reverse = chronological). Containment-guarded; powers
/// the MARKET dashboard's browsable take-series panel. A missing dir is empty, not an error
/// (fresh machine before the first take is written).
#[tauri::command]
pub async fn list_vault_dir(path: String) -> Result<Vec<DirDoc>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Ok(dir) = resolve_vault_path(&path) else { return Ok(Vec::new()) };
        if !dir.is_dir() {
            return Ok(Vec::new());
        }
        let base = path.trim_end_matches('/');
        let mut out: Vec<DirDoc> = Vec::new();
        let Ok(entries) = std::fs::read_dir(&dir) else { return Ok(out) };
        for entry in entries.flatten() {
            if entry.file_type().map(|ft| ft.is_symlink()).unwrap_or(true) {
                continue;
            }
            let p = entry.path();
            if p.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref()
                != Some("md")
            {
                continue;
            }
            let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("?").to_string();
            // The doc's path stays tracker-relative (input dir + filename), NOT stripped against the
            // real vault — so a pack-resolved entry round-trips back through the pack-aware guard when
            // the webview reads it via read_doc. Same result as before for the real vault.
            let fname = p.file_name().and_then(|s| s.to_str()).unwrap_or("?");
            let rel = format!("{base}/{fname}");
            let title = std::fs::read_to_string(&p)
                .ok()
                .and_then(|t| {
                    t.lines().find(|l| l.starts_with("# ")).map(|l| l[2..].trim().to_string())
                })
                .filter(|t| !t.is_empty())
                .unwrap_or_else(|| stem.clone());
            out.push(DirDoc { path: rel, name: stem, title });
        }
        out.sort_by(|a, b| b.name.cmp(&a.name)); // newest-first (reverse lexical on timestamped names)
        Ok(out)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Cap on an inlined image — base64 rides the IPC + sits in the webview as a data URI, so refuse
/// absurd files rather than balloon memory. Report photos are well under this.
const MAX_IMAGE_BYTES: usize = 16 * 1024 * 1024;

/// Read a vault image (containment-checked) → a `data:<mime>;base64,…` URI the webview's <img> can
/// render directly. The text `read_doc` can't carry binary; this is the image sibling. Read-only.
#[tauri::command]
pub async fn read_image(path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let canon = resolve_vault_path(&path)?;
        let mime = match canon.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref()
        {
            Some("jpg") | Some("jpeg") => "image/jpeg",
            Some("png") => "image/png",
            Some("gif") => "image/gif",
            Some("webp") => "image/webp",
            Some("avif") => "image/avif",
            Some("svg") => "image/svg+xml",
            other => return Err(format!("unsupported image type: {}", other.unwrap_or("?"))),
        };
        let bytes = std::fs::read(&canon).map_err(|e| format!("{path}: {e}"))?;
        if bytes.len() > MAX_IMAGE_BYTES {
            return Err(format!("image too large ({} bytes)", bytes.len()));
        }
        Ok(format!("data:{mime};base64,{}", base64_encode(&bytes)))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Standard base64 (RFC 4648, padded) — kept inline to avoid a dependency for ~15 lines.
fn base64_encode(input: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(A[((n >> 18) & 63) as usize] as char);
        out.push(A[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 { A[((n >> 6) & 63) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { A[(n & 63) as usize] as char } else { '=' });
    }
    out
}

/// Tray "Refresh now": run every registered producer (fire-and-forget thread per producer).
pub fn refresh_all_producers<R: Runtime>(app: AppHandle<R>) {
    let cfg = config::load();
    for p in cfg.producers {
        let app = app.clone();
        std::thread::spawn(move || {
            let _ = tool_command(&p.cmd, &p.args, &p.cwd).output();
            poller::poll_once(&app);
        });
    }
}

#[cfg(test)]
mod tests {
    use super::base64_encode;

    #[test]
    fn base64_matches_rfc4648_vectors() {
        // the canonical progressive-padding vectors
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
        assert_eq!(base64_encode(b"foob"), "Zm9vYg==");
        assert_eq!(base64_encode(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64_encode(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn base64_handles_high_bytes() {
        assert_eq!(base64_encode(&[0xff, 0xfe, 0xfd]), "//79");
        assert_eq!(base64_encode(&[0x00]), "AA==");
    }
}
