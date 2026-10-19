#!/usr/bin/env node
// The browser fill: open the ATS's hosted
// application form in a HEADED Playwright Chromium under a dedicated persistent profile, fill
// every packet field, upload the résumé, screenshot the result, and STOP with the window open.
// The human clicks Submit; `hn career apply --submit --confirm --receipt` records it afterwards.
//
// Usage:
//   node scripts/apply-fill.mjs --packet <folder>/packet.json --profile <dir> --shot <out.png>
//                               [--after <post-submit.png>] [--wait-min 30] [--no-wait]
//
// Posture:
// - Playwright is already in this package's devDependencies (the agent's eye uses it); the
//   browser is the Chromium build Playwright already installed. Zero new dependencies, no new
//   browser download, nothing branded or signed in to any account.
// - HEADED, never headless; no stealth / fingerprint flags. A real window with a human at the
//   keyboard is the honest posture — reCAPTCHA scores the human's click, not this script.
// - The profile dir is dedicated + empty (cookies/history isolated from the daily browser).
// - This script NEVER clicks Submit, never touches EEO/consent/captcha controls, and never
//   answers a question the packet left null.
//
// Adapters were built against the real DOM:
// - Greenhouse (job-boards.greenhouse.io): inputs carry `id` = the API field key (first_name,
//   email, question_NNN…); dropdowns are combobox inputs (aria-autocomplete=list) — type the
//   label, pick the [role=option]; the résumé input is `#resume` (visually hidden).
// - Ashby (jobs.ashbyhq.com): text/email/tel/number inputs carry `id` = the field path; select
//   questions render as RADIO groups (`input[name=<path>]`, one per option, label beside);
//   booleans render as a CHECKBOX (`input[name=<path>]`); location is a labelled autocomplete.
//
// Output: progress lines on stderr; ONE `RESULT {json}` line on stdout the CLI parses.

import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";

function arg(flag, dflt) {
  const i = process.argv.indexOf(flag);
  return i > -1 ? process.argv[i + 1] : dflt;
}
const packetPath = arg("--packet", "");
const profileDir = arg("--profile", "");
const shotPath = arg("--shot", "");
const afterPath = arg("--after", "");
const waitMin = Number(arg("--wait-min", "30"));
const noWait = process.argv.includes("--no-wait");
if (!packetPath || !profileDir || !shotPath) {
  console.error("usage: apply-fill.mjs --packet <packet.json> --profile <dir> --shot <out.png> [--after <png>] [--wait-min N] [--no-wait]");
  process.exit(2);
}

const packet = JSON.parse(await readFile(packetPath, "utf8"));
const folder = dirname(resolve(packetPath));
const log = (m) => console.error(`[fill] ${m}`);
const report = []; // {key, label, status: filled|skipped|unresolved, note}
const mark = (f, status, note = "") => report.push({ key: f.key, label: f.label, status, note });

const labelOf = (v) => (Array.isArray(v) ? v : [v]).map((x) => String(x));
// Node has no CSS.escape; an attribute selector takes any id verbatim (quotes escaped).
const byId = (key) => `[id="${String(key).replace(/"/g, '\\"')}"]`;

// ----- Greenhouse -----
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

async function ghCombo(page, key, labels) {
  const input = page.locator(byId(key));
  if (!(await input.count())) return "no such field on the page";
  for (const label of labels) {
    await input.click();
    await input.fill(label);
    // Scope to the combobox's OWN listbox (aria-controls) when it says which; otherwise to the
    // VISIBLE options only, matched on the whole text. A page-wide substring match on "No"
    // landed on the phone widget's hidden country list ("Norway", "Norfolk Island"…) and every
    // "No" answer went unfilled while "Yes" worked.
    const owns = await input.getAttribute("aria-controls").catch(() => null);
    const exact = new RegExp(`^\\s*${escapeRe(label)}\\s*$`, "i");
    const scope = owns ? page.locator(`[id="${owns}"]`) : page;
    let opt = scope.locator('[role="option"]:visible', { hasText: exact }).first();
    try {
      await opt.waitFor({ timeout: 3000 });
    } catch {
      opt = page.locator('[role="option"]:visible', { hasText: exact }).first();
      try {
        await opt.waitFor({ timeout: 2000 });
      } catch {
        await page.keyboard.press("Escape").catch(() => {});
        return `option not offered: ${label}`;
      }
    }
    await opt.click();
    // assert: the combobox's control now shows the chosen label (react-select's single-value)
    const control = input.locator('xpath=ancestor::*[contains(@class, "control")][1]');
    const shown = (await control.count()) ? (await control.innerText()).trim() : "";
    if (shown && !shown.toLowerCase().includes(label.toLowerCase())) {
      return `select shows "${shown.slice(0, 60)}" not "${label}"`;
    }
  }
  return "";
}

