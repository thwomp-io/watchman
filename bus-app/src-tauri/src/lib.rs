//! Watchman — menu-bar-resident inbox + native notifications for the harness message bus.
//! The delivery surface for the harness message bus (contract: ../docs/BUS.md). Deterministic; zero model anywhere.

mod backend;
mod bus;
mod commands;
mod config;
mod poller;
mod remote;
mod dash;
mod tray;
mod viz;
mod watcher;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        // single-instance MUST register first: a second launch focuses the existing window
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_notification::init())
        // dialog: the native folder picker behind "Load Weight Pack…" (browse to ANY pack dir). A
        // modal dialog, NOT a menu — safe for an Accessory app (a Builder menu is not).
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ));
    // Self-update (release bundles only — the autostart rule, extended to a whole plugin): a dev
    // binary must never replace an installed app, so debug builds don't even REGISTER the updater —
    // the capability exists only where updating is legitimate, instead of being an ignored runtime
    // no-op. process rides the same gate: relaunch() exists exclusively as the post-update restart.
    // Endpoint + pubkey live in tauri.conf.json `plugins.updater` — the base config points NOWHERE
    // (empty endpoints: the dev daily-driver must never offer to overwrite itself with a public
    // release), and the release overlay `tauri.release.conf.json` supplies the published releases'
    // latest.json endpoint at release-build time. Full key/release mechanics: docs/UPDATER.md.
    // NOTE (update-path regression class): the bundled engine ships INSIDE the bundle the updater
    // replaces, while its venv lives in the user-writable state dir and survives — the startup
    // `uv sync --frozen` below is what reconciles the surviving venv with the NEW engine's lockfile.
    // On Windows that re-sync runs in the freshly-installed app's first launch — the same context
    // as the known uv-junction "untrusted mount point" first-run class (installer-
    // context execution) — so the UPDATE path must be regression-tested against that class on a
    // real Windows machine before the updater ships (docs/UPDATER.md, first-release checklist).
    #[cfg(not(debug_assertions))]
    let builder = builder
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());
    builder
        .invoke_handler(tauri::generate_handler![
            commands::list_events,
            commands::ack_events,
            commands::unread_count,
            commands::urgent_unread_count,
            commands::distinct_meta,
            commands::app_version,
            commands::get_config,
            commands::run_producer,
            commands::list_surfaces,
            commands::run_surface,
            commands::list_viz,
            commands::list_dashboards,
            commands::run_widget,
            commands::list_packs,
            commands::get_active_pack,
            commands::set_active_pack,
            commands::read_viz,
            commands::list_vault_docs,
            commands::read_doc,
            commands::list_vault_dir,
            commands::read_image,
        ])
        .setup(|app| {
            // Dock app + tray hybrid (the Slack/Discord model): Regular gives a Dock icon +
            // Cmd-Tab entry + app menu, because the console is a full surface that's actively
            // opened to browse (DASH/VAULT/VIZ/takes), not an Accessory background utility.
            // Standing-watchman delivery is preserved because CloseRequested (below) HIDES the
            // window instead of quitting — so closing keeps the process resident + the tray alive,
            // and the Dock icon re-shows it (Reopen, below). Regular (not Accessory): the console is
            // a full browse surface, not a background-only utility.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Regular);
            // standing-agent requirement: survive reboots. Release bundles only — a dev binary
            // must never register itself as a login item (a dev binary must never masquerade as the installed one).
            #[cfg(not(debug_assertions))]
            {
                use tauri_plugin_autostart::ManagerExt;
                let _ = app.autolaunch().enable();
            }
            // Explicitly request notification authorization — without this the plugin never
            // registers with Notification Center (no prompt, no Settings entry) and falls back
            // to script-style delivery under the wrong identity (otherwise the first
            // notification can ride a stale script-runner whitelist).
            {
                use tauri_plugin_notification::NotificationExt;
                let _ = app.notification().request_permission();
            }
            // Bundled-packs fallback (the fresh-install demo fix): resolve the app resource dir
            // ONCE here — before any command can run — so samples_packs_dir() falls back to the
            // packs shipped inside the bundle when no cloned repo exists on the machine.
            config::set_resource_packs_dir(app.path().resource_dir().ok().map(|d| d.join("packs")));
            // Bundled-engine fallback (same shape): an installed console with no repo checkout
            // runs `hn` from the engine staged into the bundle resources.
            config::set_resource_engine_dir(app.path().resource_dir().ok().map(|d| d.join("engine")));
            // Warm the bundled engine's venv in the background: the first `uv run` on a fresh
            // machine downloads a Python + resolves the lockfile (a minute-scale one-time cost).
            // Doing it once at startup keeps the first widget paint from eating that latency;
            // uv's own project lock serializes any widget spawns that race it.
            if let Some(engine) = config::engine() {
                if engine.bundled {
                    std::thread::spawn(move || {
                        let mut warm = std::process::Command::new("uv");
                        warm.args(["sync", "--frozen", "--no-dev", "--project"])
                            .arg(&engine.project_dir)
                            .env("UV_PROJECT_ENVIRONMENT", config::engine_venv_dir())
                            .env("PATH", config::augmented_path());
                        #[cfg(windows)]
                        {
                            use std::os::windows::process::CommandExt;
                            warm.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
                        }
                        let _ = warm.output();
                    });
                }
            }
            tray::init(&app.handle().clone())?;
            poller::spawn(app.handle().clone());
            watcher::spawn(app.handle().clone()); // VAULT live-refresh: fs-watch → vault-changed
            Ok(())
        })
        .on_window_event(|window, event| {
            // close = hide (stay resident); the tray's Quit (and Cmd-Q) is the real exit
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Watchman")
        .run(|app, event| {
            // Dock-icon click (or app-switcher activate) must re-show a hidden window. With the
            // close-to-hide handler above, after a close the window is hidden, not gone — so without
            // this the Dock icon would be a dead click. macOS-only Reopen event.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        });
}
