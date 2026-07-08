// Installed-PWA detection → a root class (html.pwa): CSS keys installed-app-only ergonomics off
// the CLASS, not the media query directly, because headless Chromium can't emulate
// display-mode — this makes the standalone layout drivable by the agent's eye (console-shot
// --standalone forces the class) while production derives it from the real media state.
if (window.matchMedia?.("(display-mode: standalone)").matches) {
  document.documentElement.classList.add("pwa");
}
// Web-console push plumbing: (re)register the service worker at boot so a subscribed device
// picks up sw.js updates on every visit — NOT only when the bell is touched. Web-only: the
// Tauri webview has no push stack (the native console notifies through the OS), and jsdom/
// older browsers simply skip. Registration is idempotent; failures are non-fatal chrome.
if (!("__TAURI_INTERNALS__" in window) && "serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initTheme } from "./theme";

// Theme boot — index.html's inline script already stamped data-theme pre-paint; this re-applies
// through the module (same resolution), registers the OS-scheme listener, and points the
// theme-color metas at the active chassis.
initTheme();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
