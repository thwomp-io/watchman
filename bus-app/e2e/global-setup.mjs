import { writeFileSync } from "node:fs";
import { start } from "./sealed-serve.mjs";

export default async function globalSetup() {
  const s = await start();
  process.env.E2E_TOKEN = s.token;
  writeFileSync(new URL("./.run.json", import.meta.url), JSON.stringify({ pid: s.proc.pid, root: s.root, token: s.token }));
  return async () => {
    s.proc.kill();
    const { rmSync } = await import("node:fs");
    rmSync(s.root, { recursive: true, force: true });
  };
}