// Greenhouse's location question: the API names it `location` (+ derived longitude/latitude);
// the form renders ONE autocomplete `#candidate-location` and computes the coordinates itself.
const GH_LOCATION_KEYS = { location: "candidate-location" };
const GH_DERIVED = new Set(["longitude", "latitude"]);

async function ghAutocomplete(page, id, text) {
  const input = page.locator(byId(id));
  if (!(await input.count())) return "no such field on the page";
  await input.click();
  await input.fill(String(text));
  const owns = await input.getAttribute("aria-controls").catch(() => null);
  const scope = owns ? page.locator(`[id="${owns}"]`) : page;
  const opt = scope.locator('[role="option"]:visible').first();
  try {
    await opt.waitFor({ timeout: 6000 });
    await opt.click();
  } catch {
    await page.keyboard.press("Escape").catch(() => {});
    return `no suggestion appeared for "${text}" — pick it by hand`;
  }
  return "";
}

async function ghCheckboxGroup(page, key, labels) {
  // A multi-select renders as `fieldset#key[]` of checkboxes `#key[]_<value>`, label beside.
  // The API already names multi-value fields with the trailing `[]` — don't double it.
  const base = key.replace(/\[\]$/, "");
  const boxes = page.locator(`input[type="checkbox"][id^="${base}[]_"]`);
  const n = await boxes.count();
  if (!n) return null; // not a checkbox group — the caller falls back to the combobox
  for (const label of labels) {
    let hit = false;
    for (let i = 0; i < n; i++) {
      const el = boxes.nth(i);
      const text = await el.evaluate((e) => (e.labels?.[0]?.innerText || e.closest("label")?.innerText || "").trim());
      if (text.toLowerCase() === label.toLowerCase()) {
        if (!(await el.isChecked())) await el.check({ force: true });
        if (!(await el.isChecked())) return `checkbox did not take: ${label}`;
        hit = true;
        break;
      }
    }
    if (!hit) return `option not offered: ${label}`;
  }
  return "";
}

async function ghLookup(page, key, text) {
  // A typeahead over a list too big to ship (schools, countries). Pick ONLY a suggestion that
  // equals the authored text or starts with it ("United States" → "United States +1"); never
  // guess — a wrong school silently picked is worse than an unresolved field. When nothing
  // matches, shorten the typed text word by word just to surface what the list DOES offer, and
  // put those names in the report so the human (or the next answers.yaml edit) can choose.
  const input = page.locator(byId(key));
  if (!(await input.count())) return "no such field on the page";
  const owns = await input.getAttribute("aria-controls").catch(() => null);
  const scope = owns ? page.locator(`[id="${owns}"]`) : page;
  const wanted = String(text).trim();
  const words = wanted.split(/\s+/);
  let offered = [];
  for (let n = words.length; n >= 1; n--) {
    const typed = words.slice(0, n).join(" ");
    await input.click();
    await input.fill(typed);
    try {
      await scope.locator('[role="option"]:visible').first().waitFor({ timeout: n === words.length ? 6000 : 3000 });
    } catch {
      continue;
    }
    offered = (await scope.locator('[role="option"]:visible').allInnerTexts()).map((s) => s.trim());
    const hit = offered.findIndex((o) => o.toLowerCase() === wanted.toLowerCase() || o.toLowerCase().startsWith(wanted.toLowerCase()));
    if (hit >= 0) {
      await scope.locator('[role="option"]:visible').nth(hit).click();
      if (offered[hit].toLowerCase() !== wanted.toLowerCase()) log(`  ~ ${key}: picked "${offered[hit]}" for "${wanted}"`);
      return "";
    }
    break; // suggestions exist but none match — report them
  }
  await input.fill("").catch(() => {});
  await page.keyboard.press("Escape").catch(() => {});
  if (!offered.length) return `no suggestion appeared for "${wanted}" — pick it by hand`;
  return `"${wanted}" is not in the list; it offers: ${offered.slice(0, 6).join(" · ")}${offered.length > 6 ? " …" : ""} — set the exact name in answers.yaml`;
}

