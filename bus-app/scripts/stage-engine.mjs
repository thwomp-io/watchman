// Stage the hn engine (the uv project the console spawns) into src-tauri/engine so the bundler
// ships it as an app resource. Installed builds run `uv run --project <resources>/engine hn …`,
// so a downloaded console works with no repo checkout — uv is the only prerequisite.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const busApp = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(busApp);
const stage = join(busApp, "src-tauri", "engine");

rmSync(stage, { recursive: true, force: true });
mkdirSync(stage, { recursive: true });

for (const f of ["pyproject.toml", "uv.lock", ".python-version"]) {
  cpSync(join(repo, f), join(stage, f));
}
cpSync(join(repo, "src", "harness"), join(stage, "src", "harness"), {
  recursive: true,
  filter: (src) => !src.includes("__pycache__"),
});

console.log(`staged engine → ${stage}`);
