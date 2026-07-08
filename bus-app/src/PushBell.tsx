// The baseplate bell — subscribe/unsubscribe this device to bus web-push (alert/warn only;
// the wire never pushes). Lives in the footer, not the masthead: an armed transport is ambient
// chassis state like BUS.DB ONLINE, not a navigation zone. Web-only — App.tsx renders it only
// off-Tauri (the native console has its own OS notifications).

import { useEffect, useState } from "react";
import { currentSubscription, pushSupport, sendTestPush, subscribeDevice, unsubscribeDevice } from "./push";

type BellState = "probing" | "off" | "on" | "busy" | "denied";

export default function PushBell() {
  const support = pushSupport();
  const [state, setState] = useState<BellState>("probing");
  const [hint, setHint] = useState(false);
  const [flash, setFlash] = useState(""); // transient TEST feedback, self-clearing

  useEffect(() => {
    if (support !== "supported") return;
    let alive = true;
    void currentSubscription().then((sub) => {
      if (alive) setState(sub ? "on" : "off");
    });
    return () => {
      alive = false;
    };
  }, [support]);

  if (support === "unsupported") return null; // nothing actionable to say — stay off the plate

  if (support === "needs-install") {
    // iOS Safari in-browser: push only exists once the PWA is on the Home Screen. The bell
    // stays visible as a DISCOVERABLE hint (tap → the how), never a dead control.
    return (
      <span className="push-bell">
        <button className="bell-btn dim" title="Push alerts need the installed app"
                onClick={() => setHint((h) => !h)}>
          ◇ PUSH
        </button>
        {hint && <span className="bell-hint">ADD TO HOME SCREEN (SHARE ▸) TO ENABLE ALERTS</span>}
      </span>
    );
  }

  const toggle = async () => {
    // subscribe() runs inside this click handler on purpose — iOS requires the permission
    // prompt to ride a user gesture; hoisting it to an effect would silently never prompt.
    const was = state;
    setState("busy");
    try {
      if (was === "on") {
        await unsubscribeDevice();
        setState("off");
      } else {
        setState((await subscribeDevice()) ? "on" : "denied");
      }
    } catch {
      setState(was === "probing" ? "off" : was);
    }
  };

  const test = async () => {
    setFlash("…");
    try {
      const r = await sendTestPush();
      setFlash(r.sent > 0 ? "SENT" : "NO TARGET");
    } catch {
      setFlash("FAILED");
    }
    setTimeout(() => setFlash(""), 4000);
  };

  return (
    <span className="push-bell">
      <button
        className={`bell-btn ${state === "on" ? "armed" : ""}`}
        disabled={state === "busy" || state === "probing"}
        title={
          state === "on" ? "Push alerts armed on this device — click to disarm"
          : state === "denied" ? "Notifications denied — re-allow in browser/OS settings"
          : "Arm push alerts on this device (alert/warn signals only)"
        }
        onClick={() => void toggle()}
      >
        {state === "on" ? "◆ PUSH ARMED" : state === "denied" ? "◇ PUSH DENIED" : "◇ PUSH"}
      </button>
      {state === "on" && (
        <button className="bell-btn" title="Send a test notification to this device"
                onClick={() => void test()}>
          {flash || "TEST"}
        </button>
      )}
    </span>
  );
}
