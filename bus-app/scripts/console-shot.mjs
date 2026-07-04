#!/usr/bin/env node
// The agent's eye: render the served web console headlessly at any viewport,
// wait for the widgets to settle, and write a screenshot the agent can Read — the self-verifying
// render loop extended to the whole webview. Layout bugs are the machine's job; the operator's
// eyeball is for taste.
//
// Usage:
//   node scripts/console-shot.mjs <url> <out.png> [--viewport phone|tablet|desktop|WxH]
//                                                 [--token-file ~/.config/harness/bus-token]
//                                                 [--tab DASH|INBOX|VIZ|VAULT|SURFACES]
//                                                 [--settle-ms 4000]
//
// Notes for future readers:
// - The token is read from DISK and injected into localStorage via addInitScript (runs before
//   the app boots) — it never transits argv/env logs or chat. Pass --token-file "" to skip
//   (you'll screenshot the token prompt / degraded state, which is itself a valid test).
// - Settle heuristic: wait for at least one .widget, then for all ACQUIRING markers to clear
//   (bounded), then a short paint delay. networkidle is useless here — the 30s bus poll keeps
//   the connection warm forever.
// - Covers everything EXCEPT Rust-native surfaces (tray/notifications/TCC) — those stay
//   "worked = the operator saw it".

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { chromium } from "playwright";

const VIEWPORTS = {
  phone: { width: 390, height: 844 }, // iPhone-class viewport
  tablet: { width: 820, height: 1180 },
  desktop: { width: 1600, height: 1000 },
};

function arg(flag, dflt) {
  const i = process.argv.indexOf(flag);
  return i > -1 ? process.argv[i + 1] : dflt;
}

let [url, out] = process.argv.slice(2);
// A bare filename (no slash) lands in the operator's reviewable shot dropzone when one is
// configured (CONSOLE_SHOT_DIR) — the operator can then eyeball any shot the agent took, and the
// dir can live outside the repo (gitignored / skipped by the vault browser). Unset, bare names
// resolve against the CWD like any other relative path.
const SHOT_DIR = process.env.CONSOLE_SHOT_DIR;
if (out && !out.includes("/") && SHOT_DIR) out = `${SHOT_DIR}/${out}`;
if (!url || !out) {
  console.error("usage: console-shot.mjs <url> <out.png> [--viewport phone|desktop|WxH] " +
    "[--token-file PATH] [--tab NAME] [--settle-ms N]");
  process.exit(2);
}
const vpArg = arg("--viewport", "desktop");
const viewport = VIEWPORTS[vpArg] ??
  (() => {
    const [w, h] = vpArg.split("x").map(Number);
    return { width: w || 1600, height: h || 1000 };
  })();
const tokenFile = arg("--token-file", `${homedir()}/.config/harness/bus-token`);
const tab = arg("--tab", "");
const settleMs = Number(arg("--settle-ms", "4000"));

const browser = await chromium.launch();
const page = await browser.newPage({ viewport, deviceScaleFactor: 2 });
if (tokenFile) {
  const token = readFileSync(tokenFile, "utf8").trim();
  await page.addInitScript((t) => window.localStorage.setItem("watchman.token", t), token);
}
await page.goto(url, { waitUntil: "domcontentloaded" });
// --standalone: emulate the installed-PWA media state (display-mode: standalone) via CDP —
// Playwright's emulateMedia doesn't expose display-mode, but the DevTools protocol does. Sent
// AFTER navigation (pre-goto it didn't stick against the fresh document). Notch insets still
// can't be emulated — safe-area env() reads 0 headlessly: verify GEOMETRY here, notch on-device.
if (process.argv.includes("--standalone")) {
  // force the installed-app root class (headless Chromium can't emulate display-mode; the app
  // keys its standalone CSS off html.pwa, set from real matchMedia in production — main.tsx)
  await page.evaluate(() => document.documentElement.classList.add("pwa"));
}
await page.waitForSelector(".widget, .inbox, .vault, .viz-rail", { timeout: 30_000 }).catch(() => {});
if (tab) await page.click(`nav.zones button:has-text("${tab}")`).catch(() => {});
// bounded settle: let ACQUIRING widgets resolve (cold spawns are ~0.7s each after the perf pass)
const deadline = Date.now() + 25_000;
while (Date.now() < deadline) {
  const busy = await page.getByText("ACQUIRING").count().catch(() => 0);
  if (!busy) break;
  await page.waitForTimeout(500);
}
await page.waitForTimeout(settleMs); // paint/chart-animation grace
// fullPage captures the whole scrollable document — which mis-renders position:fixed chrome
// (bars paint at scroll-top, not the viewport edge). --standalone implies viewport-true capture
// (that's what the installed app's screen IS); --viewport-only forces it anywhere.
const viewportOnly = process.argv.includes("--standalone") || process.argv.includes("--viewport-only");
await page.screenshot({ path: out, fullPage: !viewportOnly });
await browser.close();
console.log(`wrote ${out} (${viewport.width}x${viewport.height}${tab ? ` · ${tab}` : ""})`);
