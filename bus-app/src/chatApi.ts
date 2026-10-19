// The pocket agent's door calls (the CHAT tab). Served console only — the door reads the runner's state
// files and drops prompts/flags into its spool; the native app has no such commands, so these reject there.

import { transport } from "./transport";
import type { PocketQueued, PocketSnapshot } from "./generated/contracts";

const invoke = <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> =>
  transport().invoke<T>(cmd, args);

export const pocketState = (since: number): Promise<PocketSnapshot> => invoke("pocket_state", { since });
export const pocketSend = (text: string): Promise<PocketQueued> => invoke("pocket_send", { text });
export const pocketStop = (): Promise<boolean> => invoke("pocket_stop");
export const pocketReset = (): Promise<boolean> => invoke("pocket_reset");
