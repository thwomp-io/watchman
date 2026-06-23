import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Frontend component-test tier (tracker — the webview-behavior gate). The React render layer runs in
// jsdom with the Tauri IPC mocked (src/test/setup.ts), so component logic — effects, state, the
// dispatch-on-kind, the pack-swap layout refetch — is exercised without the bundled app or a display.
// What it does NOT cover: the real Rust↔webview boundary (that's the Rust unit tests + the "the maintainer saw
// it" deploy check). Run with `npm test`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
