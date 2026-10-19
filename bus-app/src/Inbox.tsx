// The Inbox zone — the bus, triaged. One slim status bar on top (the overall state, a chip per
// watchman whose popover carries its full vitals, the agenda), then the list and the reader side by
// side at full height (stacked list → reader with a back button on a phone).
//
// The list groups by triage band (ACT · WATCH · CATALYST WIRE · FILINGS). The wire band reads like a
// news list: one row per story (the same headline under several tickers is one row with every ticker
// chip), the outlet and the age of the PUBLISH time, newest first; quote/option-chain pages the engine
// flagged stay behind the band's "n filtered" footer. A catalyst opens like an article — outlet,
// publish time, Open original (the publisher's page, not the Google News redirect), the lead image,
// the summary — and is marked read on open, like the News tab. Every other event keeps the
// inspector: title, meta, body, payload, Go to, Mark read.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ackEvents, articleCard, distinctMeta, listEvents, onBusSelect, onBusUpdated, runSurface, watchmenStatus,
  type ArticleCard,
} from "./api";
import JsonView from "./JsonView";
import { payloadRef, resolveRef, useNav } from "./nav";
import type { AgentHealth, BusEvent, DistinctMeta, WatchmenStatus } from "./types";
import {
  ageLabel, articleOf, BANDS, bandFor, isUrgent, payloadOf, publishedMs, storyRows,
  type Band, type StoryRow,
} from "./inboxModel";

type Health = "green" | "amber" | "red";
const SEV: Record<string, string> = { alert: "sev-alert", warn: "sev-warn", info: "sev-info" };

interface Agenda {
  prints: { symbol: string; days_out: number }[];
  wash: { today_poisoned: boolean; next_clean_start: string | null } | null;
}

// The ambient resting state: worst of the watchmen's own health and the unread urgent signals.
function harnessHealth(wm: WatchmenStatus | null, unread: BusEvent[]): Health {
  if (wm?.overall === "red") return "red";
  if (unread.some((e) => e.severity === "alert")) return "red";
  if (unread.some((e) => e.severity === "warn")) return "amber";
  return "green";
}

function Cadence({ agent }: { agent: AgentHealth }) {
  return (
    <span className="cadence" title={`${agent.runs_today}/${agent.expected_by_now} runs today`}>
      {agent.cadence.map((t, i) => (
        <i key={i} className={`tick tick-${t.state}`} title={`${t.at} · ${t.state}`} />
      ))}
    </span>
  );
}

export function agentStatusWord(a: AgentHealth): string {
  if (a.state === "standby") return "STANDING BY";
  if (a.missed > 0) return `${a.missed} MISSED`;
  if (!a.market_day) return "MARKET CLOSED";
  return "ON SCHEDULE";
}

const todayLabel = (a: AgentHealth) =>
  a.state === "standby" || !a.market_day ? "—" : `${a.runs_today}/${a.expected_by_now}`;

// One watchman as a status-bar chip; click → its vitals in a popover. A red watchman's chip turns
// red and says why, so a degraded agent is visible without opening anything.
function WatchmanChip({ agent }: { agent: AgentHealth }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  const word = agentStatusWord(agent);
  return (
    <div className="wm-chip-wrap" ref={ref}>
      <button type="button" className={`wm-chip wm-${agent.state}`} aria-expanded={open}
              onClick={() => setOpen((o) => !o)} title={`${agent.label} — ${word}`}>
        <span className={`vital-led led-${agent.state}`} />
        <span className="wm-name">{agent.label}</span>
        {agent.state === "red" && <span className="wm-word">{word}</span>}
        <span className="wm-meta">{agent.last_run_rel ?? "—"} · next {agent.next_run ?? "—"}</span>
        <Cadence agent={agent} />
      </button>
      {open && (
        <div className="wm-pop bezel" role="dialog" aria-label={`${agent.label} vitals`}>
          <div className="wm-pop-head">
            <span className={`vital-led led-${agent.state}`} />
            <strong>{agent.label}</strong>
            <span className={`vital-status status-${agent.state}`}>{word}</span>
          </div>
          <dl className="wm-pop-grid">
            <div><dt>last</dt><dd>{agent.last_run_rel ?? "—"}</dd></div>
            <div><dt>next</dt><dd>{agent.next_run ?? "—"}</dd></div>
            <div><dt>today</dt><dd>{todayLabel(agent)}</dd></div>
            <div><dt>missed</dt><dd>{agent.missed}</dd></div>
          </dl>
          <div className="wm-pop-cadence"><span>cadence</span><Cadence agent={agent} /></div>
          {agent.last_flags && <p className="wm-pop-flags">last flags: {agent.last_flags}</p>}
        </div>
      )}
    </div>
  );
}

