// Watchman service worker — the browser half of the bus web-push transport (server half:
// harness/bus/push.py). Deliberately tiny: push → notification, click → console. No fetch
// handler, no caching — the console's offline story is the native app's job; a stale-asset
// cache here would only make web deploys mysterious.
//
// Lives in public/ so Vite copies it verbatim to dist/sw.js — root scope, required for the
// registration in src/push.ts to control the whole console.

self.addEventListener("push", (event) => {
  // Payload contract (push.py _payload_for): { title, summary, lane, kind, subject, severity }.
  // Parse defensively — a malformed payload must still surface SOMETHING (a silent push handler
  // risks the browser revoking the subscription for not showing a notification).
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "WATCHMAN", summary: event.data ? event.data.text() : "" };
  }
  const title = data.title || "WATCHMAN";
  const body = data.summary || [data.kind, data.subject].filter(Boolean).join(" · ");
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      // tag coalesces re-sends of the same signal (kind:subject) instead of stacking banners —
      // the notification twin of the bus's idempotency-key discipline.
      tag: [data.kind, data.subject].filter(Boolean).join(":") || undefined,
      icon: "/icons/watchman-192.png",
      badge: "/icons/watchman-192.png",
      data: { lane: data.lane, severity: data.severity },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  // Focus an open console if one exists; otherwise open one. The Inbox is the start URL —
  // deep-linking a specific event is a future nicety, not this transport's contract.
  event.waitUntil(
    (async () => {
      const wins = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of wins) {
        if ("focus" in w) return w.focus();
      }
      return clients.openWindow("/");
    })(),
  );
});
