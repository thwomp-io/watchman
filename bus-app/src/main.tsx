// Installed-PWA detection → a root class (html.pwa): CSS keys installed-app-only ergonomics off
// the CLASS, not the media query directly, because headless Chromium can't emulate
// display-mode — this makes the standalone layout drivable by the agent's eye (console-shot
// --standalone forces the class) while production derives it from the real media state.
if (window.matchMedia?.("(display-mode: standalone)").matches) {
  document.documentElement.classList.add("pwa");
}
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
