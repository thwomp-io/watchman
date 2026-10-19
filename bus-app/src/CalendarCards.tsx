// CalendarCards — the Planning tab's "on the calendar" band. Data = `hn travel upcoming --json`
// (PlanningCalendar): one card per live trip or visit, soonest first, with the picker's cover rule,
// its window, a countdown, who's going and an "open decision" chip when its folder carries a compare
// radar. Clicking a card opens the subtab that follows that kind's picker (`symbols_from: trip|visit`)
// with the card already selected there. Undated live items and parked ideas ride at the row's end as a
// compact "someday" group — listed, never counted down.

import { useNav } from "./nav";
import type { CalendarCard, PlanningCalendar } from "./generated/contracts";
import { Cover } from "./TripPicker";

export type CalendarData = PlanningCalendar;

// The countdown's big number + unit: "6 days", "9 weeks", "3 months"; TODAY / NOW while it runs.
export function countdown(c: CalendarCard): { big: string; unit: string } {
  const d = c.days;
  if (d == null) return { big: "—", unit: c.when };
  if (d < 0) return { big: "NOW", unit: "under way" };
  if (d === 0) return { big: "TODAY", unit: "" };
  if (d < 14) return { big: String(d), unit: d === 1 ? "day" : "days" };
  if (d < 70) return { big: String(Math.floor(d / 7)), unit: "weeks" };
  const m = Math.floor(d / 30);
  return { big: String(m), unit: m === 1 ? "month" : "months" };
}

export default function CalendarCards({ data }: { data: CalendarData }) {
  const nav = useNav();
  const upcoming = data?.upcoming ?? [];
  const someday = data?.someday ?? [];
  const open = (c: CalendarCard) => nav.navigate({ zone: "dash", follows: c.kind, pick: c.slug });

  if (!upcoming.length && !someday.length) {
    return <p className="projections-empty">Nothing on the calendar — no live trip or visit in the corpus.</p>;
  }
  return (
    <div className="cal-cards">
      {upcoming.map((c) => {
        const cd = countdown(c);
        return (
          <button key={c.slug} type="button" className={`cal-card kind-${c.kind}`}
                  onClick={() => open(c)} title={`${c.title} — open on the ${c.kind === "trip" ? "Trips" : "Visits"} tab`}>
            <Cover path={c.cover} />
            <span className="cal-card-body">
              <span className="cal-card-top">
                <em className={`cal-kind cal-kind-${c.kind}`}>{c.kind}</em>
                <em className="cal-status">{c.status}</em>
                {c.decision && <em className="cal-decision">open decision</em>}
              </span>
              <strong className="cal-name">{c.name}</strong>
              {c.anchor && c.anchor !== c.name && <span className="cal-anchor">{c.anchor}</span>}
              <span className="cal-window">{c.window}</span>
              <span className="cal-who">{c.who.length ? c.who.join(", ") : "—"}</span>
            </span>
            <span className="cal-countdown" aria-label={`${cd.big} ${cd.unit}`}>
              <b>{cd.big}</b>
              {cd.unit && <small>{cd.unit}</small>}
            </span>
          </button>
        );
      })}
      {someday.length > 0 && (
        <div className="cal-someday" aria-label="Someday">
          <span className="cal-someday-title">someday</span>
          {someday.map((c) => (
            <button key={c.slug} type="button" className={`cal-someday-chip kind-${c.kind}`}
                    onClick={() => open(c)} title={`${c.title} · ${c.status}`}>
              <strong>{c.name}</strong>
              <span>{c.window}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
