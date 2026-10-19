// CampaignBoard — Career ▸ Campaign: where the search stands. Data = `hn career campaign --json`
// (CampaignReport): one card per conversation in flight (stage, next step + its date, last touch, the
// channel, the employer-screen line), soonest next step first; an inbound note with no pipeline row is
// a card with an amber "not in the pipeline" chip (drift); a stale card (no touch in the threshold, no
// dated next step) renders muted with "decide or archive". Below: the career calendar's milestones
// with a countdown, the Planning calendar's card shape. Clicking a card opens its note. Read-only —
// stage edits stay in applications.yaml. The recruiter mail is a table widget over the same payload.

import { useState } from "react";
import DocPopup from "./DocPopup";
import type { CampaignReport, InFlight, Milestone } from "./generated/contracts";

export type CampaignData = CampaignReport;

export function countdown(days: number): { big: string; unit: string } {
  if (days <= 0) return { big: "TODAY", unit: "" };
  if (days < 14) return { big: String(days), unit: days === 1 ? "day" : "days" };
  if (days < 70) return { big: String(Math.floor(days / 7)), unit: "weeks" };
  const m = Math.floor(days / 30);
  return { big: String(m), unit: m === 1 ? "month" : "months" };
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function daysUntil(iso: string, asOf: string): number {
  const a = new Date(`${asOf}T00:00:00`).getTime();
  const b = new Date(`${iso}T00:00:00`).getTime();
  return Math.round((b - a) / 86_400_000);
}

export default function CampaignBoard({ data }: { data: CampaignData }) {
  const [doc, setDoc] = useState<string | null>(null);
  const cards = data?.in_flight ?? [];
  const cal = data?.calendar ?? [];

  const card = (c: InFlight) => {
    const next = c.next_date
      ? `${shortDate(c.next_date)}${c.next_time ? ` · ${c.next_time}` : ""} · in ${daysUntil(c.next_date, data.as_of)}d`
      : null;
    return (
      <button key={`${c.company}:${c.role}`} type="button"
              className={`camp-card stage-${c.stage}${c.stale ? " stale" : ""}${c.drift ? " drift" : ""}`}
              onClick={() => c.ref && setDoc(c.ref)} aria-disabled={!c.ref || undefined}
              title={c.ref ? `${c.company} — open the note` : c.company}>
        <span className="camp-top">
          <em className="camp-stage">{c.stage}</em>
          <em className={`camp-screen screen-${c.screen_line.split(":")[0]}`}>{c.screen_line}</em>
          {c.drift && <em className="camp-drift">not in the pipeline</em>}
          {c.stale && <em className="camp-stale">decide or archive</em>}
        </span>
        <strong className="camp-company">{c.company}</strong>
        <span className="camp-role">{c.role || "—"}</span>
        {next ? <span className="camp-next dated">{next}</span> : null}
        <span className="camp-step">{c.next_step || "no next step written"}</span>
        <span className="camp-meta">
          {c.last_touch ? `last touch ${c.days_since}d ago` : "no touch recorded"} · via {c.channel}
          {c.location ? ` · ${c.location}` : ""}
        </span>
      </button>
    );
  };

  const milestone = (m: Milestone) => {
    const cd = countdown(m.days);
    return (
      <button key={`${m.date}:${m.label}`} type="button" className={`cal-card camp-milestone kind-${m.kind}`}
              onClick={() => m.ref && setDoc(m.ref)} aria-disabled={!m.ref || undefined} title={m.label}>
        <span className="cal-card-body">
          <span className="cal-card-top">
            <em className={`cal-kind camp-kind-${m.kind}`}>{m.kind || "date"}</em>
            {m.approx && <em className="cal-status">approx</em>}
          </span>
          <strong className="cal-name">{m.label}</strong>
          <span className="cal-window">{shortDate(m.date)}{m.time ? ` · ${m.time}` : ""}</span>
        </span>
        <span className="cal-countdown" aria-label={`${cd.big} ${cd.unit}`}>
          <b>{cd.big}</b>
          {cd.unit && <small>{cd.unit}</small>}
        </span>
      </button>
    );
  };

  return (
    <div className="campaign">
      <h4 className="projections-pane-title">In flight · {cards.length}</h4>
      {cards.length ? <div className="camp-cards">{cards.map(card)}</div>
        : <p className="projections-empty">Nothing in flight — applications.yaml has no open rows.</p>}
      <h4 className="projections-pane-title">The calendar</h4>
      {cal.length ? <div className="cal-cards camp-cal">{cal.map(milestone)}</div>
        : <p className="projections-empty">No milestones ahead — role-hunt/reference/career-calendar.yaml.</p>}
      {(data.calendar_notes ?? []).map((n) => <p key={n} className="camp-note">{n}</p>)}
      {data.mail_note && <p className="camp-note">mail: {data.mail_note}</p>}
      {doc && <DocPopup doc={doc} onClose={() => setDoc(null)} />}
    </div>
  );
}
