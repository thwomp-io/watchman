//! App config: bus db path resolution (mirrors harness.bus.store.default_db_path) + the
//! producers registry (~/.config/harness/bus-app.json) — "Refresh now" targets are CONFIG,
//! not code, so adding a producer (career watchman) is a JSON edit (OSS seam).

use std::fs;
use std::path::{Path, PathBuf};

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
    /// Remote bus (docs/BUS.md "Serving the bus over HTTP"): base URL of an `hn bus serve`
    /// instance — prefer the MagicDNS name (e.g. `http://my-mini.mesh.internal:8787`;
    /// survives node re-enrollment) over the tailnet IP (stable but registration-bound).
    /// Absent/blank = local rusqlite, unchanged — the OOTB default. Multi-device is an opt-in
    /// layer; no sample pack ever sets this.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bus_url: Option<String>,
    /// Bearer token for `bus_url` (the server's `~/.config/harness/bus-token` value). Same
    /// threat model as the toolkit's .env keys: the mesh/ACL is the perimeter, this is
    /// defense-in-depth. Required whenever `bus_url` is set.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bus_token: Option<String>,
    #[serde(default)]
    pub tracker_path: Option<String>,
    /// The active weight pack (a scenario bundle dir). When set, on-demand panel reads inject it as
    /// WEIGHTS_PACK so the console renders the pack's data (the scenario-switcher). None/empty = the
    /// user's real corpus. Producers see it only when the pack is a BUNDLED demo persona (the
    /// demo-pack full seal routes every spawn through the pack); personal packs never touch them.
    #[serde(default)]
    pub active_pack: Option<String>,
    pub producers: Vec<Producer>,
    #[serde(default = "default_surfaces")]
    pub surfaces: Vec<Surface>,
    #[serde(default = "default_live_viz")]
    pub live_viz: Vec<LiveViz>,
}

/// The real user home, cross-platform. `HOME` is the unix env; **Windows doesn't set it** —
/// `USERPROFILE` is the Windows equivalent. Checked in that order so a unix-style `HOME`
/// override (tests, sandboxes) still wins everywhere.
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
///
/// A resolved dir that doesn't exist falls back to [`harness_home`]: an installed console has no
/// repo checkout, and spawning with a nonexistent working directory fails before the child ever
/// runs. The engine project is pinned per-spawn via `--project` (see [`engine`]), so the cwd
/// carries no resolution semantics of its own.
pub fn resolve_cwd(cwd: &str) -> PathBuf {
    let dir = if let Some(rest) = cwd.strip_prefix("~/") {
        harness_home().join(rest)
    } else {
        PathBuf::from(cwd)
    };
    if dir.is_dir() { dir } else { harness_home() }
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
/// Candidates are platform-gated: unix gets the homebrew/local tool dirs; Windows gets uv's
/// per-user install dir (`%USERPROFILE%\.local\bin`) and cargo's. Startup-registered launches on
/// any platform can miss per-user tool dirs, so the same belt applies everywhere.
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
        // Splitting a Windows PATH on ':' would shred `C:\…` entries; with ';' the drive colons
        // survive. Existing dir = "/" so the candidate passes is_dir() on any test host; the
        // SEPARATOR is what's under test.
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

    #[test]
    fn bundled_pack_detection_gates_the_demo_seal() {
        // A pack under the repo samples dir is bundled (the demo-pack full seal engages)…
        let bundled = samples_packs_dir().join("demo-investor");
        assert!(is_bundled_pack(&bundled));
        // …a personal pack anywhere else is not (blend semantics stay).
        assert!(!is_bundled_pack(Path::new("/somewhere/else/my-pack")));
    }

    #[test]
    fn bus_endpoint_resolves_local_remote_and_seal() {
        let mut cfg = default_config();
        // Default: local file (OOTB unchanged).
        assert!(matches!(bus_endpoint(&cfg), BusEndpoint::Local(_)));
        // bus_url set → remote; trailing slash normalized; token rides along.
        cfg.bus_url = Some("http://my-mini.mesh.internal:8787/".into());
        cfg.bus_token = Some(" secret ".into());
        assert_eq!(
            bus_endpoint(&cfg),
            BusEndpoint::Remote { url: "http://my-mini.mesh.internal:8787".into(), token: "secret".into() }
        );
        // Missing token stays REMOTE (empty token) — the client errors actionably, never a
        // silent local fallback the user opted out of.
        cfg.bus_token = None;
        assert_eq!(
            bus_endpoint(&cfg),
            BusEndpoint::Remote { url: "http://my-mini.mesh.internal:8787".into(), token: String::new() }
        );
        // Blank url = unset.
        cfg.bus_url = Some("   ".into());
        assert!(matches!(bus_endpoint(&cfg), BusEndpoint::Local(_)));
        // Demo seal trumps remote: bundled pack active → the sealed local bus, never the mesh.
        cfg.bus_url = Some("http://my-mini.mesh.internal:8787".into());
        cfg.active_pack = Some(samples_packs_dir().join("demo-investor").display().to_string());
        assert_eq!(
            bus_endpoint(&cfg),
            BusEndpoint::Local(demo_seal_state_dir().join("bus.db"))
        );
    }

    #[test]
    fn demo_seal_covers_the_bus_db() {
        // The seal must reach the Inbox's direct bus.db read, not just spawns/file reads —
        // a bundled pack active means a demo-scoped bus, never the real one.
        let mut cfg = default_config();
        cfg.active_pack = Some(samples_packs_dir().join("demo-investor").display().to_string());
        assert_eq!(db_path(&cfg), demo_seal_state_dir().join("bus.db"));
        // No pack (or a personal pack) → the normal resolution chain.
        cfg.active_pack = None;
        assert_ne!(db_path(&cfg), demo_seal_state_dir().join("bus.db"));
        cfg.active_pack = Some("/somewhere/else/my-pack".into());
        assert_ne!(db_path(&cfg), demo_seal_state_dir().join("bus.db"));
    }
}