function StatusBar({ wm, agenda, health, urgent }: {
  wm: WatchmenStatus | null; agenda: Agenda | null; health: Health;
  urgent: { alert: number; warn: number };
}) {
  const pulse = wm?.agents.find((a) => a.id === "pulse");
  const nextPrint = agenda?.prints?.[0];
  const headlineText = urgent.alert > 0 ? `${urgent.alert} NEED${urgent.alert === 1 ? "S" : ""} YOU`
    : urgent.warn > 0 ? `${urgent.warn} WATCH` : "ALL QUIET";
  const overall = !wm ? "acquiring…" : wm.overall === "red" ? "attention"
    : wm.overall === "standby" ? "standing by" : "all systems nominal";
  return (
    <div className={`statusbar glow-${health}`}>
      <span className={`status-orb orb-${health}`} />
      <span className="status-headline">{headlineText}</span>
      <span className={`status-overall over-${wm?.overall ?? "standby"}`}>{overall}</span>
      <div className="wm-chips">
        {wm?.agents.map((a) => <WatchmanChip key={a.id} agent={a} />)}
      </div>
      <dl className="status-agenda">
        {nextPrint && <div><dt>next print</dt><dd>{nextPrint.symbol} · {nextPrint.days_out}d</dd></div>}
        {agenda?.wash && (
          <div><dt>vest wash</dt><dd>{agenda.wash.today_poisoned
            ? `poisoned → ${agenda.wash.next_clean_start ?? "—"}` : "clear"}</dd></div>
        )}
        {pulse?.last_flags && <div className="status-lastflag"><dt>last flag</dt><dd>{pulse.last_flags.split(";")[0]}</dd></div>}
      </dl>
    </div>
  );
}

// ————— the reader ————————————————————————————————————————————————————————————————————————————

