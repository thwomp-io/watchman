//! The desktop shell: the window shows the engine's served console, the same page a browser or a
//! phone gets. Local mode (the default) starts `hn bus serve --console` from the engine on a private
//! loopback port with its own token and the console writes enabled, then opens it. Remote mode
//! (`bus_url` set) opens that served console instead. The shell keeps only what a browser can't
//! do: the tray, native notifications (poller.rs reads the bus directly), autostart, the folder
//! picker and the updater, exposed to the page as a few IPC commands that only the shell's own
//! local origin may call (capabilities/default.json).

use std::fs::OpenOptions;
use std::hash::{BuildHasher, Hasher};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime, State, Url};

use crate::config;

/// Writes the local server enables for its own window: the desktop app is the operator's console.
const LOCAL_WRITES: &str = "set_active_pack,save_dashboard,reset_dashboard,run_producer";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(300); // a fresh machine's first uv sync

#[derive(Default)]
pub struct Shell {
    child: Mutex<Option<Child>>,
    /// The console the window shows: (origin, token).
    target: Mutex<Option<(String, String)>>,
    #[cfg(not(debug_assertions))]
    pending_update: Mutex<Option<tauri_plugin_updater::Update>>,
}

/// 192 bits from the OS: std's RandomState seeds each hasher from the OS RNG, so three independent
/// hashers give unpredictable output without a new dependency.
fn random_token() -> String {
    (0..3)
        .map(|_| {
            let mut h = std::collections::hash_map::RandomState::new().build_hasher();
            h.write_u64(std::process::id() as u64);
            format!("{:016x}", h.finish())
        })
        .collect()
}

fn free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    listener.local_addr().map(|a| a.port()).map_err(|e| e.to_string())
}

fn state_dir() -> PathBuf {
    config::harness_home().join(".local/state/harness")
}

/// The built console UI the local server serves: this checkout's `bus-app/dist` when the engine is
/// a repo, else the copy bundled into the app resources.
fn ui_dir<R: Runtime>(app: &AppHandle<R>, engine: &config::Engine) -> Option<PathBuf> {
    let repo = engine.project_dir.join("bus-app/dist");
    if !engine.bundled && repo.join("index.html").is_file() {
        return Some(repo);
    }
    let bundled = app.path().resource_dir().ok()?.join("ui");
    bundled.join("index.html").is_file().then_some(bundled)
}

/// `uv run hn …` against the engine project, with the environment every console spawn had: the
/// augmented PATH, UTF-8 stdio, the corpus/state/cache defaults and the bundled packs.
fn engine_command(engine: &config::Engine, hn_args: &[String]) -> Command {
    let mut cmd = Command::new("uv");
    cmd.arg("run");
    if engine.bundled {
        // must match the startup warm-up sync exactly, or the first run re-syncs the venv
        cmd.args(["--frozen", "--no-dev"]).env("UV_PROJECT_ENVIRONMENT", config::engine_venv_dir());
    }
    cmd.arg("--project").arg(&engine.project_dir).arg("hn").args(hn_args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let cfg = config::load();
    cmd.env("PATH", config::augmented_path())
        .env("PYTHONUTF8", "1")
        .env("TRACKER_PATH", config::tracker_path(&cfg))
        .env("HARNESS_STATE_DIR", state_dir())
        .env("HARNESS_CACHE_DIR", config::harness_home().join(".cache/harness"))
        .env("HARNESS_PACKS_DIR", config::samples_packs_dir())
        // the server exits when this process does, even if the app is killed before its cleanup
        .env("HARNESS_PARENT_PID", std::process::id().to_string());
    cmd
}

fn wait_healthy(origin: &str, child: &mut Child) -> Result<(), String> {
    let deadline = Instant::now() + HEALTH_TIMEOUT;
    while Instant::now() < deadline {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("the engine exited ({status}) before serving"));
        }
        let probe = ureq::get(&format!("{origin}/health")).config().timeout_global(Some(Duration::from_secs(2))).build();
        if probe.call().is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err("the engine didn't answer within 5 minutes".into())
}