/// Where the app's bus lives: the local SQLite file (default) or a served bus over HTTP.
#[derive(Clone, Debug, PartialEq)]
pub enum BusEndpoint {
    Local(PathBuf),
    Remote { url: String, token: String },
}

/// Resolve the bus endpoint. Three rules, in order:
/// 1. **Demo seal trumps remote** — while a bundled demo pack is active the console renders its
///    sealed (empty) bus and nothing else; a configured mesh bus is real state and must not leak
///    into demo mode.
/// 2. `bus_url` set (non-blank) → remote. The token rides along even when missing/blank — the
///    remote client surfaces "token missing" as an actionable error instead of silently falling
///    back to a local file the user explicitly opted out of (a silent fallback would render an
///    EMPTY local bus and read as "no events" — worse than an error).
/// 3. Otherwise the local file via [`db_path`] — the unchanged OOTB path.
pub fn bus_endpoint(cfg: &AppConfig) -> BusEndpoint {
    if active_bundled_pack(cfg).is_some() {
        return BusEndpoint::Local(db_path(cfg));
    }
    match cfg.bus_url.as_deref().map(str::trim) {
        Some(url) if !url.is_empty() => BusEndpoint::Remote {
            url: url.trim_end_matches('/').to_string(),
            token: cfg.bus_token.as_deref().map(str::trim).unwrap_or_default().to_string(),
        },
        _ => BusEndpoint::Local(db_path(cfg)),
    }
}

/// Same resolution order as the Python side: explicit config > HARNESS_BUS_DB > default.
pub fn db_path(cfg: &AppConfig) -> PathBuf {
    // Demo-pack full seal: the Inbox/badge/poller read a demo-scoped (empty) bus while a bundled
    // demo persona is active — real standing-agent events never render in demo mode. Trumps every
    // other resolution source by design.
    if active_bundled_pack(cfg).is_some() {
        return demo_seal_state_dir().join("bus.db");
    }
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
/// Atomic: written to a sibling temp file, then renamed over — an in-place truncate-and-write
/// leaves a zeroed registry if the process dies mid-save, and a zeroed registry reads as corrupt
/// on the next launch (losing the user's producers/pack selection to a reseed).
pub fn save(cfg: &AppConfig) -> Result<(), String> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let body = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, body).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &path).map_err(|e| e.to_string())
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

/// Where the sample weight packs live. Two-stage resolution:
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

/// The app-bundle engine dir (`<resource_dir>/engine`), set once at Tauri setup — the
/// installed-app fallback for [`engine`]. Same `OnceLock` rationale as [`RESOURCE_PACKS_DIR`].
static RESOURCE_ENGINE_DIR: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();

/// Called once from the app `setup` hook, BEFORE any command can run.
pub fn set_resource_engine_dir(dir: Option<PathBuf>) {
    let _ = RESOURCE_ENGINE_DIR.set(dir);
}

