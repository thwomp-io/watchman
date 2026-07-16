"""Standing-agent health — derived deterministically from the pulse run-log + the known launchd
schedule. No model in the loop; the bus-app's Inbox watch-floor renders this.

The signal that matters is *cadence*: a scheduled run that never happened is the sleep-coalescing
failure mode that birthed the bus epic. We reconstruct it from two on-disk facts — the append-only
run log (the did-it-run audit) and the fixed schedule — so the answer needs no live process probe
and is fully unit-testable against a frozen ``now``.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel

from harness.bus.store import harness_state_dir

# The finance-pulse schedule — 9 runs per market day, Mon-Fri, local time.
# Default slots span a NYSE trading session as seen from one local clock — edit PULSE_SCHEDULE
# to yours, and keep it in sync with whatever scheduler fires the pulse (e.g. a launchd plist's
# StartCalendarInterval slots).
PULSE_SCHEDULE: list[time] = [
    time(9, 45), time(10, 30), time(11, 15),
    time(12, 0), time(12, 45), time(13, 30),
    time(14, 15), time(15, 0), time(15, 45),
]
PULSE_LOG = harness_state_dir() / "pulse.log"  # HARNESS_STATE_DIR-sealed (sandbox reads its own log)
_LAST_SLOT_GRACE = timedelta(minutes=30)  # window for the final daily slot to land a run

# pulse.log line: "YYYY-MM-DD HH:MM <quiet | N flag(s)[ (M new)]: ...>"
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) (.*)$")


class Tick(BaseModel):
    """One scheduled slot's outcome for today."""

    at: str  # "HH:MM"
    state: str  # "ran" | "missed" | "pending"


class AgentHealth(BaseModel):
    """One standing agent's health snapshot."""

    id: str
    label: str
    state: str  # "green" (on-schedule) | "red" (missed a due run) | "standby" (never-run / fresh instance)
    healthy: bool
    market_day: bool
    last_run: str | None  # "YYYY-MM-DD HH:MM" (local) or None
    last_run_rel: str | None  # humanized, e.g. "12m ago" / "Fri 12:48"
    next_run: str | None  # e.g. "9:33a Mon" (the next scheduled fire)
    runs_today: int
    expected_by_now: int
    missed: int
    cadence: list[Tick]
    last_flags: str | None  # most-recent non-quiet run's flag text


class WatchmenStatus(BaseModel):
    """The watch-floor payload (today: just the finance pulse; agents grow as watchmen ship)."""

    as_of: str
    overall: str  # worst agent state — "red" | "green" | "standby" (all agents idle/never-run)
    agents: list[AgentHealth]


def _parse_log(text: str) -> list[tuple[datetime, str]]:
    out: list[tuple[datetime, str]] = []
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        out.append((dt, m.group(3).strip()))
    return out


def _humanize(delta: timedelta, when: datetime) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return when.strftime("%a %H:%M")


def _fmt_slot(d: datetime) -> str:
    h12 = d.hour % 12 or 12
    ampm = "a" if d.hour < 12 else "p"
    return f"{h12}:{d.minute:02d}{ampm} {d.strftime('%a')}"


def _next_run(now: datetime, schedule: list[time]) -> str | None:
    if not schedule:
        return None
    # later slot remaining today (only if today is a market day)
    if now.weekday() < 5:
        for slot in schedule:
            cand = datetime.combine(now.date(), slot)
            if cand > now:
                return _fmt_slot(cand)
    # otherwise the first slot of the next market day
    day = now.date() + timedelta(days=1)
    for _ in range(7):
        if day.weekday() < 5:
            return _fmt_slot(datetime.combine(day, schedule[0]))
        day += timedelta(days=1)
    return None


def _pulse_health(now: datetime, log_path: Path, schedule: list[time]) -> AgentHealth:
    entries = _parse_log(log_path.read_text()) if log_path.exists() else []
    today = now.date()
    today_runs = [dt for dt, _ in entries if dt.date() == today]
    market_day = now.weekday() < 5

    # No run history at all → a fresh / never-configured instance (a clone without the launchd pulse
    # agent, or the demo sandbox with a sealed-empty state). That's STANDBY, not "missed all 9 slots":
    # nothing was ever scheduled to fire here, so the watch-floor reads calm, not alarmed. "missed"
    # (red) is reserved for an agent that HAS run before but skipped a due slot.
    if not entries:
        return AgentHealth(
            id="pulse",
            label="Finance pulse",
            state="standby",
            healthy=True,
            market_day=market_day,
            last_run=None,
            last_run_rel=None,
            next_run=_next_run(now, schedule),
            runs_today=0,
            expected_by_now=0,
            missed=0,
            cadence=[Tick(at=s.strftime("%H:%M"), state="pending") for s in schedule],
            last_flags=None,
        )

    cadence: list[Tick] = []
    expected_by_now = 0
    missed = 0
    for i, slot in enumerate(schedule):
        slot_dt = datetime.combine(today, slot)
        end = (
            datetime.combine(today, schedule[i + 1])
            if i + 1 < len(schedule)
            else slot_dt + _LAST_SLOT_GRACE
        )
        ran = any(slot_dt <= r < end for r in today_runs)
        if market_day and now >= slot_dt:
            expected_by_now += 1
        if ran:
            state = "ran"
        elif not market_day:
            state = "pending"  # nothing scheduled today
        elif now >= end:
            state = "missed"
            missed += 1
        else:
            state = "pending"  # window still open
        cadence.append(Tick(at=slot.strftime("%H:%M"), state=state))

    last_dt = max((dt for dt, _ in entries), default=None)
    last_flags = next(
        (rest for dt, rest in sorted(entries, reverse=True) if rest and rest != "quiet"),
        None,
    )
    healthy = missed == 0
    return AgentHealth(
        id="pulse",
        label="Finance pulse",
        state="green" if healthy else "red",
        healthy=healthy,
        market_day=market_day,
        last_run=last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else None,
        last_run_rel=_humanize(now - last_dt, last_dt) if last_dt else None,
        next_run=_next_run(now, schedule),
        # slots that FIRED, not raw log entries — a manual/ad-hoc `pulse --notify` (or two runs in
        # one slot window) must never push the ratio past its max (the "11/9" bug). Capped at the
        # schedule; the cadence ticks already encode which scheduled slots ran.
        runs_today=sum(1 for t in cadence if t.state == "ran"),
        expected_by_now=expected_by_now,
        missed=missed,
        cadence=cadence,
        last_flags=last_flags,
    )


def compute_watchmen_status(
    now: datetime,
    log_path: Path = PULSE_LOG,
    schedule: list[time] = PULSE_SCHEDULE,
) -> WatchmenStatus:
    """Reconstruct standing-agent health from the run-log + schedule, relative to ``now``."""
    pulse = _pulse_health(now, log_path, schedule)
    agents = [pulse]
    overall = (
        "red" if any(a.state == "red" for a in agents)
        else "green" if any(a.state == "green" for a in agents)
        else "standby"
    )
    return WatchmenStatus(as_of=now.strftime("%Y-%m-%d %H:%M"), overall=overall, agents=agents)
