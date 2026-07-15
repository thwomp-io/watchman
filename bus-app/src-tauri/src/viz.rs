//! Vault viz discovery — finds the harness's viz DATA JSONs and shape-sniffs
//! their type. The workshop pattern co-locates data JSONs with the docs they illustrate (a
//! `visuals/` sibling dir holds the static SVG renders); this module walks the vault for that
//! signature and never writes anything (read-only, like every bus-app vault touch).

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct VizEntry {
    pub path: String,      // vault-relative, e.g. finance/allocation/allocation.json
    pub doc: String,       // parent dir vault-relative (the owning document/workshop)
    pub name: String,      // file stem
    pub viz_type: String,  // sankey | treemap | pies | line | unknown
    pub title: String,     // from the data JSON when present
    pub supported: bool,   // renderable by the current interactive component set
}

const SKIP_DIRS: &[&str] = &[".git", ".obsidian", ".beads", "node_modules", "tmp", "screenshots"];
const MAX_DEPTH: usize = 7;

/// Shape-sniff the viz type from the data contract (data JSONs don't carry a type field — the
/// static engine takes type as a CLI arg; signatures are distinctive enough to sniff).
fn sniff(v: &serde_json::Value) -> &'static str {
    let has = |k: &str| v.get(k).is_some();
    // the live-contract shapes (vest calendar / trap-map ladder / bead family tree) — most
    // specific first, ahead of sankey (bead-tree's beads+edges deliberately avoid nodes/links)
    if has("windows") && has("vests") {
        return "vest-timeline";
    }
    if let Some(symbols) = v.get("symbols").and_then(|s| s.as_array()) {
        if symbols.first().is_some_and(|s| s.get("rungs").is_some()) {
            return "ladder";
        }
    }
    if v.get("beads").is_some_and(|b| b.is_array()) && v.get("edges").is_some_and(|e| e.is_array()) {
        return "bead-tree";
    }
    if has("nodes") && has("links") {
        return "sankey";
    }
    if let Some(nodes) = v.get("nodes").and_then(|n| n.as_array()) {
        if nodes.iter().all(|n| n.get("value").is_some()) {
            return "treemap";
        }
    }
    if has("pies") {
        return "pies";
    }
    if has("restaurants") {
        return "food-bank";
    }
    if has("dayStart") && (has("items") || has("availability")) {
        return "schedule";
    }
    if has("axes") && has("candidates") {
        return "compare";
    }
    if has("axes") && has("rows") {
        return "matrix";
    }
    // rank-bar: `rows` whose entries carry `parts` (no `axes` — disjoint from matrix/compare above).
    if let Some(rows) = v.get("rows").and_then(|r| r.as_array()) {
        if rows.first().is_some_and(|r| r.get("parts").is_some()) {
            return "rank-bar";
        }
    }
    if has("rings") && has("points") {
        return "radial"; // drive-time ring map — static-only for now
    }
    // scatter: bare top-level `points` with NUMERIC x + a categorical `group` (vs line's ISO-date x
    // under `series`). Must precede the `points`→line catch-all.
    if let Some(pts) = v.get("points").and_then(|p| p.as_array()) {
        if pts.first().is_some_and(|p| {
            p.get("x").is_some_and(|x| x.is_number()) && p.get("group").is_some()
        }) {
            return "scatter";
        }
    }
    if has("points") || has("series") {
        return "line";
    }
    "unknown"
}

fn walk(dir: &Path, depth: usize, vault: &Path, out: &mut Vec<VizEntry>) {
    if depth > MAX_DEPTH {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else { return };
    let children: Vec<PathBuf> = entries.flatten().map(|e| e.path()).collect();
    let has_visuals = children.iter().any(|p| p.is_dir() && p.file_name().is_some_and(|n| n == "visuals"));

    for child in &children {
        if child.is_dir() {
            let name = child.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if !SKIP_DIRS.contains(&name) {
                walk(child, depth + 1, vault, out);
            }
        } else if has_visuals && child.extension().is_some_and(|e| e == "json") {
            let Ok(text) = fs::read_to_string(child) else { continue };
            let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else { continue };
            let viz_type = sniff(&value);
            let rel = |p: &Path| {
                p.strip_prefix(vault).unwrap_or(p).to_string_lossy().to_string()
            };
            out.push(VizEntry {
                path: rel(child),
                doc: rel(dir),
                name: child.file_stem().and_then(|s| s.to_str()).unwrap_or("?").to_string(),
                viz_type: viz_type.to_string(),
                title: value.get("title").and_then(|t| t.as_str()).unwrap_or("").to_string(),
                supported: matches!(viz_type,
                    "sankey" | "treemap" | "pies" | "line" | "matrix" | "compare" | "schedule"
                        | "food-bank" | "scatter" | "rank-bar" | "vest-timeline" | "ladder"
                        | "bead-tree"),
            });
        }
    }
}

pub fn discover(vault: &Path) -> Vec<VizEntry> {
    let mut out = Vec::new();
    walk(vault, 0, vault, &mut out);
    out.sort_by(|a, b| (!a.supported, &a.doc, &a.name).cmp(&(!b.supported, &b.doc, &b.name)));
    out
}