async function fillGreenhouse(page, f) {
  if (GH_DERIVED.has(f.key)) return "derived"; // reported as skipped, never unresolved
  if (GH_LOCATION_KEYS[f.key]) return ghAutocomplete(page, GH_LOCATION_KEYS[f.key], f.value);
  if (f.type === "lookup") return ghLookup(page, f.key, f.value);
  const sel = byId(f.key);
  switch (f.type) {
    case "file": {
      const input = page.locator(sel);
      if (!(await input.count())) return "no such file input";
      await input.setInputFiles(resolve(folder, String(f.value)));
      return "";
    }
    case "multiselect": {
      const viaBoxes = await ghCheckboxGroup(page, f.key, labelOf(f.value));
      if (viaBoxes !== null) return viaBoxes;
      return ghCombo(page, f.key, labelOf(f.value));
    }
    case "select":
    case "boolean":
      return ghCombo(page, f.key, labelOf(f.value));
    default: {
      const input = page.locator(sel);
      if (!(await input.count())) return "no such field on the page";
      await input.fill(String(f.value));
      return f.type === "phone" ? "" : assertValue(input, String(f.value)); // the tel widget reformats
    }
  }
}

// ----- Ashby -----
async function ashbyBoolean(page, key, label) {
  // A Boolean renders as a Yes | No pair of <button aria-pressed> inside the field entry; the
  // hidden checkbox named by the path is NOT the state the form reads — toggling it "worked"
  // for Yes by coincidence and did nothing for No, silently. Click the button
  // whose text is the answer, then ASSERT aria-pressed on it.
  const input = page.locator(`input[name="${key}"]`).first();
  if (!(await input.count())) return "no such field on the page";
  const entry = input.locator('xpath=ancestor::*[contains(@class, "field-entry")][1]');
  const scope = (await entry.count()) ? entry : input.locator("xpath=ancestor::div[3]");
  const want = /^(yes|true|1)$/i.test(label) ? "Yes" : "No";
  const btn = scope.locator("button", { hasText: new RegExp(`^\\s*${want}\\s*$`, "i") }).first();
  if (!(await btn.count())) return `no ${want} button beside the field`;
  await btn.click();
  await page.waitForTimeout(150);
  const pressed = await btn.getAttribute("aria-pressed");
  if (pressed !== "true") {
    const other = scope.locator("button", { hasText: new RegExp(`^\\s*${want === "Yes" ? "No" : "Yes"}\\s*$`, "i") }).first();
    return `toggle did not take: ${want} aria-pressed=${pressed}, the other=${await other.getAttribute("aria-pressed").catch(() => "?")}`;
  }
  return "";
}

async function ashbyRadios(page, key, labels) {
  // Selects render as a radio group whose inputs carry the field path in their `id`
  // (`<formId>_<path>…`) — the `name` is the form-level group id, not the path (seen live).
  const inputs = page.locator(`input[type="radio"][id*="${key}"], input[type="radio"][name="${key}"]`);
  const n = await inputs.count();
  if (!n) return "no such field on the page";
  for (const label of labels) {
    let hit = null;
    for (let i = 0; i < n; i++) {
      const el = inputs.nth(i);
      const text = await el.evaluate((e) => (e.labels?.[0]?.innerText || e.closest("label")?.innerText || "").trim());
      if (text.toLowerCase() === label.toLowerCase()) { hit = el; break; }
    }
    if (!hit) return `option not offered: ${label}`;
    // the input sits under a styled control; a plain .check() waits on visibility forever
    if (!(await hit.isChecked())) await hit.evaluate((e) => e.click());
    if (!(await hit.isChecked())) await hit.check({ force: true });
    if (!(await hit.isChecked())) return `radio did not take: ${label}`;
  }
  return "";
}

async function fillAshby(page, f) {
  const idLoc = page.locator(byId(f.key));
  switch (f.type) {
    case "file": {
      const input = (await idLoc.count()) ? idLoc : page.locator('input[type="file"]').first();
      await input.setInputFiles(resolve(folder, String(f.value)));
      return "";
    }
    case "boolean":
      return ashbyBoolean(page, f.key, labelOf(f.value)[0]);
    case "select":
    case "multiselect":
      return ashbyRadios(page, f.key, labelOf(f.value));
    case "location": {
      // The location field is an UNLABELED autocomplete (aria-autocomplete=list) — a label
      // lookup lands on the neighbouring "Location - Zip Code" number input instead.
      const box = page.locator('input[aria-autocomplete="list"]').first();
      if (!(await box.count())) return "location autocomplete not found";
      await box.click();
      await box.fill(String(f.value));
      const opt = page.locator('[role="option"]').first();
      try {
        await opt.waitFor({ timeout: 5000 });
        await opt.click();
      } catch {
        return "no location suggestion appeared — pick it by hand";
      }
      return "";
    }
    default: {
      const input = (await idLoc.count()) ? idLoc : page.locator(`input[name="${f.key}"]`);
      if (!(await input.count())) return "no such field on the page";
      await input.fill(String(f.value));
      return assertValue(input, String(f.value));
    }
  }
}