/// The uv project console spawns run `hn` against, and whether it's the bundled copy.
pub struct Engine {
    pub project_dir: PathBuf,
    /// true → the engine shipped inside the app resources (read-only install dir); its venv must
    /// live in the user-writable state dir ([`engine_venv_dir`]) instead of `<project>/.venv`.
    pub bundled: bool,
}

/// Where the `hn` engine lives. Two candidates, mirroring [`samples_packs_dir`]:
///   - the engine bundled INTO the app itself (`bundle.resources` ships a staged copy of the uv
///     project → `<resource_dir>/engine`; see `scripts/stage-engine.mjs`);
///   - a development checkout at the conventional `{harness_home}/projects/harness`, when present.
///
/// The order is build-profile-dependent, and that's the product contract: a RELEASE build is a
/// self-contained install — it prefers its own bundled engine on every platform, so an installed
/// console runs the exact engine its version shipped with, never whatever state a repo checkout
/// happens to be in. A DEV build (`tauri dev`) prefers the repo so engine edits reflect without a
/// rebuild. Each falls back to the other; None when neither exists.
pub fn engine() -> Option<Engine> {
    let repo = harness_home().join("projects/harness");
    let repo_engine = repo
        .join("pyproject.toml")
        .is_file()
        .then_some(Engine { project_dir: repo, bundled: false });
    let bundled_engine = match RESOURCE_ENGINE_DIR.get() {
        Some(Some(dir)) if dir.join("pyproject.toml").is_file() => {
            Some(Engine { project_dir: dir.clone(), bundled: true })
        }
        _ => None,
    };
    if cfg!(debug_assertions) {
        repo_engine.or(bundled_engine)
    } else {
        bundled_engine.or(repo_engine)
    }
}

/// The venv for the bundled engine — user-writable, survives app upgrades, never touches the
/// read-only install dir. Passed to uv via `UV_PROJECT_ENVIRONMENT`.
pub fn engine_venv_dir() -> PathBuf {
    harness_home().join(".local/state/harness/engine-venv")
}

/// Is this pack dir one of the BUNDLED demo packs (vs. a personal pack the user loaded)?
/// Checked against both bundled-pack homes (repo `samples/packs` + the app-resource copy).
pub fn is_bundled_pack(dir: &Path) -> bool {
    if dir.starts_with(samples_packs_dir()) {
        return true;
    }
    matches!(RESOURCE_PACKS_DIR.get(), Some(Some(packs)) if dir.starts_with(packs))
}

/// The active pack — but only when it's a bundled demo pack. This is the demo-pack full seal's
/// gate: while a bundled sample persona is loaded, the console must render NOTHING but that pack —
/// no lane fallback, no vault browse, no producer read may reach a real corpus that happens to
/// exist on the machine. Personal packs (loaded via "Load Weight Pack…") keep blend semantics:
/// lanes the pack lacks fall back to the real corpus, by design.
pub fn active_bundled_pack(cfg: &AppConfig) -> Option<PathBuf> {
    let dir = cfg
        .active_pack
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(expand_home)?;
    is_bundled_pack(&dir).then_some(dir)
}

/// The root every vault read (VAULT browser, viz discovery, File widget fallback) resolves
/// against: the demo pack itself while one is active (the pack IS the whole corpus — a missing
/// file reads empty, never the real vault), else the real tracker path.
pub fn vault_root(cfg: &AppConfig) -> PathBuf {
    active_bundled_pack(cfg).unwrap_or_else(|| tracker_path(cfg))
}

/// Writable state root for demo mode — the bus db and any spawned-tool state (pulse log, seen
/// caches) land here while a bundled demo pack is active, so the Inbox/status surfaces render a
/// clean standby instead of the machine's real standing-agent activity.
pub fn demo_seal_state_dir() -> PathBuf {
    harness_home().join(".local/state/harness/demo-seal")
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
        bus_url: None,
        bus_token: None,
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
        // An existing-but-unreadable registry is preserved for inspection, never silently
        // replaced — the reseed below then treats this launch as a first run.
        let _ = fs::rename(&path, path.with_extension("json.bak"));
    }
    let mut cfg = default_config();
    // First run (no config on disk): default to the bundled fictional pack so a fresh / published
    // install renders sample data OOTB and NEVER the real-or-absent corpus (the b15.1 OOTB-safe
    // state). Fires ONLY when the config file is absent — an existing user/dev config is returned
    // verbatim above, so the dev daily-driver (pack-less = real corpus) is untouched.
    cfg.active_pack = default_pack_dir().map(|p| p.to_string_lossy().into_owned());
    let _ = save(&cfg);
    cfg
}
