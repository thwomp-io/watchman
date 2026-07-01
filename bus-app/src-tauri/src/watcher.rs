//! Vault fs-watcher: emits `vault-changed` to the webview when a tracker/ doc or image is
//! created/removed/modified, so the VAULT rail reflects edits in near-real-time (whether from
//! Obsidian, an agent, or git). Event-driven (notify → FSEvents on macOS) — no idle polling. The
//! Rust side is the one that touches the filesystem; the webview only refetches. Same boundary as
//! the read commands.

use std::path::Path;
use std::sync::mpsc::channel;
use std::time::Duration;

use notify::{EventKind, RecursiveMode, Watcher};
use tauri::{AppHandle, Emitter, Runtime};

use crate::config;

/// How long to swallow a burst before emitting once — an editor save or a `git checkout` fires
/// many events; we coalesce them into a single refresh.
const COALESCE: Duration = Duration::from_millis(300);

/// Path segments we never wake on: VCS/tooling internals + non-browsable dirs that churn
/// constantly (a `git commit`/`bd` write must not trigger a vault refresh).
const NOISE: &[&str] = &[
    "/.git/", "/.beads/", "/.obsidian/", "/node_modules/", "/.trash/", "/screenshots/", "/tmp/",
];

fn is_noise(path: &Path) -> bool {
    let s = path.to_string_lossy();
    NOISE.iter().any(|seg| s.contains(seg))
}

fn relevant(ev: &notify::Event) -> bool {
    matches!(
        ev.kind,
        EventKind::Create(_) | EventKind::Modify(_) | EventKind::Remove(_)
    ) && ev.paths.iter().any(|p| !is_noise(p))
}

pub fn spawn<R: Runtime>(app: AppHandle<R>) {
    std::thread::spawn(move || {
        let vault = config::tracker_path(&config::load());
        let (tx, rx) = channel();
        let mut watcher = match notify::recommended_watcher(move |res| {
            let _ = tx.send(res);
        }) {
            Ok(w) => w,
            Err(_) => return,
        };
        if watcher.watch(&vault, RecursiveMode::Recursive).is_err() {
            return;
        }
        // `watcher` is held for the thread's life — dropping it would stop the watch.
        loop {
            match rx.recv() {
                Ok(Ok(ev)) if relevant(&ev) => {
                    // drain the rest of the burst, then emit once (trailing debounce)
                    while rx.recv_timeout(COALESCE).is_ok() {}
                    let _ = app.emit("vault-changed", ());
                }
                Ok(_) => {}        // irrelevant event or a watch error — keep watching
                Err(_) => break,   // channel closed (watcher dropped) — exit the thread
            }
        }
    });
}
