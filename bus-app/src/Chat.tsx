// CHAT — one conversation with the pocket agent (the lane the runner on the always-on node answers as).
// Signal's "Note to Self" shape: the tab opens straight into the conversation. Replies stream (the
// runner's live file, polled), tool activity folds into one expandable "working… (N steps)" row, and
// the header pill carries the status line's intent ("am I near a wall?") with the detail behind a tap.
// Served console only: the door reads the runner's state and drops prompts into its spool.

import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { pocketReset, pocketSend, pocketState, pocketStop } from "./chatApi";
import "./Chat.css";
import type { PocketEntry, PocketQueued, PocketStatus } from "./generated/contracts";
import {
  type ChatItem, derivePill, fmtDuration, fmtTokens, groupEntries, resetIn, type Step,
} from "./chatModel";

const FAST_MS = 800;   // while a reply is in flight
const IDLE_MS = 4000;

function clockOf(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function Steps({ steps, working }: { steps: Step[]; working: boolean }) {
  const [open, setOpen] = useState(false);
  const refused = steps.filter((s) => s.denied).length;
  return (
    <div className={`chat-steps${open ? " open" : ""}`}>
      <button type="button" className="chat-steps-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className={`chat-steps-dot${working ? " live" : ""}`} />
        {working ? "working…" : "worked"} ({steps.length} step{steps.length === 1 ? "" : "s"})
        {refused > 0 && <em className="chat-refused">{refused} refused</em>}
        <span className="chat-steps-caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ol className="chat-steps-list">
          {steps.map((s, i) => (
            <li key={i} className={s.denied ? "denied" : s.ok === false ? "failed" : ""}>
              <span className="chat-step-tool">{s.tool}</span>
              <code className="chat-step-text">{s.text}</code>
              {s.denied && <span className="chat-step-flag">refused</span>}
              {!s.denied && s.result && <pre className="chat-step-result">{s.result}</pre>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Bubble({ item }: { item: Exclude<ChatItem, { kind: "steps" }> }) {
  return (
    <div className={`chat-msg chat-${item.kind}`}>
      <div className="chat-bubble">
        {item.kind === "assistant"
          ? <div className="vault-md chat-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown></div>
          : <p>{item.text}</p>}
      </div>
      <span className="chat-time">{clockOf(item.ts)}</span>
    </div>
  );
}

function StatusCard({ status, online, onReset, nowMs }: {
  status: PocketStatus; online: boolean; onReset: () => void; nowMs: number;
}) {
  const five = status.limits?.five_hour;
  const seven = status.limits?.seven_day;
  const p = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const cost = status.cost_usd == null ? "—" : `$${status.cost_usd.toFixed(2)}`;
  return (
    <div className="wm-pop chat-card bezel" role="dialog" aria-label="Agent status">
      <div className="wm-pop-head">
        <span className={`vital-led ${online ? "led-green" : "led-standby"}`} />
        <strong>{(status.actor ?? "agent").toUpperCase()}</strong>
        <span className="vital-status">{online ? status.state ?? "idle" : "offline"}</span>
      </div>
      <dl className="chat-card-grid">
        <div><dt>model</dt><dd>{status.model ?? "—"}{status.effort ? ` · ${status.effort}` : ""}</dd></div>
        <div><dt>context</dt><dd>{fmtTokens(status.context_used)} / {fmtTokens(status.context_max)}</dd></div>
        <div><dt>5h</dt><dd>{p(five?.utilization)}<small className="chat-reset">resets in {resetIn(five?.resets_at, nowMs)}</small></dd></div>
        <div><dt>7d</dt><dd>{p(seven?.utilization)}<small className="chat-reset">resets in {resetIn(seven?.resets_at, nowMs)}</small></dd></div>
        <div><dt>cost</dt><dd title="list-price estimate for this conversation — the plan does not bill per token">{cost} est.</dd></div>
        <div><dt>time</dt><dd>{fmtDuration(status.duration_ms)} · {status.turns ?? 0} turn{status.turns === 1 ? "" : "s"}</dd></div>
        <div className="wide"><dt>cwd</dt><dd>{status.cwd ?? "—"}{status.branch ? ` · ${status.branch}` : ""}</dd></div>
        {status.mode === "replay"
          ? <div className="wide"><dt>mode</dt><dd>scripted demo — no agent (<code>hn pocket replay</code>)</dd></div>
          : <div className="wide"><dt>seal</dt>
              <dd className={status.seal?.ok ? "pos" : "neg"}>
                {status.seal?.ok ? "sealed (read-only)" : status.seal?.failures?.join("; ") || "not checked"}
              </dd></div>}
        <div className="wide"><dt>version</dt><dd>claude {status.claude_version ?? "—"} · harness {status.harness_version ?? "—"}</dd></div>
      </dl>
      <button type="button" className="chat-new" onClick={onReset}>NEW CONVERSATION</button>
    </div>
  );
}

export default function Chat() {
  const [entries, setEntries] = useState<PocketEntry[]>([]);
  const [status, setStatus] = useState<PocketStatus | null>(null);
  const [online, setOnline] = useState(false);
  const [live, setLive] = useState("");
  const [queued, setQueued] = useState<PocketQueued[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [cardOpen, setCardOpen] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());
  const seqRef = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const cardRef = useRef<HTMLDivElement>(null);

  const working = status?.state === "working" || queued.length > 0;
  const workingRef = useRef(false);
  workingRef.current = working;

  const poll = useCallback(async () => {
    try {
      const snap = await pocketState(seqRef.current);
      if (snap.entries.length) {
        // a reset starts a new transcript at seq 1 — drop what we hold rather than append to it
        const restarted = snap.entries[0].seq <= seqRef.current;
        seqRef.current = snap.entries[snap.entries.length - 1].seq;
        setEntries((prev) => (restarted ? snap.entries : [...prev, ...snap.entries]));
      } else if (!snap.status.turns && seqRef.current && !snap.queued.length && snap.status.state === "idle"
                 && !snap.status.session_id) {
        seqRef.current = 0;
        setEntries([]);
      }
      setStatus(snap.status);
      setOnline(snap.online);
      setLive(snap.live);
      setQueued(snap.queued);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const loop = async () => {
      if (!alive) return;
      if (!document.hidden) await poll();
      timer = window.setTimeout(loop, workingRef.current ? FAST_MS : IDLE_MS);
    };
    void loop();
    const onVis = () => { if (!document.hidden) void poll(); };
    document.addEventListener("visibilitychange", onVis);
    const tick = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => { alive = false; clearTimeout(timer); clearInterval(tick); document.removeEventListener("visibilitychange", onVis); };
  }, [poll]);

  // follow the conversation unless the reader scrolled up to read something older
  useEffect(() => {
    const el = logRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [entries, live, queued]);

  useEffect(() => {
    if (!cardOpen) return;
    const close = (e: MouseEvent) => {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) setCardOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [cardOpen]);

  const items = useMemo(() => groupEntries(entries), [entries]);
  const lastSteps = items.length && items[items.length - 1].kind === "steps" ? items[items.length - 1].seq : -1;
  const pill = derivePill(status ?? ({} as PocketStatus), online);

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      const q = await pocketSend(text);
      setDraft("");
      setQueued((prev) => [...prev, q]);
      stickRef.current = true;
      void poll();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="chat-zone">
      <header className="chat-head">
        <span className="chat-title">{(status?.actor ?? "chat").toUpperCase()}</span>
        <div className="wm-chip-wrap" ref={cardRef}>
          <button type="button" className={`wm-chip chat-pill tone-${pill.tone}`} aria-expanded={cardOpen}
                  onClick={() => setCardOpen((o) => !o)} title="Agent status">
            <span className={`vital-led ${pill.tone === "offline" ? "led-standby" : pill.tone === "alert" ? "led-red" : "led-green"}`} />
            <span className="wm-name">{pill.model}</span>
            <span className="chat-ring" style={{ "--p": `${pill.ctxPct ?? 0}` } as CSSProperties}
                  aria-label={`context ${pill.ctxPct ?? 0}%`} />
            <span className="wm-meta">5h {pill.fivePct == null ? "—" : `${pill.fivePct}%`}</span>
            {pill.tone !== "ok" && <span className="wm-word">{pill.word}</span>}
          </button>
          {cardOpen && status && (
            <StatusCard status={status} online={online} nowMs={nowMs}
                        onReset={() => { void pocketReset(); setCardOpen(false); }} />
          )}
        </div>
      </header>

      <div className="chat-log" ref={logRef}
           onScroll={(e) => {
             const el = e.currentTarget;
             stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
           }}>
        {items.length === 0 && !queued.length && (
          <p className="chat-empty">
            {online ? "Ask anything. Read-only: it reads your files and live data, and can file beads for later."
                    : "The runner is offline — start it on the serving machine (hn pocket run)."}
          </p>
        )}
        {items.map((it) => it.kind === "steps"
          ? <Steps key={`s${it.seq}`} steps={it.steps} working={working && it.seq === lastSteps && !live} />
          : <Bubble key={it.seq} item={it} />)}
        {queued.map((q) => (
          <div key={q.id} className="chat-msg chat-user pending">
            <div className="chat-bubble"><p>{q.text}</p></div>
            <span className="chat-time">sending…</span>
          </div>
        ))}
        {live && (
          <div className="chat-msg chat-assistant streaming">
            <div className="chat-bubble"><div className="vault-md chat-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{live}</ReactMarkdown></div></div>
          </div>
        )}
        {working && !live && lastSteps < 0 && status?.state === "working" && (
          <div className="chat-typing" aria-label="thinking"><span /><span /><span /></div>
        )}
      </div>

      {error && <p className="chat-error">{error}</p>}
      <form className="chat-composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <textarea value={draft} rows={1} placeholder={online ? "Message" : "Runner offline — messages wait in the queue"}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    // desktop: Enter sends, Shift+Enter breaks a line; a phone keyboard's return stays a newline
                    if (e.key === "Enter" && !e.shiftKey && !("ontouchstart" in window)) { e.preventDefault(); void send(); }
                  }} />
        {status?.state === "working"
          ? <button type="button" className="chat-stop" onClick={() => void pocketStop()} title="Stop the reply">■</button>
          : <button type="submit" className="chat-send" disabled={!draft.trim()} title="Send">↑</button>}
      </form>
    </div>
  );
}
