//! Native tray: unread badge in the title + a rebuilt-on-poll menu (top-5 recent unread →
//! Open Inbox · Refresh now · Quit). Native menu over a webview popover — reliable on macOS.

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, Runtime};

use crate::bus::Event;

pub const TRAY_ID: &str = "main-tray";

fn show_main<R: Runtime>(app: &AppHandle<R>) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn build_menu<R: Runtime>(app: &AppHandle<R>, recent: &[Event]) -> tauri::Result<Menu<R>> {
    let menu = Menu::new(app)?;
    for e in recent.iter().take(5) {
        let label = format!("{} · {}", sev_dot(&e.severity), truncate(&e.title, 48));
        menu.append(&MenuItem::with_id(app, format!("evt-{}", e.id), label, true, None::<&str>)?)?;
    }
    if !recent.is_empty() {
        menu.append(&PredefinedMenuItem::separator(app)?)?;
    }
    menu.append(&MenuItem::with_id(app, "open", "Open Inbox", true, None::<&str>)?)?;
    menu.append(&MenuItem::with_id(app, "refresh", "Refresh now", true, None::<&str>)?)?;
    menu.append(&PredefinedMenuItem::separator(app)?)?;
    menu.append(&MenuItem::with_id(app, "quit", "Quit Watchman", true, None::<&str>)?)?;
    Ok(menu)
}

fn sev_dot(severity: &str) -> &'static str {
    match severity {
        "alert" => "🔴",
        "warn" => "🟠",
        _ => "🔵",
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(max - 1).collect::<String>())
    }
}

pub fn init<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let menu = build_menu(app, &[])?;
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(app.default_window_icon().expect("default icon").clone())
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            match id {
                "open" => show_main(app),
                "refresh" => {
                    let _ = app.emit("bus-refresh-requested", ());
                    crate::commands::refresh_all_producers(app.clone());
                }
                "quit" => app.exit(0),
                other => {
                    if let Some(num) = other.strip_prefix("evt-") {
                        show_main(app);
                        let _ = app.emit("bus-select", num.parse::<i64>().unwrap_or(0));
                    }
                }
            }
        })
        .build(app)?;
    Ok(())
}

/// Called by the poller after each pass: badge count + recent-unread menu items.
pub fn update<R: Runtime>(app: &AppHandle<R>, unread: i64, recent: &[Event]) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let title = if unread > 0 { format!("{unread}") } else { String::new() };
        let _ = tray.set_title(Some(title.as_str()));
        if let Ok(menu) = build_menu(app, recent) {
            let _ = tray.set_menu(Some(menu));
        }
    }
}
