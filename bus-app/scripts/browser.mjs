// One launch posture for every Playwright script in this package.
//
// - The Chromium sandbox is ON everywhere. Playwright's `chromiumSandbox` defaults to false, and then
//   Chromium is started with --no-sandbox: a renderer exploit would run with the user's own file
//   access (.env files, SSH keys, tokens). With the sandbox on, it also needs a sandbox escape.
// - Trusted pages only (the served console, the sealed demo): the bundled Chromium is fine.
// - Third-party pages (the apply fill): a browser that patches itself. Playwright pins one Chromium
//   build per release, and Chrome ships security fixes most weeks, so the bundled build is usually
//   behind; installed Google Chrome (`channel: "chrome"`) updates itself. Either way the build is
//   checked against Google's own list of current stable versions before any page loads, and an
//   unverifiable or stale build is refused unless the caller explicitly accepts it.

import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

export const SANDBOXED = Object.freeze({ chromiumSandbox: true });

const require = createRequire(import.meta.url);
const { version: APP_VERSION } = require("../package.json");
const UA = `harness-bus-app/${APP_VERSION}`;

// Google Chrome's app bundle, per platform; Playwright's `channel: "chrome"` launches this install.
const CHROME_PLIST = {
  darwin: "/Applications/Google Chrome.app/Contents/Info.plist",
};

export function bundledVersion() {
  // browsers.json isn't in playwright-core's `exports`; locate the package by its exported package.json
  const dir = dirname(require.resolve("playwright-core/package.json"));
  const browsers = JSON.parse(readFileSync(join(dir, "browsers.json"), "utf8"));
  const c = browsers.browsers.find((b) => b.name === "chromium");
  return c?.browserVersion ?? null;
}

function chromeVersion() {
  const plist = CHROME_PLIST[process.platform];
  if (!plist || !existsSync(plist)) return null;
  try {
    return execFileSync("/usr/libexec/PlistBuddy", ["-c", "Print :CFBundleShortVersionString", plist], {
      encoding: "utf8",
    }).trim();
  } catch {
    return null;
  }
}

export function compareVersions(a, b) {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return Math.sign(d);
  }
  return 0;
}

/** The newest Chrome stable version Google lists for this platform (its version-history API). */
export async function newestStable(fetchImpl = fetch) {
  const platform = process.platform === "darwin" ? (process.arch === "arm64" ? "mac_arm64" : "mac")
    : process.platform === "win32" ? "win64" : "linux";
  const url = `https://versionhistory.googleapis.com/v1/chrome/platforms/${platform}/channels/stable/versions?pageSize=10`;
  const resp = await fetchImpl(url, { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(15000) });
  if (!resp.ok) throw new Error(`Chrome version history answered HTTP ${resp.status}`);
  const versions = ((await resp.json()).versions ?? []).map((v) => v.version).filter(Boolean);
  if (!versions.length) throw new Error("Chrome version history listed no stable versions");
  return versions.sort(compareVersions).at(-1);
}

/** Decide which browser opens third-party pages. Pure apart from the injected lookups (tested). */
export function chooseOpenWebBrowser({ chrome, bundled, stable, allowStale }) {
  if (chrome) {
    if (compareVersions(chrome, stable) >= 0) {
      return { ok: true, launch: { ...SANDBOXED, channel: "chrome" }, note: `Google Chrome ${chrome} (current)` };
    }
    // Chrome patches itself but applies an update only on restart
    if (allowStale) {
      return { ok: true, launch: { ...SANDBOXED, channel: "chrome" },
        note: `Google Chrome ${chrome} is behind stable ${stable} — accepted by --allow-stale-browser` };
    }
    return { ok: false, error: `Google Chrome ${chrome} is behind the current stable ${stable} — restart Chrome to `
      + "finish its update (or pass --allow-stale-browser)" };
  }
  if (bundled && compareVersions(bundled, stable) >= 0) {
    return { ok: true, launch: { ...SANDBOXED }, note: `bundled Chromium ${bundled} (current)` };
  }
  if (allowStale) {
    return { ok: true, launch: { ...SANDBOXED },
      note: `bundled Chromium ${bundled} is behind stable ${stable} — accepted by --allow-stale-browser (sandbox on)` };
  }
  return { ok: false, error: `the bundled Chromium ${bundled} is behind Chrome stable ${stable} and misses its security `
    + "fixes; third-party pages need a browser that patches itself — install Google Chrome (used automatically, "
    + "sandbox on), or pass --allow-stale-browser to accept the gap" };
}

/** Launch options for third-party pages, or a refusal. Fails closed when freshness can't be checked. */
export async function openWebLaunch({ allowStale = false, fetchImpl = fetch } = {}) {
  let stable;
  try {
    stable = await newestStable(fetchImpl);
  } catch (e) {
    if (!allowStale) {
      return { ok: false, error: `couldn't verify the browser is current (${e.message}) — refusing third-party `
        + "pages; retry, or pass --allow-stale-browser" };
    }
    return { ok: true, launch: chromeVersion() ? { ...SANDBOXED, channel: "chrome" } : { ...SANDBOXED },
      note: `freshness unverified (${e.message}) — accepted by --allow-stale-browser (sandbox on)` };
  }
  return chooseOpenWebBrowser({ chrome: chromeVersion(), bundled: bundledVersion(), stable, allowStale });
}