fn start_local<R: Runtime>(app: &AppHandle<R>) -> Result<(String, String), String> {
    let engine = config::engine().ok_or("no engine found (no checkout and none bundled)")?;
    let ui = ui_dir(app, &engine).ok_or("no built console UI found")?;
    let port = free_port()?;
    let token = random_token();
    let dir = state_dir();
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let token_file = dir.join("desktop-token");
    std::fs::write(&token_file, &token).map_err(|e| e.to_string())?;
    config::restrict_to_owner(&token_file);
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("desktop-serve.log"))
        .map_err(|e| e.to_string())?;
    let args: Vec<String> = [
        "bus", "serve", "--console", "--host", "127.0.0.1", "--port", &port.to_string(),
        "--token-file", &token_file.to_string_lossy(), "--ui", &ui.to_string_lossy(),
        "--writes", LOCAL_WRITES,
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let mut child = engine_command(&engine, &args)
        .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log))
        .spawn()
        .map_err(|e| format!("couldn't start the engine (is uv installed?): {e}"))?;
    let origin = format!("http://127.0.0.1:{port}");
    if let Err(e) = wait_healthy(&origin, &mut child) {
        let _ = child.kill();
        return Err(e);
    }
    *app.state::<Shell>().child.lock().unwrap() = Some(child);
    Ok((origin, token))
}

fn open_console<R: Runtime>(app: &AppHandle<R>, origin: &str, token: &str) {
    let Some(window) = app.get_webview_window("main") else { return };
    let Ok(mut url) = Url::parse(origin) else { return };
    url.query_pairs_mut().append_pair("token", token).append_pair("shell", "desktop");
    let _ = window.navigate(url);
}

fn show_error<R: Runtime>(app: &AppHandle<R>, message: &str) {
    use tauri_plugin_dialog::DialogExt;
    let log = state_dir().join("desktop-serve.log");
    app.dialog()
        .message(format!("{message}\n\nEngine log: {}", log.display()))
        .title("Watchman couldn't start its console")
        .show(|_| {});
}

/// Bring the window onto the configured console: the remote server, or a fresh local one. Runs off
/// the main thread (a first local start can take minutes while uv prepares the engine).
pub fn start<R: Runtime>(app: &AppHandle<R>) {
    let app = app.clone();
    std::thread::spawn(move || {
        stop_local(&app);
        let cfg = config::load();
        let remote = cfg.bus_url.as_deref().map(str::trim).filter(|u| !u.is_empty());
        let target = match remote {
            Some(url) => Ok((url.trim_end_matches('/').to_string(), cfg.bus_token.clone().unwrap_or_default())),
            None => start_local(&app),
        };
        match target {
            Ok((origin, token)) => {
                open_console(&app, &origin, &token);
                *app.state::<Shell>().target.lock().unwrap() = Some((origin, token));
            }
            Err(e) => show_error(&app, &e),
        }
    });
}

