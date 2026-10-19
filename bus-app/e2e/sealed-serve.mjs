// The E2E studio: a SEALED console on the demo pack. Every read and spawn renders the persona —
// corpus, weights, bus, state, cache and config (dashboards + .env) all point into a scratch dir,
// so a test run can never touch, or be shaped by, the operator's real data or keys.

import { spawn } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const APP = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(APP, "..");
export const packDir = (name) => join(REPO, "samples/packs", name);
export const PACK = packDir(process.env.E2E_PACK ?? "demo-investor");
export const PORT = Number(process.env.E2E_PORT ?? 8899);
export const BASE = `http://127.0.0.1:${PORT}/`;

export function stage(pack = PACK) {
  const root = mkdtempSync(join(tmpdir(), "watchman-e2e-"));
  const config = join(root, "config");
  mkdirSync(join(config, "dashboards"), { recursive: true });
  for (const f of readdirSync(join(pack, "dashboards"))) {
    const d = JSON.parse(readFileSync(join(pack, "dashboards", f), "utf8"));
    for (const w of d.widgets ?? []) {
      // pack widgets carry a default "." cwd; point them at THIS checkout instead
      if (w.source?.type === "command") w.source.cwd = REPO;
    }
    writeFileSync(join(config, "dashboards", f), JSON.stringify(d));
  }
  writeFileSync(join(config, ".env"), "# sealed: no keys\n");
  const token = "e2e-" + Math.random().toString(36).slice(2);
  writeFileSync(join(root, "token"), token, { mode: 0o600 });
  return { root, config, token };
}

export function sealedEnv(s, pack = PACK) {
  return {
    ...process.env,
    TRACKER_PATH: pack,
    WEIGHTS_PACK: pack,
    HARNESS_CONFIG_DIR: s.config,
    HARNESS_STATE_DIR: join(s.root, "state"),
    HARNESS_CACHE_DIR: join(s.root, "cache"),
    HARNESS_BUS_DB: join(s.root, "bus.db"),
  };
}

export async function start(pack = PACK) {
  const s = stage(pack);
  const env = sealedEnv(s, pack);
  const proc = spawn("uv", ["run", "hn", "bus", "serve", "--host", "127.0.0.1", "--port", String(PORT),
    "--token-file", join(s.root, "token"), "--ui", join(APP, "dist")], { cwd: REPO, env, stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`${BASE}health`);
      if (r.ok) return { ...s, proc, env };
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  proc.kill();
  throw new Error(`sealed serve did not come up on ${BASE}`);
}

export function stop(proc, root) {
  proc?.kill();
  if (root) rmSync(root, { recursive: true, force: true });
}

export { cpSync };

// The recording studio, as a command: a sealed console on any sample pack, optionally with that pack's
// scripted CHAT running under the same seal (the replay never calls a model).
//   node e2e/sealed-serve.mjs --pack demo-growth [--replay] [--autoplay]
// Prints the URL + token; Ctrl-C stops everything and removes the scratch dir.
if (import.meta.url === `file://${process.argv[1]}`) {
  const argv = process.argv.slice(2);
  const name = argv.includes("--pack") ? argv[argv.indexOf("--pack") + 1] : "demo-investor";
  const pack = packDir(name);
  const s = await start(pack);
  let replay = null;
  if (argv.includes("--replay")) {
    const args = ["run", "hn", "pocket", "replay", ...(argv.includes("--autoplay") ? ["--autoplay", "--gap", "3"] : [])];
    replay = spawn("uv", args, { cwd: REPO, env: s.env, stdio: "ignore" });
  }
  console.log(`sealed console: ${BASE}?token=${s.token}\npack: ${name}${replay ? " · CHAT replay running" : ""}`);
  const down = () => { replay?.kill(); stop(s.proc, s.root); process.exit(0); };
  process.on("SIGINT", down);
  process.on("SIGTERM", down);
}
