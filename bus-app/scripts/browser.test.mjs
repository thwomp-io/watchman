import { describe, expect, it } from "vitest";
import { bundledVersion, chooseOpenWebBrowser, compareVersions, newestStable, openWebLaunch, SANDBOXED } from "./browser.mjs";

const STABLE = "157.0.8103.26";

describe("compareVersions", () => {
  it("orders dotted builds numerically, not lexically", () => {
    expect(compareVersions("156.0.8078.4", "156.0.8078.80")).toBe(-1);
    expect(compareVersions("157.0.8103.26", "156.0.8078.80")).toBe(1);
    expect(compareVersions("157.0.8103.26", "157.0.8103.26")).toBe(0);
  });
});

describe("chooseOpenWebBrowser", () => {
  it("prefers a current Google Chrome, sandboxed", () => {
    const r = chooseOpenWebBrowser({ chrome: STABLE, bundled: "156.0.8078.4", stable: STABLE, allowStale: false });
    expect(r).toMatchObject({ ok: true, launch: { channel: "chrome", chromiumSandbox: true } });
  });

  it("refuses a Chrome that hasn't restarted onto its update, unless accepted", () => {
    const behind = { chrome: "156.0.8078.80", bundled: "156.0.8078.4", stable: STABLE };
    expect(chooseOpenWebBrowser({ ...behind, allowStale: false })).toMatchObject({ ok: false });
    expect(chooseOpenWebBrowser({ ...behind, allowStale: false }).error).toMatch(/restart Chrome/);
    expect(chooseOpenWebBrowser({ ...behind, allowStale: true })).toMatchObject({ ok: true, launch: { channel: "chrome" } });
  });

  it("refuses the stale bundled Chromium on third-party pages, naming both builds", () => {
    const r = chooseOpenWebBrowser({ chrome: null, bundled: "156.0.8078.4", stable: STABLE, allowStale: false });
    expect(r.ok).toBe(false);
    expect(r.error).toContain("156.0.8078.4");
    expect(r.error).toContain(STABLE);
    expect(r.error).toMatch(/install Google Chrome/);
  });

  it("accepting the gap still keeps the sandbox on", () => {
    const r = chooseOpenWebBrowser({ chrome: null, bundled: "156.0.8078.4", stable: STABLE, allowStale: true });
    expect(r).toMatchObject({ ok: true, launch: { chromiumSandbox: true } });
    expect(r.launch.channel).toBeUndefined();
  });

  it("a bundled build that is current needs no flag", () => {
    expect(chooseOpenWebBrowser({ chrome: null, bundled: STABLE, stable: STABLE, allowStale: false }).ok).toBe(true);
  });

  it("every launch it returns is sandboxed", () => {
    for (const chrome of [null, "156.0.8078.80", STABLE]) {
      for (const allowStale of [false, true]) {
        const r = chooseOpenWebBrowser({ chrome, bundled: "156.0.8078.4", stable: STABLE, allowStale });
        if (r.ok) expect(r.launch.chromiumSandbox).toBe(true);
      }
    }
    expect(SANDBOXED.chromiumSandbox).toBe(true);
  });
});

describe("freshness source", () => {
  const listing = (versions) => async () => ({ ok: true, json: async () => ({ versions: versions.map((version) => ({ version })) }) });

  it("takes the newest stable across majors", async () => {
    expect(await newestStable(listing(["156.0.8078.80", STABLE, "156.0.8078.79"]))).toBe(STABLE);
  });

  it("fails closed when Google can't be reached", async () => {
    const down = async () => { throw new Error("getaddrinfo ENOTFOUND"); };
    const r = await openWebLaunch({ fetchImpl: down });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/couldn't verify/);
    expect((await openWebLaunch({ fetchImpl: down, allowStale: true })).launch.chromiumSandbox).toBe(true);
  });

  it("an HTTP error is a failure, not an empty answer", async () => {
    const r = await openWebLaunch({ fetchImpl: async () => ({ ok: false, status: 503 }) });
    expect(r).toMatchObject({ ok: false });
    expect(r.error).toMatch(/HTTP 503/);
  });
});

describe("the installed Playwright", () => {
  it("reports its bundled Chromium build (the real lookup, not an injected one)", () => {
    expect(bundledVersion()).toMatch(/^\d+\.0\.\d+\.\d+$/);
  });
});