function fullTime(ms: number): string {
  if (Number.isNaN(ms)) return "";
  return new Date(ms).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function ArticleReader({ row, onGoto, onAck }: {
  row: StoryRow; onGoto?: () => void; onAck?: () => void;
}) {
  const e = row.events[0];
  const a = articleOf(e);
  const [card, setCard] = useState<ArticleCard | null>(null);
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    setCard(null);
    setRaw(false);
    if (a.image_url || row.filtered) return; // the feed's own image wins; junk isn't fetched
    let live = true;
    articleCard(e.id).then((c) => { if (live) setCard(c); }).catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [e.id]);
  const link = a.resolved_url || card?.final_url || a.url;
  const viaGoogle = !a.resolved_url && !card?.final_url && /news\.google\.com/.test(a.url || "");
  const image = a.image_url || card?.image || "";
  const summary = a.summary || card?.dek || "";
  const ms = publishedMs(a.published);
  return (
    <article className="reader">
      <div className="reader-meta">
        <span className="reader-outlet">{row.outlet || card?.site || "unknown outlet"}</span>
        {a.author && <span className="reader-author">{a.author}</span>}
        {!Number.isNaN(ms) && <span className="reader-when" title={fullTime(ms)}>{fullTime(ms)} · {ageLabel(ms)} ago</span>}
      </div>
      <h2 className="reader-title">{row.title}</h2>
      <div className="reader-chips">
        {row.symbols.map((s) => <span key={s} className="ticker-chip">{s}</span>)}
        {row.filtered && <span className="filtered-chip">filtered · {row.filtered}</span>}
        {(a.tags ?? []).slice(0, 4).map((t) => <span key={t} className="tag-chip">{t}</span>)}
      </div>
      <div className="reader-actions">
        {link && (
          <a className="open-original" href={link} target="_blank" rel="noreferrer">
            Open original ↗{viaGoogle && <em> via Google News</em>}
          </a>
        )}
        {onGoto && <button className="goto" onClick={onGoto}>Go to research →</button>}
        {onAck && <button onClick={onAck}>Mark read</button>}
        <button className="reader-raw" onClick={() => setRaw((r) => !r)}>{raw ? "Hide raw" : "Raw"}</button>
      </div>
      {image && <img className="reader-image" src={image} alt="" loading="lazy"
                     onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }} />}
      {summary
        ? <p className="reader-summary">{summary}</p>
        : <p className="reader-empty">Headline only — open the original for the story.</p>}
      {row.events.length > 1 && (
        <p className="reader-also">Filed under {row.symbols.join(" · ")} ({row.events.length} wire entries)</p>
      )}
      {raw && <JsonView data={payloadOf(e)} />}
    </article>
  );
}

function EventInspector({ e, onGoto, onAck }: { e: BusEvent; onGoto?: () => void; onAck?: () => void }) {
  return (
    <>
      <div className={`detail-rule ${SEV[e.severity] ?? "sev-info"}`} />
      <h2>{e.title}</h2>
      <p className="detail-meta">
        {e.producer} · {e.kind}{e.subject ? ` · ${e.subject}` : ""} · {e.created_at}
        {e.delivered_via.length > 0 && ` · VIA ${e.delivered_via.join(",").toUpperCase()}`}
      </p>
      {e.body && <p className="detail-body">{e.body}</p>}
      <JsonView data={payloadOf(e)} />
      <div className="detail-actions">
        {onGoto && <button className="goto" onClick={onGoto}>GO TO →</button>}
        {onAck && <button className="primary" onClick={onAck}>MARK READ</button>}
      </div>
    </>
  );
}

// ————— the zone ————————————————————————————————————————————————————————————————————————————————

interface Row { key: string; band: Band; story?: StoryRow; event: BusEvent; ids: number[]; ms?: number }