pub fn stop_local<R: Runtime>(app: &AppHandle<R>) {
    if let Some(mut child) = app.state::<Shell>().child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// POST a door command to the console the window shows (best-effort; the tray's actions).
fn door<R: Runtime>(app: &AppHandle<R>, cmd: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    let (origin, token) = app.state::<Shell>().target.lock().unwrap().clone().ok_or("console not started")?;
    let mut res = ureq::post(&format!("{origin}/api/invoke/{cmd}"))
        .header("Authorization", &format!("Bearer {token}"))
        .config()
        .timeout_global(Some(Duration::from_secs(600)))
        .build()
        .send_json(body)
        .map_err(|e| e.to_string())?;
    res.body_mut().read_json().map_err(|e| e.to_string())
}

/// The tray's "Refresh now": re-run every registered producer through the console's door.
pub fn refresh_producers<R: Runtime>(app: &AppHandle<R>) {
    for p in config::load().producers {
        let app = app.clone();
        std::thread::spawn(move || {
            let _ = door(&app, "run_producer", serde_json::json!({ "id": p.id }));
            crate::poller::poll_once(&app);
        });
    }
}

fn reload_console<R: Runtime>(app: &AppHandle<R>) {
    if let Some((origin, token)) = app.state::<Shell>().target.lock().unwrap().clone() {
        open_console(app, &origin, &token);
    }
}

/// The tray's "Load Weight Pack…": a native folder picker, then the door's pack switch.
pub fn load_pack_dialog<R: Runtime>(app: &AppHandle<R>) {
    use tauri_plugin_dialog::DialogExt;
    let app2 = app.clone();
    app.dialog().file().set_title("Load Weight Pack").pick_folder(move |dir| {
        if let Some(path) = dir.and_then(|d| d.into_path().ok()) {
            let picked = door(&app2, "set_active_pack", serde_json::json!({ "pack": path.to_string_lossy() }));
            if picked.is_ok() {
                reload_console(&app2);
            }
        }
    });
}

/// The tray's "Use this computer's console": drop the remote server and start the local one.
pub fn use_local<R: Runtime>(app: &AppHandle<R>) {
    let mut cfg = config::load();
    cfg.bus_url = None;
    cfg.bus_token = None;
    if config::save(&cfg).is_ok() {
        start(app);
    }
}

// ————— IPC commands (the shell's own local origin only) ————————————————————————————————————

#[derive(Serialize)]
pub struct ShellInfo {
    mode: &'static str,
    bus_url: Option<String>,
    bus_token_set: bool,
    version: String,
}

#[tauri::command]
pub fn shell_info<R: Runtime>(app: AppHandle<R>) -> ShellInfo {
    let cfg = config::load();
    let url = cfg.bus_url.filter(|u| !u.trim().is_empty());
    ShellInfo {
        mode: if url.is_some() { "remote" } else { "local" },
        bus_url: url,
        bus_token_set: cfg.bus_token.is_some_and(|t| !t.trim().is_empty()),
        version: app.package_info().version.to_string(),
    }
}

/// Point the window at a remote served console (url + token), or clear it (None) to return to the
/// local server. Field-allowlisted write; the window reloads onto the chosen console.
#[tauri::command]
pub fn set_bus_config<R: Runtime>(app: AppHandle<R>, url: Option<String>, token: Option<String>) -> Result<(), String> {
    let mut cfg = config::load();
    let url = url.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let token = token.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    match url {
        Some(u) => {
            if !(u.starts_with("http://") || u.starts_with("https://")) {
                return Err("the server URL must start with http:// or https://".into());
            }
            let effective = token.or_else(|| cfg.bus_token.clone().filter(|t| !t.trim().is_empty()));
            if effective.is_none() {
                return Err("a bearer token is required (the server's bus-token value)".into());
            }
            cfg.bus_url = Some(u);
            cfg.bus_token = effective;
            // a seeded demo pack seals the poller onto the local demo bus (config::bus_endpoint),
            // which would silently drop the remote server's notifications
            cfg.active_pack = None;
        }
        None => {
            cfg.bus_url = None;
            cfg.bus_token = None;
        }
    }
    config::save(&cfg)?;
    start(&app);
    Ok(())
}

/// Probe a remote server before switching to it.
#[tauri::command]
pub fn test_bus_connection(url: String, token: String) -> Result<String, String> {
    let remote = crate::remote::RemoteBus::new(url.trim().to_string(), token.trim().to_string())?;
    let unread = remote.unread_count()?;
    Ok(format!("ok — bus reachable, {unread} unread"))
}

#[tauri::command]
pub async fn pick_pack_folder<R: Runtime>(app: AppHandle<R>) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().set_title("Load Weight Pack").pick_folder(move |dir| {
        let _ = tx.send(dir.and_then(|d| d.into_path().ok()).map(|p| p.to_string_lossy().into_owned()));
    });
    rx.recv().ok().flatten()
}

#[derive(Serialize)]
pub struct UpdateInfo {
    version: String,
    notes: Option<String>,
}

/// Check the release feed (release builds only; a dev build has no updater and answers an error).
#[tauri::command]
pub async fn update_check<R: Runtime>(app: AppHandle<R>, state: State<'_, Shell>) -> Result<Option<UpdateInfo>, String> {
    #[cfg(not(debug_assertions))]
    {
        use tauri_plugin_updater::UpdaterExt;
        let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
        let info = update.as_ref().map(|u| UpdateInfo { version: u.version.clone(), notes: u.body.clone() });
        *state.pending_update.lock().unwrap() = update;
        Ok(info)
    }
    #[cfg(debug_assertions)]
    {
        let _ = (app, state);
        Err("updates are release-only".into())
    }
}

/// Download and install the update found by the last check, emitting `update-progress` (0..1).
#[tauri::command]
pub async fn update_install<R: Runtime>(app: AppHandle<R>, state: State<'_, Shell>) -> Result<(), String> {
    #[cfg(not(debug_assertions))]
    {
        use tauri::Emitter;
        let update = state.pending_update.lock().unwrap().take().ok_or("no update pending")?;
        let mut received = 0usize;
        let progress = app.clone();
        update
            .download_and_install(
                move |chunk, total| {
                    received += chunk;
                    let fraction = total.map(|t| (received as f64 / t as f64).min(1.0));
                    let _ = progress.emit("update-progress", fraction);
                },
                || {},
            )
            .await
            .map_err(|e| e.to_string())
    }
    #[cfg(debug_assertions)]
    {
        let _ = (app, state);
        Err("updates are release-only".into())
    }
}

#[tauri::command]
pub fn update_restart<R: Runtime>(app: AppHandle<R>) {
    stop_local(&app);
    app.restart();
}
