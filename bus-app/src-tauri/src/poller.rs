//! The delivery loop: every 30s (and on demand) — notify undelivered unread events under the
//! app's OWN identity, mark `delivered_via:"desktop"`, refresh tray badge/menu, nudge the
//! webview. Deterministic; the db is the single source of delivery truth (no app-local state —
//! a reinstall can't desync, and ntfy later uses the identical mechanism).

use std::time::Duration;

use tauri::{AppHandle, Emitter, Runtime};
use tauri_plugin_notification::NotificationExt;

use crate::{bus, config, tray};

const MARKER: &str = "desktop";
const POLL_SECS: u64 = 30;

pub fn poll_once<R: Runtime>(app: &AppHandle<R>) {
    let cfg = config::load();
    let path = config::db_path(&cfg);
    let conn = match bus::open(&path) {
        Ok(c) => c,
        Err(_) => return, // db unavailable this tick — next tick retries (graceful degradation)
    };

    if let Ok(undelivered) = bus::undelivered_unread(&conn, MARKER) {
        for event in undelivered.iter().rev() {
            // oldest first so notification order reads chronologically
            let shown = app
                .notification()
                .builder()
                .title(format!("[{}] {}", event.lane, event.title))
                .body(event.body.clone())
                .show();
            if shown.is_ok() {
                let _ = bus::mark_delivered(&conn, event.id, MARKER);
            }
        }
    }

    let unread = bus::unread_count(&conn).unwrap_or(0);
    let recent = bus::list_events(&conn, true, None, None, 5).unwrap_or_default();
    tray::update(app, unread, &recent);
    let _ = app.emit("bus-updated", unread);
}

pub fn spawn<R: Runtime>(app: AppHandle<R>) {
    std::thread::spawn(move || loop {
        poll_once(&app);
        std::thread::sleep(Duration::from_secs(POLL_SECS));
    });
}
