//! The delivery loop: every 30s (and on demand) — notify undelivered unread events under the
//! app's OWN identity, mark `delivered_via` with this instance's transport marker, refresh tray
//! badge/menu, nudge the webview. Deterministic; the db is the single source of delivery truth
//! (no app-local state — a reinstall can't desync, and ntfy later uses the identical mechanism).
//! Transport-agnostic via backend::BusHandle: a `bus_url` remote watchman runs this exact loop
//! against the served bus, with its own per-device marker (see BusHandle::transport_marker).

use std::time::Duration;

use tauri::{AppHandle, Emitter, Runtime};
use tauri_plugin_notification::NotificationExt;

use crate::{backend, config, tray};

const POLL_SECS: u64 = 30;

/// Backlog guard: a tick with more undelivered events than this is a catch-up (a device's first
/// poll against an established bus — the remote first-boot case — or a mode flip), not a live
/// burst. Deliver ONE summary notification and adopt the rest silently; the Inbox + badge carry
/// the detail. Normal producer bursts (a pulse run's handful of flags) stay well under it and
/// keep per-event notifications.
const BACKLOG_SUMMARY_THRESHOLD: usize = 25;

pub fn poll_once<R: Runtime>(app: &AppHandle<R>) {
    let cfg = config::load();
    let handle = match backend::open(&cfg) {
        Ok(h) => h,
        Err(_) => return, // bus unavailable this tick (db locked / mesh down) — next tick retries
    };
    let marker = handle.transport_marker();

    if let Ok(undelivered) = handle.undelivered_unread(&marker) {
        if undelivered.len() > BACKLOG_SUMMARY_THRESHOLD {
            let shown = app
                .notification()
                .builder()
                .title(format!("[bus] {} events awaiting — backlog adopted", undelivered.len()))
                .body("Catch-up detected (first poll on this device?). See the Inbox.")
                .show();
            if shown.is_ok() {
                for event in &undelivered {
                    let _ = handle.mark_delivered(event.id, &marker);
                }
            }
        } else {
            for event in undelivered.iter().rev() {
                // oldest first so notification order reads chronologically
                let shown = app
                    .notification()
                    .builder()
                    .title(format!("[{}] {}", event.lane, event.title))
                    .body(event.body.clone())
                    .show();
                if shown.is_ok() {
                    let _ = handle.mark_delivered(event.id, &marker);
                }
            }
        }
    }

    let unread = handle.unread_count().unwrap_or(0);
    let recent = handle.list_events(true, None, None, 5).unwrap_or_default();
    tray::update(app, unread, &recent);
    let _ = app.emit("bus-updated", unread);
}

pub fn spawn<R: Runtime>(app: AppHandle<R>) {
    std::thread::spawn(move || loop {
        poll_once(&app);
        std::thread::sleep(Duration::from_secs(POLL_SECS));
    });
}
