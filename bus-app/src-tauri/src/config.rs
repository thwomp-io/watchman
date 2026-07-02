//! App config: bus db path resolution (mirrors harness.bus.store.default_db_path) + the
//! producers registry (~/.config/harness/bus-app.json) — "Refresh now" targets are CONFIG,
//! not code, so adding a producer (career watchman) is a JSON edit (OSS seam).

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Producer {
    pub id: String,
    pub label: String,
    pub cmd: String,
    pub args: Vec<String>,
    pub cwd: String,
}

/// A lane information surface: a config-registered, JSON-emitting read-only
/// command the app can run on demand and render generically. Surfaces are CONFIG, not code —
/// zero domain assumptions in the app (the OSS seam, same as producers).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Surface {
    pub id: String,
    pub label: String,
    pub lane: String,
    pub cmd: String,
    pub args: Vec<String>,
    pub cwd: String,
}

/// A LIVE viz: a config-registered JSON-emitting command whose output IS a viz
/// data contract (treemap/sankey/...) — the always-current sibling of vault-snapshot data files.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LiveViz {
    pub id: String,
    pub label: String,
    pub lane: String,
    pub viz_type: String,
    pub cmd: String,
    pub args: Vec<String>,
    pub cwd: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default)]
    pub db_path: Option<String>,
    #[serde(default)]
    pub tracker_path: Option<String>,
    /// The active weight pack (a scenario bundle dir). When set, on-demand panel reads inject it as
    /// WEIGHTS_PACK so the console renders the pack's data (the scenario-switcher). None/empty = the
    /// user's real corpus. The standing producers are a separate spawn path and never see this.
    #[serde(default)]
    pub active_pack: Option<String>,
    pub producers: Vec<Producer>,
    #[serde(default = "default_surfaces")]
    pub surfaces: Vec<Surface>,
    #[serde(default = "default_live_viz")]
    pub live_viz: Vec<LiveViz>,
}

/// The real user home, cross-platform. `HOME` is the unix env; **Windows doesn't set it** —
/// `USERPROFILE` is the Windows equivalent (the Windows-support fix: before this, every
/// home-derived path on Windows silently fell back to `/`). Checked in that order so a unix-style
/// `HOME` override (tests, sandboxes) still wins everywhere.
fn home() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/"))
}

/// The harness STATE root — `HARNESS_HOME` env (for an isolated test / CI instance) else `$HOME`.
/// One knob relocates ALL of an instance's harness state (config, bus db, dashboards, default vault,
/// bundled packs), so a sandboxed prod-app test instance never collides with a dev daily-driver's
/// `~/.config/harness` + `~/.local/state/harness`. Deliberately NOT used by [`augmented_path`]: the
/// spawned toolchain (uv/cargo) lives under the real `$HOME` and must resolve there regardless of the
/// harness-state sandbox.
pub fn harness_home() -> PathBuf {
    harness_home_from(std::env::var("HARNESS_HOME").ok(), home())
}

/// Pure core of [`harness_home`]: the `HARNESS_HOME` override (when non-blank) else the real home.
/// Split out so tests don't mutate global env (matches [`augment_path_with`]).
fn harness_home_from(env: Option<String>, real_home: PathBuf) -> PathBuf {
    match env {
        Some(h) if !h.trim().is_empty() => PathBuf::from(h.trim()),
        _ => real_home,
    }
}

pub fn expand_home(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        home().join(rest)
    } else {
        PathBuf::from(path)
    }
}

/// Resolve a command working-directory against [`harness_home`] (NOT the real `$HOME`).
///
/// A widget/surface/producer `cwd` like `.` points at the harness REPO (the uv
/// project `uv run hn` needs). It must follow `HARNESS_HOME` so a sandboxed prod-app instance spawns
/// `hn` in the *sandboxed* tree (reading fictional pack data) instead of the dev daily-driver's real
/// repo + corpus. Behavior-preserving in dev (there `harness_home() == $HOME`, so this equals the old
/// `expand_home`); sandbox-correct in prod. The leak this closes: a published app on a machine that
/// HAS the real corpus would otherwise `cd . && uv run hn …` and read it live.
/// Absolute / non-`~/` cwds pass through unchanged.
pub fn resolve_cwd(cwd: &str) -> PathBuf {
    if let Some(rest) = cwd.strip_prefix("~/") {
        harness_home().join(rest)
    } else {
        PathBuf::from(cwd)
    }
}