function useCompact(): boolean {
  const q = "(max-width: 720px)";
  const [c, setC] = useState(() => typeof window !== "undefined" && window.matchMedia?.(q)?.matches === true);
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setC(e.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return c;
}

export default function Inbox({ onUnread }: { onUnread: (n: number) => void }) {
  const nav = useNav();
  const compact = useCompact();
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [meta, setMeta] = useState<DistinctMeta>({ lanes: [], kinds: [] });
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [lane, setLane] = useState("");
  const [kind, setKind] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showFiltered, setShowFiltered] = useState(false);
  const [wm, setWm] = useState<WatchmenStatus | null>(null);
  const [agenda, setAgenda] = useState<Agenda | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const reload = useCallback(async () => {
    const evs = await listEvents({ unreadOnly, lane, kind });
    setEvents(evs);
    setMeta(await distinctMeta());
    onUnread(evs.filter((e) => !e.read_at && isUrgent(e)).length);
  }, [unreadOnly, lane, kind, onUnread]);

  const loadWm = useCallback(() => void watchmenStatus().then(setWm).catch(() => setWm(null)), []);

  useEffect(() => {
    void reload();
    loadWm();
    const unsubs = [
      onBusUpdated(() => { void reload(); loadWm(); }),
      onBusSelect((id) => setSelectedKey(`id:${id}`)),
    ];
    return () => { unsubs.forEach((p) => void p.then((u) => u())); };
  }, [reload, loadWm]);

  // the agenda (prints + wash) is a live call — once, lazily
  useEffect(() => {
    void runSurface("finance.watch")
      .then((raw) => {
        const d = JSON.parse(raw) as { prints?: Agenda["prints"]; wash_sale?: Agenda["wash"] };
        setAgenda({ prints: d.prints ?? [], wash: d.wash_sale ?? null });
      })
      .catch(() => setAgenda(null));
  }, []);

  const unread = events.filter((e) => !e.read_at);
  const health = harnessHealth(wm, unread.filter(isUrgent));
  const urgentCounts = {
    alert: unread.filter((e) => e.severity === "alert").length,
    warn: unread.filter((e) => e.severity === "warn").length,
  };

  // the visible rows, in display order — what j/k walks
  const { bands, rows, filteredN } = useMemo(() => {
    const g: Record<Band, BusEvent[]> = { act: [], watch: [], wire: [], filings: [] };
    for (const e of events) g[bandFor(e)].push(e);
    // only catalysts read as articles; any other info event in the band (a macro print) stays an
    // inspector row, interleaved by time
    const stories = storyRows(g.wire.filter((e) => e.kind === "catalyst"));
    const filteredRows = stories.filter((s) => s.filtered);
    const others = g.wire.filter((e) => e.kind !== "catalyst");
    const out: Row[] = [];
    const per: Record<Band, Row[]> = { act: [], watch: [], wire: [], filings: [] };
    for (const b of BANDS) {
      const list: Row[] = b.key === "wire"
        ? [
          ...stories.filter((s) => !s.filtered || showFiltered).map((s) => ({
            key: s.key, band: "wire" as Band, story: s, event: s.events[0], ids: s.events.map((x) => x.id),
            ms: s.publishedMs,
          })),
          ...others.map((e) => ({ key: `id:${e.id}`, band: "wire" as Band, event: e, ids: [e.id], ms: Date.parse(e.created_at) })),
        ].sort((x, y) => (y.ms || 0) - (x.ms || 0))
        : g[b.key].map((e) => ({ key: `id:${e.id}`, band: b.key, event: e, ids: [e.id] }));
      per[b.key] = list;
      out.push(...list);
    }
    return { bands: per, rows: out, filteredN: filteredRows.length };
  }, [events, showFiltered]);

  // a bus-select by id lands on the row that holds that event
  const selected = useMemo(() => {
    if (!selectedKey) return null;
    if (selectedKey.startsWith("id:")) {
      const id = Number(selectedKey.slice(3));
      return rows.find((r) => r.key === selectedKey || r.ids.includes(id)) ?? null;
    }
    return rows.find((r) => r.key === selectedKey) ?? null;
  }, [rows, selectedKey]);

  const ack = useCallback(async (ids: number[]) => {
    await ackEvents(ids);
    await reload();
  }, [reload]);

  const open = useCallback((r: Row) => {
    setSelectedKey(r.key);
    // a catalyst is marked read on open (the News-tab rule); act/watch stay explicit to-dos
    if (r.band === "wire") {
      const fresh = r.story?.events.filter((e) => !e.read_at).map((e) => e.id) ?? [];
      if (fresh.length) void ack(fresh);
    }
  }, [ack]);

  useEffect(() => { // j/k + arrows, like the News tab; never while typing or with a modifier
    const onKey = (e: KeyboardEvent) => {
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey) return;
      const down = e.key === "j" || e.key === "ArrowDown";
      const up = e.key === "k" || e.key === "ArrowUp";
      if (!down && !up) return;
      const i = selected ? rows.indexOf(selected) : -1;
      const next = rows[Math.max(0, Math.min(rows.length - 1, i + (down ? 1 : -1)))];
      if (next) { open(next); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selected, open]);

  useEffect(() => {
    listRef.current?.querySelector("li.selected")?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);

  const ref = selected ? payloadRef(payloadOf(selected.event)) : null;
  const goto = ref ? () => void resolveRef(ref).then(nav.navigate) : undefined;
  const showList = !compact || !selected;
  const showReader = !compact || !!selected;

  return (
    <div className="inbox">
      <div className="strip">
        <label className="toggle">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
          <span>UNREAD ONLY</span>
        </label>
        <select value={lane} onChange={(e) => setLane(e.target.value)}>
          <option value="">LANE · ALL</option>
          {meta.lanes.map((l) => <option key={l} value={l}>{l.toUpperCase()}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">KIND · ALL</option>
          {meta.kinds.map((k) => <option key={k} value={k}>{k.toUpperCase()}</option>)}
        </select>
        <div className="spacer" />
        <button disabled={unread.length === 0} onClick={() => void ack(unread.map((e) => e.id))}>
          MARK ALL READ
        </button>
      </div>

      <StatusBar wm={wm} agenda={agenda} health={health} urgent={urgentCounts} />

      <div className={`split${compact ? " split-compact" : ""}`}>
        {showList && (
          <div className="feed bezel" ref={listRef}>
            {events.length === 0 && <div className="empty">ALL QUIET — THE WATCHMEN ARE STANDING BY</div>}
            {BANDS.map(({ key, label, accent }) => {
              const list = bands[key];
              if (list.length === 0 && !(key === "wire" && filteredN > 0)) return null;
              const unreadN = list.filter((r) => (r.story ? r.story.unread : !r.event.read_at)).length;
              return (
                <section key={key} className={`band band-${key}`}>
                  <header className={`band-head ${accent}`}>
                    <span className="band-led" />
                    <span className="band-label">{label}</span>
                    <span className="band-count">{unreadN > 0 ? `${unreadN}/${list.length}` : list.length}</span>
                  </header>
                  <ul className="band-rows">
                    {list.map((r) => {
                      const s = r.story;
                      const unreadRow = s ? s.unread : !r.event.read_at;
                      return (
                        <li key={r.key}
                            className={[
                              selected?.key === r.key ? "selected" : "",
                              unreadRow ? "unread" : "read",
                              SEV[r.event.severity] ?? "sev-info",
                              s ? "wire-row" : "",
                              s?.filtered ? "filtered" : "",
                            ].join(" ")}
                            onClick={() => open(r)}>
                          <span className="led" />
                          {s ? (
                            <>
                              <span className="row-tickers">{s.symbols.map((x) => <b key={x}>{x}</b>)}</span>
                              <span className="row-title">{s.title}</span>
                              {s.outlet && <span className="row-outlet">{s.outlet}</span>}
                              <span className="row-when">{ageLabel(s.publishedMs)}</span>
                            </>
                          ) : (
                            <>
                              {r.event.lane !== "finance" && <span className="lane-tag">{r.event.lane}</span>}
                              <span className="row-title">{r.event.title}</span>
                              <span className="row-when">{ageLabel(Date.parse(r.event.created_at))}</span>
                            </>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                  {key === "wire" && filteredN > 0 && (
                    <button className="band-foot" onClick={() => setShowFiltered((v) => !v)}>
                      {filteredN} filtered (quote and option pages) · {showFiltered ? "hide" : "show"}
                    </button>
                  )}
                </section>
              );
            })}
          </div>
        )}

        {showReader && (
          <aside className="detail bezel">
            {compact && selected && (
              <button className="reader-back" onClick={() => setSelectedKey(null)}>← back to the list</button>
            )}
            {selected ? (
              selected.story ? (
                <ArticleReader row={selected.story} onGoto={goto}
                               onAck={selected.story.unread ? () => void ack(selected.ids) : undefined} />
              ) : (
                <EventInspector e={selected.event} onGoto={goto}
                                onAck={!selected.event.read_at ? () => void ack(selected.ids) : undefined} />
              )
            ) : (
              <div className="detail-empty">
                <span className={`digest-orb orb-${health}`} />
                <p>select a signal — j/k to move</p>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}

