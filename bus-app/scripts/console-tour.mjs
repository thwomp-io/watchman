#!/usr/bin/env node
// The agent's eye, in MOTION: record a scripted tour of the served console as video, for
// producing demo media (GIFs) without a human at the screen. Sibling of console-shot.mjs —
// same token-injection and settle mechanics, plus Playwright's recordVideo and a small
// step language. Born from the audit finding that the original hero GIFs were hand-recorded
// against real state; this makes demo capture reproducible against a SEALED demo instance.
//
// Usage:
//   node scripts/console-tour.mjs <url> <out.webm> [--viewport WxH] [--token-file PATH]
//                                  [--theme dark|light] [--steps "wait:3000,group:Finance,
//                                   sub:Unwind,wait:2500,sub:Market,wait:2500,zone:INBOX,wait:3000"]
//
// Step language (comma-separated):
//   zone:NAME   — click a top-level zone tab (DASH/INBOX/VIZ/VAULT/SURFACES)
//   group:NAME  — click a DASH group in the .strip (Finance/Travel/Career/...)
//   sub:NAME    — click a DASH subtab in the .substrip (Core/Unwind/Market/Tickets/...)
//   wait:MS     — dwell (readability pause; becomes GIF hold time)
//   settle      — bounded wait for ACQUIRING widgets to clear
//
// The video lands wherever Playwright puts it inside --video-dir (we rename to <out.webm>).
// Convert downstream with the house ffmpeg GIF pipeline; ALWAYS frame-extract-review the
// output before shipping (the gate this script exists to serve).

import { readFileSync, renameSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { homedir } from "node:os";
import { chromium } from "playwright";

function arg(flag, dflt) {
  const i = process.argv.indexOf(flag);
  return i > -1 ? process.argv[i + 1] : dflt;
}

const [url, out] = process.argv.slice(2);
if (!url || !out) {
  console.error("usage: console-tour.mjs <url> <out.webm> [--viewport WxH] [--token-file PATH] [--theme T] [--steps ...]");
  process.exit(2);
}
const [vw, vh] = (arg("--viewport", "1280x800")).split("x").map(Number);
const viewport = { width: vw || 1280, height: vh || 800 };
const tokenFile = arg("--token-file", `${homedir()}/.config/harness/bus-token`);
const theme = arg("--theme", "dark");
const steps = (arg("--steps", "settle,wait:3000")).split(",").map((s) => s.trim()).filter(Boolean);

const videoDir = dirname(out) + "/.tour-video";
mkdirSync(videoDir, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport,
  recordVideo: { dir: videoDir, size: viewport },
});
const page = await context.newPage();
if (tokenFile) {
  const token = readFileSync(tokenFile, "utf8").trim();
  await page.addInitScript((t) => window.localStorage.setItem("watchman.token", t), token);
}
await page.addInitScript((th) => window.localStorage.setItem("watchman.theme", th), theme);
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".widget, .inbox, .vault, .viz-rail", { timeout: 30_000 }).catch(() => {});

async function settle(maxMs = 20_000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const busy = await page.getByText("ACQUIRING").count().catch(() => 0);
    if (!busy) break;
    await page.waitForTimeout(400);
  }
}

for (const step of steps) {
  const [op, val] = step.split(":");
  if (op === "wait") await page.waitForTimeout(Number(val) || 1000);
  else if (op === "settle") await settle();
  else if (op === "zone") await page.click(`nav.zones button:has-text("${val}")`).catch(() => {});
  else if (op === "group") await page.click(`.strip button:has-text("${val}")`).catch(() => {});
  else if (op === "sub") await page.click(`.substrip button:has-text("${val}")`).catch(() => {});
  else console.error(`unknown step: ${step}`);
}

await page.close();          // flushes the recording
const video = await page.video()?.path?.().catch(() => null);
await context.close();
await browser.close();
if (video) {
  renameSync(video, out);
  console.log(`wrote ${out} (${viewport.width}x${viewport.height}, ${steps.length} steps)`);
} else {
  console.error("no video produced");
  process.exit(1);
}
