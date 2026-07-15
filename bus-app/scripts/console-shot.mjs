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
//                                                 [--viz-item NAME]  (VIZ rail entry, case-insensitive)
//                                                 [--dash-group Finance] [--dash-lane Global]
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
    "[--token-file PATH] [--tab NAME] [--viz-item NAME] [--settle-ms N]");
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
const dashGroup = arg("--dash-group", "");
const dashLane = arg("--dash-lane", "");
const vizItem = arg("--viz-item", "");
const hoverSel = arg("--hover", ""); // CSS selector to hover before capture (tooltip verification)
const clickSel = arg("--click", ""); // CSS selector to click before capture (modal/popup verification)
const settleMs = Number(arg("--settle-ms", "4000"));

const browser = await chromium.launch();
const page = await browser.newPage({ viewport, deviceScaleFactor: 2 });
if (tokenFile) {
  const token = readFileSync(tokenFile, "utf8").trim();
  await page.addInitScript((t) => window.localStorage.setItem("watchman.token", t), token);
  // --theme dark|light pins the console theme for the shot (the theme layer honors an explicit
  // stored choice over prefers-color-scheme, so this beats headless Chromium's ambient scheme).
  const themeArg = process.argv.find((a) => a.startsWith("--theme="))?.split("=")[1]
    ?? (process.argv.includes("--theme") ? process.argv[process.argv.indexOf("--theme") + 1] : null);
  if (themeArg) {
    await page.addInitScript((th) => window.localStorage.setItem("watchman.theme", th), themeArg);
  }
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
// DASH is 2-level (group row → lane subtabs) — navigate both when asked, waiting for each
// row to exist so a fresh config-discovered lane (no rebuild) is reachable the same session.
if (dashGroup) {
  await page.waitForTimeout(400);
  await page.click(`button:has-text("${dashGroup}")`).catch(() => {});
}
if (dashLane) {
  await page.waitForTimeout(400);
  await page.click(`button:has-text("${dashLane}")`).catch(() => {});
}
// VIZ rail is grouped (top toggle → doc → item) — --viz-item selects a rail entry by
// case-insensitive name, expanding collapsed top groups (chev "▸") first so fresh vault
// discoveries are reachable in a cold headless session (2026-07-10 — the eye extended to the
// VIZ rail; the DASH lanes got the same treatment earlier).
if (vizItem) {
  await page.waitForTimeout(600);
  for (const t of await page.locator('.viz-top-toggle:has-text("▸")').all())
    await t.click().catch(() => {});
  await page.waitForTimeout(400);
  await page.locator(".viz-rail button", { hasText: new RegExp(vizItem, "i") }).first()
    .click().catch(() => {});
}
// bounded settle: let ACQUIRING widgets resolve (cold spawns are ~0.7s each after the perf pass)
const deadline = Date.now() + 25_000;
while (Date.now() < deadline) {
  const busy = await page.getByText("ACQUIRING").count().catch(() => 0);
  if (!busy) break;
  await page.waitForTimeout(500);
}
await page.waitForTimeout(settleMs); // paint/chart-animation grace
// --hover: park the cursor on a selector so hover-born UI (tooltips, relationship cards) is IN
// the capture — the agent's eye can now verify what only exists under the pointer.
if (hoverSel) {
  await page.locator(hoverSel).first().hover({ force: true }).catch(() => {});
  await page.waitForTimeout(500);
}
if (clickSel) {
  await page.locator(clickSel).first().click({ force: true }).catch(() => {});
  await page.waitForTimeout(700); // popup content fetch + paint
}
// fullPage captures the whole scrollable document — which mis-renders position:fixed chrome
// (bars paint at scroll-top, not the viewport edge). --standalone implies viewport-true capture
// (that's what the installed app's screen IS); --viewport-only forces it anywhere.
const viewportOnly = process.argv.includes("--standalone") || process.argv.includes("--viewport-only");
await page.screenshot({ path: out, fullPage: !viewportOnly });
await browser.close();
console.log(`wrote ${out} (${viewport.width}x${viewport.height}${tab ? ` · ${tab}` : ""})`);