/// The PATH *list* separator (`:` on unix, `;` on Windows — NOT the dir separator).
/// A Windows path itself contains a drive colon (`C:\…`), so splitting a Windows PATH on `:`
/// would shred every entry — the platform const is load-bearing, not cosmetic.
#[cfg(windows)]
const PATH_LIST_SEP: char = ';';
#[cfg(not(windows))]
const PATH_LIST_SEP: char = ':';

/// A PATH for spawned subprocesses that survives the GUI launch context.
///
/// `Command::new("uv")` resolves a bare program name via the process PATH — but a launchd
/// LaunchAgent / login-item launch inherits a minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`)
/// that omits `/opt/homebrew/bin` (where uv lives). Dev launches worked only because a
/// terminal-launched process inherits the interactive shell PATH. We prepend the standard
/// tool dirs deterministically, dir-existence-checked + dedup'd against the inherited PATH.
/// (Cross-language twin of the career `render` texbin PATH-extend; same rationale: long-lived
/// / non-interactive launch contexts don't inherit the dirs.)
///
/// Cross-platform: candidates are platform-gated — unix gets the homebrew/local tool dirs;
/// Windows gets uv's default per-user install dir (`%USERPROFILE%\.local\bin` — uv uses the same
/// relative path on Windows by its own convention) + cargo's. Windows GUI launches inherit a fuller
/// PATH than launchd's minimal one, but startup-registered launches can still miss per-user tool
/// dirs, so the same belt applies on every platform.
pub fn augmented_path() -> String {
    #[cfg(not(windows))]
    let candidates = [
        "/opt/homebrew/bin".to_string(),
        "/usr/local/bin".to_string(),
        home().join(".local/bin").to_string_lossy().into_owned(),
    ];
    #[cfg(windows)]
    let candidates = [
        home().join(".local").join("bin").to_string_lossy().into_owned(),
        home().join(".cargo").join("bin").to_string_lossy().into_owned(),
    ];
    augment_path_with(
        &std::env::var("PATH").unwrap_or_default(),
        &candidates,
        PATH_LIST_SEP,
    )
}

/// Pure core of [`augmented_path`]: prepend each existing-on-disk candidate dir that isn't
/// already present, preserving the inherited PATH as the suffix. Split out for testability
/// (no global-env mutation in tests); the list separator is a PARAMETER so tests pin both
/// platforms' behavior on any host (the platform const feeds it in [`augmented_path`]).
fn augment_path_with(current: &str, candidates: &[String], sep: char) -> String {
    let existing: Vec<&str> = current.split(sep).collect();
    let mut prefix: Vec<String> = Vec::new();
    for dir in candidates {
        if PathBuf::from(dir).is_dir() && !existing.contains(&dir.as_str()) && !prefix.contains(dir)
        {
            prefix.push(dir.clone());
        }
    }
    let sep_s = sep.to_string();
    match (prefix.is_empty(), current.is_empty()) {
        (true, _) => current.to_string(),
        (false, true) => prefix.join(&sep_s),
        (false, false) => format!("{}{}{}", prefix.join(&sep_s), sep_s, current),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_inherited_path_as_suffix_and_never_duplicates() {
        // "/" exists on every unix; use it as a stand-in candidate so the test is FS-stable.
        let candidates = ["/".to_string()];
        // Already present -> no change.
        let already = "/usr/bin:/:/bin";
        assert_eq!(augment_path_with(already, &candidates, ':'), already);
        // Absent -> prepended, original preserved as suffix, exactly once.
        let out = augment_path_with("/usr/bin:/bin", &candidates, ':');
        assert_eq!(out, "/:/usr/bin:/bin");
        assert_eq!(out.split(':').filter(|p| *p == "/").count(), 1);
    }

    #[test]
    fn nonexistent_candidates_are_skipped() {
        let candidates = ["/no/such/dir/xyzzy".to_string()];
        assert_eq!(augment_path_with("/usr/bin", &candidates, ':'), "/usr/bin");
    }

    #[test]
    fn empty_inherited_path_yields_bare_prefix() {
        assert_eq!(augment_path_with("", &["/".to_string()], ':'), "/");
    }

    #[test]
    fn windows_semicolon_separator_never_splits_drive_colons() {
        // The Windows regression this guards: splitting a Windows PATH on ':' shreds
        // `C:\…` entries. With ';' the drive colons survive intact. Existing dir = "/" so the
        // candidate passes the is_dir() check on any test host; the SEPARATOR is what's under test.
        let candidates = ["/".to_string()];
        let win_path = r"C:\Windows\system32;C:\Users\u\.local\bin";
        let out = augment_path_with(win_path, &candidates, ';');
        assert_eq!(out, format!(r"/;{win_path}"));
        // And a candidate already present under ';' isn't re-prepended.
        let already = format!(r"/;{win_path}");
        assert_eq!(augment_path_with(&already, &candidates, ';'), already);
    }

    #[test]
    fn manifest_is_default_reads_the_flag_tolerating_comments_and_indent() {
        assert!(manifest_is_default("name: x\ndefault: true\n"));
        assert!(manifest_is_default("default: true  # the bundled default"));
        assert!(manifest_is_default("  default: true")); // indented
        assert!(!manifest_is_default("default: false"));
        assert!(!manifest_is_default("name: x\n# default: true (commented out)"));
        assert!(!manifest_is_default("defaulting: true")); // key must be exactly `default`
        assert!(!manifest_is_default("lanes:\n  - finance"));
    }

    #[test]
    fn harness_home_prefers_nonblank_override_else_real_home() {
        let real = PathBuf::from("/Users/real");
        // override wins
        assert_eq!(
            harness_home_from(Some("/tmp/sandbox".into()), real.clone()),
            PathBuf::from("/tmp/sandbox")
        );
        // blank / whitespace override is ignored -> real home
        assert_eq!(harness_home_from(Some("   ".into()), real.clone()), real);
        assert_eq!(harness_home_from(None, real.clone()), real);
        // trimmed
        assert_eq!(
            harness_home_from(Some("  /tmp/sb  ".into()), real),
            PathBuf::from("/tmp/sb")
        );
    }
}

/// Same resolution order as the Python side: explicit config > HARNESS_BUS_DB > default.
pub fn db_path(cfg: &AppConfig) -> PathBuf {
    if let Some(p) = &cfg.db_path {
        return expand_home(p);
    }
    if let Ok(env) = std::env::var("HARNESS_BUS_DB") {
        if !env.trim().is_empty() {
            return expand_home(env.trim());
        }
    }
    harness_home().join(".local/state/harness/bus.db")
}

fn config_path() -> PathBuf {
    harness_home().join(".config/harness/bus-app.json")
}

/// Persist the config (used by the scenario-switcher to remember the active pack across launches).
pub fn save(cfg: &AppConfig) -> Result<(), String> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let body = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    fs::write(&path, body).map_err(|e| e.to_string())
}