// ----- post-action state checks (every adapter asserts the control's STATE, not the click) -----
async function assertValue(input, want) {
  const got = await input.inputValue().catch(() => null);
  if (got === null) return "";
  return got.trim() === String(want).trim() ? "" : `field shows "${got.slice(0, 60)}" not "${want.slice(0, 60)}"`;
}

// ----- run -----
const adapters = { greenhouse: fillGreenhouse, ashby: fillAshby };
const fill = adapters[packet.ats];
if (!fill) {
  console.log(`RESULT ${JSON.stringify({ ok: false, error: `no browser adapter for ats ${packet.ats}` })}`);
  process.exit(1);
}

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  viewport: { width: 1280, height: 1000 },
});
const page = context.pages()[0] ?? (await context.newPage());
log(`opening ${packet.apply_url}`);
const nav = await page.goto(packet.apply_url, { waitUntil: "domcontentloaded", timeout: 60000 });
const status = nav ? nav.status() : 0;
if (status >= 400) {
  // The ATS answered with an error page (a 503 "lost in the weeds" was seen live) — say so
  // instead of reporting every field as missing, and let the operator retry in a minute.
  await page.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
  console.log(`RESULT ${JSON.stringify({ ok: false, error: `the ATS returned HTTP ${status} at ${packet.apply_url} — not a form; try again in a minute (screenshot: ${shotPath})` })}`);
  await context.close().catch(() => {});
  process.exit(1);
}
await page.waitForTimeout(2500);
// Greenhouse's job page carries the form below the description; scroll it into view so the
// operator sees what got filled.
const form = page.locator("form").first();
if (await form.count()) await form.scrollIntoViewIfNeeded().catch(() => {});

for (const f of packet.fields) {
  if (f.kind === "compliance") { mark(f, "skipped", "EEO/consent — yours at the form"); continue; }
  if (f.value === null || f.value === undefined || f.value === "") { mark(f, "skipped", "no answer in the packet"); continue; }
  try {
    const err = await fill(page, f);
    if (err === "derived") { mark(f, "skipped", "derived by the form from the location you pick"); }
    else if (err) { mark(f, "unresolved", err); log(`✗ ${f.label}: ${err}`); }
    else { mark(f, "filled"); log(`✓ ${f.label}`); }
  } catch (e) {
    mark(f, "unresolved", String(e.message || e).split("\n")[0].slice(0, 120));
    log(`✗ ${f.label}: ${e.message}`);
  }
}
await page.waitForTimeout(800);
await page.screenshot({ path: shotPath, fullPage: true });
log(`filled-form screenshot → ${shotPath}`);

const counts = report.reduce((a, r) => ((a[r.status] = (a[r.status] || 0) + 1), a), {});
// A page with NONE of the adapter's fields is the wrong page (a role description, a redirect),
// not a form to wait on — say so and stop (an embed posting's apply_url opened the
// company's JD and the run sat waiting for Submit with nothing filled).
const missing = report.filter((r) => r.status === "unresolved" && /no such/.test(r.note)).length;
if ((counts.filled || 0) === 0 && missing > 0) {
  console.log(`RESULT ${JSON.stringify({ ok: false, error: `no ${packet.ats} form fields found at ${page.url()} — ${missing} field(s) missing; this is not the application form (screenshot: ${shotPath})`, counts, report, shot: shotPath })}`);
  await context.close().catch(() => {});
  process.exit(1);
}
const startUrl = page.url();
let submitted = false;
let finalUrl = startUrl;
if (!noWait) {
  log(`READY — review the window and click Submit yourself (waiting up to ${waitMin} min; close the window to stop)`);
  const deadline = Date.now() + waitMin * 60_000;
  let closed = false;
  context.on("close", () => { closed = true; });
  page.on("close", () => { closed = true; });
  while (Date.now() < deadline && !closed) {
    await new Promise((r) => setTimeout(r, 2000));
    let url = "";
    try { url = page.url(); } catch { closed = true; break; }
    if (url !== startUrl && !url.startsWith(startUrl + "#")) {
      submitted = true;
      finalUrl = url;
      await page.waitForTimeout(2500);
      if (afterPath) {
        try { await page.screenshot({ path: afterPath, fullPage: true }); log(`post-submit screenshot → ${afterPath}`); } catch {}
      }
      break;
    }
  }
}
console.log(`RESULT ${JSON.stringify({ ok: true, ats: packet.ats, counts, report, shot: shotPath, submitted, final_url: finalUrl, after: submitted && afterPath && existsSync(afterPath) ? afterPath : "" })}`);
await context.close().catch(() => {});
