import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// Every DASH lane of the sealed demo console renders: no error walls, loading settles, and table
// tiles show real rows (the grid's min-height clamp once left them one row tall).

const run = JSON.parse(readFileSync(new URL("./.run.json", import.meta.url), "utf8")) as { token: string };

interface Dash { lane: string; title: string; group?: string; widgets: { id: string; kind: string }[] }

async function dashboards(baseURL: string): Promise<Dash[]> {
  const r = await fetch(`${baseURL}api/invoke/list_dashboards`, {
    method: "POST", headers: { Authorization: `Bearer ${run.token}`, "Content-Type": "application/json" }, body: "{}",
  });
  const body = await r.json();
  return (typeof body === "string" ? JSON.parse(body) : body) as Dash[];
}

async function open(page: Page, group: string, title: string) {
  await page.addInitScript((t) => window.localStorage.setItem("watchman.token", t), run.token);
  await page.goto("/");
  await page.locator("nav.zones button", { hasText: "DASH" }).click();
  await page.locator(".strip button", { hasText: new RegExp(`^\\s*${group}\\s*$`) }).first().click();
  const sub = page.locator(".substrip button", { hasText: new RegExp(`^\\s*${title}\\s*$`) });
  if (await sub.count()) await sub.first().click();
}

// one lane per test, discovered from the console's own dashboard list
const lanes: Dash[] = await dashboards(`http://127.0.0.1:${process.env.E2E_PORT ?? 8899}/`);

for (const d of lanes) {
  test(`${d.group ?? d.title} ▸ ${d.title} renders`, async ({ page }) => {
    await open(page, d.group ?? d.title, d.title);
    // the lane really switched: its widget count is on screen
    await expect(page.locator(".dash-grid > .widget, .studio-grid .widget")).toHaveCount(d.widgets.length);
    // every widget reaches a terminal state (AS OF … or FAILED), not just "not acquiring yet"
    const pending = page.locator(".widget .surface-when").filter({ hasNotText: /AS OF|FAILED|\d+ \/ \d+|NO TAKES YET/ });
    await expect(pending).toHaveCount(0, { timeout: 90_000 });
    // a keyless demo names its missing key (an honest setup state); any other error is a bug
    const errors = (await page.locator(".surface-error").allTextContents())
      .filter((e) => !/_KEY is not set|keys? (are |is )?not set/i.test(e));
    expect(errors, "error walls").toEqual([]);
    // table tiles show at least min(3, rows) data rows inside their visible box
    const clipped = await page.$$eval(".widget", (ws) => ws.flatMap((w) => {
      const rows = [...w.querySelectorAll("tbody tr")];
      if (!rows.length) return [];
      const box = w.getBoundingClientRect();
      const visible = rows.filter((r) => { const b = r.getBoundingClientRect(); return b.top >= box.top && b.bottom <= box.bottom + 1; }).length;
      const want = Math.min(3, rows.length);
      return visible < want ? [`${w.querySelector("header")?.textContent?.slice(0, 40)}: ${visible}/${rows.length} rows visible`] : [];
    }));
    expect(clipped, "clipped tables").toEqual([]);
  });
}

// The bank tile's photo arrows page the gallery in a real browser. A transform-centred arrow once
// jumped off the pointer on mousedown (the global button:active nudge), so the click landed on the
// photo and opened the popup instead of paging — jsdom cannot see that; a real click can.
test("Travel ▸ Visits bank tile pages its photos without opening the popup", async ({ page }) => {
  await open(page, "Travel", "Visits");
  const tile = page.locator(".bank-tile", { hasText: "Sample History Museum" });
  await expect(tile.locator(".bank-count")).toHaveText("1/3", { timeout: 60_000 });
  await tile.hover();
  await tile.locator(".bank-nav.next").click();
  await expect(tile.locator(".bank-count")).toHaveText("2/3");
  await expect(page.locator("[role=dialog]")).toHaveCount(0);
  await tile.locator(".bank-tile-label").click();
  const dialog = page.locator("[role=dialog]", { hasText: "Sample History Museum" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(dialog.locator(".bank-count")).toHaveText("2/3");
});

test("Travel ▸ Visits schedule draws the days and opens a linked item's bank card", async ({ page }) => {
  await open(page, "Travel", "Visits");
  const grid = page.locator(".vsched-grid");
  await expect(grid.locator(".vsched-dayhead")).toHaveCount(6, { timeout: 60_000 });
  await expect(grid.locator(".vsched-item")).toHaveCount(5);
  await grid.locator(".vsched-item", { hasText: "Sample History Museum" }).click();
  await expect(page.locator("[role=dialog]", { hasText: "Sample History Museum" })).toBeVisible();
});

test("Travel ▸ Visits picker switches the bank to another visit", async ({ page }) => {
  await open(page, "Travel", "Visits");
  const picker = page.locator(".picker-strip");
  await expect(picker.locator(".picker-tile")).toHaveCount(2, { timeout: 60_000 });
  await expect(picker.locator(".picker-tile.active")).toContainText("Thanksgiving");
  await picker.locator(".picker-tile", { hasText: "Cousins" }).click();
  const bank = page.locator(".widget.kind-bank");
  await bank.locator(".bank-kind", { hasText: "Restaurants" }).click();
  await expect(bank.locator(".bank-tile", { hasText: "Sample Lakeside Grill" })).toBeVisible({ timeout: 60_000 });
  // the schedule follows too: this visit planned nothing, so its two days show open
  await expect(page.locator(".widget.kind-schedule .vsched-dayhead")).toHaveCount(2, { timeout: 60_000 });
});

test("Travel ▸ Trips tab renders the picker", async ({ page }) => {
  await open(page, "Travel", "Trips");
  await expect(page.locator(".widget.kind-picker")).toBeVisible({ timeout: 60_000 });
});