/// The app-bundle resource packs dir (`<resource_dir>/packs`), set once at Tauri setup — the
/// installed-app fallback for [`samples_packs_dir`]. An `OnceLock` because resource-dir resolution
/// needs the Tauri `AppHandle`, which the free config functions deliberately don't take (they're
/// called from `load()` paths with no handle in reach).
static RESOURCE_PACKS_DIR: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();

/// Called once from the app `setup` hook, BEFORE any command can run, so every later
/// [`samples_packs_dir`] call sees the bundled location.
pub fn set_resource_packs_dir(dir: Option<PathBuf>) {
    let _ = RESOURCE_PACKS_DIR.set(dir);
}

/// Where the sample weight packs live. Two-stage resolution (the demo-experience fix — a fresh
/// installed app has NO cloned repo, so the old repo-only path yielded an empty PACK dropdown on
/// every fresh Windows/Linux install):
///   1. the harness REPO checkout (`{harness_home}/projects/harness/samples/packs`) when present —
///      the dev daily-driver + the sandboxed-launcher case (packs symlinked under `HARNESS_HOME`);
///   2. else the packs bundled INTO the app itself (`tauri.conf.json bundle.resources` ships
///      `samples/packs` → `<resource_dir>/packs`; see [`set_resource_packs_dir`]).
/// The repo wins when both exist so dev edits to packs reflect without a rebuild.
pub fn samples_packs_dir() -> PathBuf {
    let repo = harness_home().join("projects/harness/samples/packs");
    if repo.is_dir() {
        return repo;
    }
    if let Some(Some(bundled)) = RESOURCE_PACKS_DIR.get() {
        if bundled.is_dir() {
            return bundled.clone();
        }
    }
    repo // neither exists → the repo path (callers already treat a missing dir as "no packs")
}

/// Pure predicate: does a `pack.yaml` mark itself the bundled default (`default: true`)? Tolerates
/// trailing comments + indentation; split out so the scan is unit-testable without a YAML parser
/// (same "enough without a parser" ethos as `list_packs`).
fn manifest_is_default(yaml: &str) -> bool {
    yaml.lines().any(|line| {
        let code = line.split('#').next().unwrap_or("");
        matches!(code.split_once(':'), Some((k, v)) if k.trim() == "default" && v.trim() == "true")
    })
}

