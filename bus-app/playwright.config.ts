import { defineConfig } from "@playwright/test";

// Console E2E against a sealed demo-pack serve (e2e/sealed-serve.mjs). Build first: `npm run build`.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.mjs",
  timeout: 120_000,
  workers: 2,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${process.env.E2E_PORT ?? 8899}/`, viewport: { width: 1600, height: 1000 } },
});
