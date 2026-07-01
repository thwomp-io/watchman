//! Watchman — menu-bar-resident inbox + native notifications for the harness message bus.
//! The delivery surface for the harness message bus (contract: ../docs/BUS.md). Deterministic; zero model anywhere.

mod bus;
mod commands;
mod config;
mod poller;
mod dash;
mod tray;
mod viz;
mod watcher;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // single-instance MUST register first: a second launch focuses the existing window
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_notification::init())
        // dialog: the native folder picker behind "Load Weight Pack…" (browse to ANY pack dir). A
        // modal dialog, NOT a menu — safe for an Accessory app (the 0.1.30 no-Builder-menu lesson).
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![
            commands::list_events,
            commands::ack_events,
            commands::unread_count,
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
            // and the Dock icon re-shows it (Reopen, below). (Was Accessory since 0.1.0; dock-icon
            // suppression had been flaky with a visible window historically, so the app appeared on
            // the dock until a Tauri/macOS bump made suppression actually bite — fixed forward.)
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Regular);
            // standing-agent requirement: survive reboots. Release bundles only — a dev binary
            // must never register itself as a login item (caught 2026-06-12, pre-P3-acceptance).
            #[cfg(not(debug_assertions))]
            {
                use tauri_plugin_autostart::ManagerExt;
                let _ = app.autolaunch().enable();
            }
            // Explicitly request notification authorization — without this the plugin never
            // registers with Notification Center (no prompt, no Settings entry) and falls back
            // to script-style delivery under the wrong identity (caught live 2026-06-12:
            // the installed bundle's first notification rode Script Editor's old whitelist).
            {
                use tauri_plugin_notification::NotificationExt;
                let _ = app.notification().request_permission();
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