/// The bundled pack marked `default: true`, as a dir path — the OOTB active scenario a fresh/published
/// install seeds (so it renders FICTIONAL sample data, never the absent/real corpus). None if no pack
/// is marked default, in which case the app boots pack-less (the real-corpus behavior — correct for a
/// dev clone with no sample packs). Scans [`samples_packs_dir`]; deterministic (sorted).
pub fn default_pack_dir() -> Option<PathBuf> {
    let mut packs: Vec<PathBuf> = fs::read_dir(samples_packs_dir())
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.join("pack.yaml").is_file())
        .collect();
    packs.sort();
    packs.into_iter().find(|p| {
        fs::read_to_string(p.join("pack.yaml"))
            .map(|t| manifest_is_default(&t))
            .unwrap_or(false)
    })
}

/// The tracker vault root (viz data discovery): config override > {harness_home}/projects/corpus.
pub fn tracker_path(cfg: &AppConfig) -> PathBuf {
    cfg.tracker_path.as_deref().map(expand_home)
        .unwrap_or_else(|| harness_home().join("projects/corpus"))
}

fn hn_surface(id: &str, label: &str, lane: &str, args: &[&str]) -> Surface {
    let mut full: Vec<String> = vec!["run".into(), "hn".into()];
    full.extend(args.iter().map(|a| a.to_string()));
    Surface {
        id: id.into(),
        label: label.into(),
        lane: lane.into(),
        cmd: "uv".into(),
        args: full,
        cwd: ".".into(),
    }
}

fn default_surfaces() -> Vec<Surface> {
    vec![
        hn_surface("finance.watch", "Watch digest", "finance",
                   &["finance", "watch", "--no-mark", "--json"]),
        hn_surface("finance.networth", "Net worth", "finance", &["finance", "networth", "--json"]),
        hn_surface("career.openings", "Openings scan", "career", &["career", "openings", "--json"]),
        // {today}/{today+N} are substituted at spawn time (see commands::run_surface)
        hn_surface("travel.weather", "Home-base weather", "travel",
                   &["travel", "weather", "--city", "Anytown, USA", "--from", "{today}",
                     "--to", "{today+4}", "--json"]),
        // The Inbox watch-floor band reads this; fast — reads the run-log, no network.
        hn_surface("system.watchmen", "Watchmen", "system", &["bus", "watchmen", "--json"]),
    ]
}

fn default_live_viz() -> Vec<LiveViz> {
    vec![LiveViz {
        id: "finance.concentration.live".into(),
        label: "Concentration (LIVE)".into(),
        lane: "finance".into(),
        viz_type: "treemap".into(),
        cmd: "uv".into(),
        args: vec!["run".into(), "hn".into(), "finance".into(), "concentration".into(),
                   "--json".into()],
        cwd: ".".into(),
    }]
}

fn default_config() -> AppConfig {
    AppConfig {
        db_path: None,
        tracker_path: None,
        active_pack: None,
        producers: vec![Producer {
            id: "finance.pulse".into(),
            label: "Finance pulse".into(),
            cmd: "uv".into(),
            // --notify is the publish path (osascript inside it is the deprecated fallback,
            // removed in Phase 3 — after which this is purely publish + log).
            args: vec!["run".into(), "hn".into(), "finance".into(), "pulse".into(),
                       "--notify".into(), "--json".into()],
            cwd: ".".into(),
        }],
        surfaces: default_surfaces(),
        live_viz: default_live_viz(),
    }
}

/// Load the registry, writing the documented default on first launch.
pub fn load() -> AppConfig {
    let path = config_path();
    if let Ok(text) = fs::read_to_string(&path) {
        if let Ok(cfg) = serde_json::from_str::<AppConfig>(&text) {
            return cfg;
        }
    }
    let mut cfg = default_config();
    // First run (no config on disk): default to the bundled fictional pack so a fresh / published
    // install renders sample data OOTB and NEVER the real-or-absent corpus (the b15.1 OOTB-safe
    // state). Fires ONLY when the config file is absent — an existing user/dev config is returned
    // verbatim above, so the dev daily-driver (pack-less = real corpus) is untouched.
    cfg.active_pack = default_pack_dir().map(|p| p.to_string_lossy().into_owned());
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(&path, serde_json::to_string_pretty(&cfg).unwrap_or_default());
    cfg
}
