// Web-push client + PushBell — jsdom has no real push stack, so these tests cover exactly what
// is testable without one: the key-format bridge, the tri-state support probe, and the bell's
// rendering contract per state. The subscribe flow itself is exercised against a real browser
// (localhost is a secure context) — see docs/WEB-CONSOLE.md → Push notifications.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import PushBell from "./PushBell";
import { pushSupport, urlBase64ToUint8Array } from "./push";

afterEach(() => {
  cleanup();
  // undo any navigator/window stubs a test installed
  delete (navigator as unknown as Record<string, unknown>).serviceWorker;
  delete (window as unknown as Record<string, unknown>).PushManager;
  delete (window as unknown as Record<string, unknown>).Notification;
});

function stubServiceWorker(): void {
  Object.defineProperty(navigator, "serviceWorker", {
    value: { register: async () => ({ pushManager: { getSubscription: async () => null } }) },
    configurable: true,
  });
}

describe("urlBase64ToUint8Array", () => {
  it("decodes unpadded base64url including -/_ chars", () => {
    // "??>" encodes to Pz8-_g style chars when the bytes force the url-safe alphabet
    const bytes = urlBase64ToUint8Array("_-8");
    expect(Array.from(bytes)).toEqual([255, 239]);
  });

  it("round-trips the shape of a P-256 uncompressed point (65 bytes)", () => {
    const point = new Uint8Array(65).map((_, i) => (i * 7) % 256);
    let bin = "";
    point.forEach((b) => (bin += String.fromCharCode(b)));
    const b64url = btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(Array.from(urlBase64ToUint8Array(b64url))).toEqual(Array.from(point));
  });
});

describe("pushSupport", () => {
  it("is unsupported in a bare jsdom (no serviceWorker at all)", () => {
    expect(pushSupport()).toBe("unsupported");
  });

  it("is needs-install with a SW but no Notification/PushManager (iOS Safari in-browser)", () => {
    stubServiceWorker();
    expect(pushSupport()).toBe("needs-install");
  });

  it("is supported once Notification + PushManager exist", () => {
    stubServiceWorker();
    (window as unknown as Record<string, unknown>).PushManager = class {};
    (window as unknown as Record<string, unknown>).Notification = class {};
    expect(pushSupport()).toBe("supported");
  });
});

describe("PushBell", () => {
  it("renders nothing when push is unsupported (no dead chrome on the baseplate)", () => {
    const { container } = render(<PushBell />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the install hint on tap in the needs-install state", () => {
    stubServiceWorker();
    render(<PushBell />);
    const btn = screen.getByRole("button", { name: /push/i });
    expect(screen.queryByText(/HOME SCREEN/)).toBeNull();
    fireEvent.click(btn); // discoverable, gesture-driven — matches the iOS guidance
    expect(screen.getByText(/ADD TO HOME SCREEN/)).toBeInTheDocument();
  });

  it("renders the disarmed toggle when supported and no subscription exists", async () => {
    stubServiceWorker();
    (window as unknown as Record<string, unknown>).PushManager = class {};
    (window as unknown as Record<string, unknown>).Notification = class {};
    render(<PushBell />);
    expect(await screen.findByText("◇ PUSH")).toBeInTheDocument();
  });
});
