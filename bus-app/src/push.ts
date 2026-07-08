// Web-push client plumbing — the browser console's side of the bus push transport.
//
// This is a WEB-ONLY surface: the native console already posts OS notifications through its own
// authorized identity, so nothing here runs under Tauri (PushBell is simply not rendered there).
// It talks to the bus server's /api/push/* routes DIRECTLY (bearer token via the transport
// module's bootstrap) rather than through the /api/invoke RPC door: push subscription is a
// browser↔server affair with no commands.rs twin to mirror — forcing it through the door would
// invent a fake native command.
//
// iOS reality (the reason for the tri-state support probe): Safari only exposes Notification/
// PushManager to a PWA INSTALLED to the Home Screen (16.4+), and `subscribe` must run inside a
// user gesture. localhost counts as a secure context, so the whole flow is testable locally
// without TLS; off-localhost the console must be served over HTTPS.

import { bootstrapToken } from "./transport";

export type PushSupport = "supported" | "needs-install" | "unsupported";

export function pushSupport(): PushSupport {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return "unsupported";
  // Notification/PushManager absent but SW present = the iOS-Safari-in-browser signature →
  // the actionable hint is "install to Home Screen", not "unsupported".
  if (!("Notification" in window) || !("PushManager" in window)) return "needs-install";
  return "supported";
}

/** applicationServerKey wants raw bytes; the server hands out unpadded base64url (RFC 7515). */
export function urlBase64ToUint8Array(base64url: string): Uint8Array {
  const padded = base64url + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const base = (import.meta.env?.VITE_API_BASE as string | undefined) ?? "";

async function api<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tok = bootstrapToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const res = await fetch(`${base}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text().catch(() => res.statusText)}`);
  return (await res.json()) as T;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // Idempotent: register() with an already-active identical script is a cheap no-op, so every
  // caller can just ask — no module-level singleton state to get stale.
  return navigator.serviceWorker.register("/sw.js");
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== "supported") return null;
  try {
    return await (await registration()).pushManager.getSubscription();
  } catch {
    return null;
  }
}

/** A short non-identifying device label so the operator can tell subscriptions apart in
 * `hn bus push-keys` — platform-class only, never a hostname or account. */
function deviceLabel(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPad/.test(ua)) return "iOS PWA";
  if (/Android/.test(ua)) return "Android";
  if (/Mac/.test(ua)) return "macOS browser";
  return "browser";
}

/** Subscribe this browser/PWA. MUST be called from a user gesture (iOS enforces it for the
 * permission prompt). Returns false when the user denies permission. */
export async function subscribeDevice(): Promise<boolean> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return false;
  const { key } = await api<{ key: string }>("/api/push/vapid-key");
  const reg = await registration();
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true, // the only mode browsers grant — every push shows a notification
    applicationServerKey: urlBase64ToUint8Array(key).buffer as ArrayBuffer,
  });
  await api("/api/push/subscribe", { subscription: sub.toJSON(), label: deviceLabel() });
  return true;
}

/** Unsubscribe BOTH sides — browser first (stops delivery), then the server row (stops sends).
 * Server-side failure is tolerable: the next send 404/410s and the server self-prunes. */
export async function unsubscribeDevice(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  try {
    await api("/api/push/unsubscribe", { endpoint });
  } catch {
    /* server prunes on next send — see docstring */
  }
}

/** The verification affordance: ask the server to push a test banner back to THIS device. */
export async function sendTestPush(): Promise<{ sent: number; pruned: number; failed: number }> {
  const sub = await currentSubscription();
  return api("/api/push/test", sub ? { endpoint: sub.endpoint } : {});
}
