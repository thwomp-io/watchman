"""Travel Watchman — the conditions watch.

The travel-lane analog of the finance pulse: deterministic environmental flags from the keyless
weather/air senses against config thresholds (weights.yaml ``conditions:``). The configured home locale
is the standing scope; finalized trips arm their destination separately. **Zero model in the loop** —
detection lives here; the agent narrates on demand. The 6am morning report always fires;
threshold flags ride on top.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from harness.bus.models import EventDraft, Severity
from harness.travel.models import AirQualityReport, Trip, WeatherForecast
from harness.travel.ranking.weights import ConditionsThresholds

# kind -> bus severity (snow/smoke are the loud ones; heat warns; a wet day is informational).
_SEVERITY: dict[str, Severity] = {"snow": "alert", "smoke": "alert", "heat": "warn", "wet_day": "info"}


class ConditionFlag(BaseModel):
    """One deterministic conditions flag. The model narrates; this detects."""

    kind: str  # heat | smoke | wet_day | snow
    scope: str  # home | trip
    place: str
    date: str  # the forecast day it fires for (YYYY-MM-DD)
    message: str


class ConditionsReport(BaseModel):
    """The conditions-watch contract (mirrors finance PulseReport): quiet=True -> end silently;
    flags -> notify. Carries the home forecast/air the 6am morning report renders."""

    as_of: str
    home: str
    quiet: bool = True
    flags: list[ConditionFlag] = Field(default_factory=list)
    weather: WeatherForecast | None = None
    air: AirQualityReport | None = None
    armed_trips: list[str] = Field(default_factory=list)  # human labels of finalized trips under watch


def should_arm(trip: Trip, today: date, arm_statuses: list[str], arm_days: int) -> bool:
    """PURE: does this trip graduate to a watched destination right now? It must be a finalized/booked/
    active status, have a real destination + start date, and sit within [today, today+arm_days] — so a
    speculative candidate is never watched (noise control) and a far-out trip arms only as it nears."""
    if trip.status not in arm_statuses or not trip.destination or trip.start is None:
        return False
    days_out = (trip.start - today).days
    return 0 <= days_out <= arm_days


def compute_flags(
    *,
    scope: str,
    place: str,
    weather: WeatherForecast,
    air: AirQualityReport | None,
    th: ConditionsThresholds,
) -> list[ConditionFlag]:
    """PURE: forecast + air + thresholds -> flags. No I/O (fixture-testable). One flag per
    (kind, forecast-day) crossing. Wet-day fires on DURATION or accumulation, never probability."""
    flags: list[ConditionFlag] = []
    tunit = weather.temperature_unit[-1]  # "F" / "C"
    punit = weather.precipitation_unit[0]  # "i" / "m"
    for d in weather.days:
        if d.temp_max is not None and d.temp_max >= th.heat_high_f:
            flags.append(ConditionFlag(
                kind="heat", scope=scope, place=place, date=d.date,
                message=f"{place}: {d.temp_max:.0f}°{tunit} high {d.date} "
                        f"— above your {th.heat_high_f:.0f}° line (a personal comfort threshold)",
            ))
        wet_hours = d.precip_hours is not None and d.precip_hours >= th.wet_day_hours
        wet_sum = d.precip_sum is not None and d.precip_sum >= th.wet_day_sum_in
        if wet_hours or wet_sum:
            parts = []
            if d.precip_hours:
                parts.append(f"{d.precip_hours:.0f}h")
            if d.precip_sum:
                parts.append(f"{d.precip_sum:.2f}{punit}")
            flags.append(ConditionFlag(
                kind="wet_day", scope=scope, place=place, date=d.date,
                message=f"{place}: wet day {d.date} ({' · '.join(parts)}) — mostly rain",
            ))
        if d.snowfall_sum:  # any snowfall always flags
            flags.append(ConditionFlag(
                kind="snow", scope=scope, place=place, date=d.date,
                message=f"{place}: snow {d.date} — {d.snowfall_sum:.1f}{punit}",
            ))
    if air is not None:
        for a in air.days:
            if a.us_aqi_max is not None and a.us_aqi_max >= th.aqi:
                flags.append(ConditionFlag(
                    kind="smoke", scope=scope, place=place, date=a.date,
                    message=f"{place}: AQI {a.us_aqi_max} ({a.category}) {a.date} — wildfire smoke",
                ))
    return flags


def events_from_conditions(rep: ConditionsReport) -> list[EventDraft]:
    """One EventDraft per flag (the per-flag alert mode, `--notify`). The idempotency key is the
    (kind, place, FORECAST-date) — so a multi-day-out heat day flagged on three consecutive runs
    notifies ONCE (keyed on the event's date, not the run day), never nagging. Pure (no I/O)."""
    drafts: list[EventDraft] = []
    for f in rep.flags:
        drafts.append(EventDraft(
            producer="travel.conditions",
            lane="travel",
            kind=f.kind,
            subject=f.place,
            title=f"{f.place} — {f.kind.replace('_', ' ')}",
            body=f.message,
            severity=_SEVERITY.get(f.kind, "info"),
            payload={"flag": f.model_dump(), "as_of": rep.as_of},
            idempotency_key=f"travel.conditions:{f.kind}:{f.place}:{f.date}",
        ))
    return drafts


# ── the 6am morning report: ONE escalating daily notification + a doc-series ──────────────

def _fmt(v: float | None, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.0f}{suffix}"


def report_summary(rep: ConditionsReport) -> str:
    """One-line digest for the morning bus event + the run-log."""
    head = rep.home
    if rep.weather and rep.weather.days:
        d0 = rep.weather.days[0]
        head = f"{rep.home}: {d0.condition}, {_fmt(d0.temp_max)}{rep.weather.temperature_unit}"
    if rep.flags:
        kinds = ", ".join(sorted({f.kind for f in rep.flags}))
        return f"{head} — {len(rep.flags)} alert(s): {kinds}"
    return f"{head} — quiet"


def render_morning_report(rep: ConditionsReport) -> str:
    """The deterministic morning digest markdown (model-free) — today's home conditions + the near
    horizon + any alerts. The dashboard doc-series browses these newest-first; each is
    self-contained + dated. The agent's interpretive take is a SEPARATE on-demand act (the doctrine)."""
    wx = rep.weather
    air_by_date = {a.date: a for a in (rep.air.days if rep.air else [])}
    out: list[str] = [
        "---", f"date: {rep.as_of}", f"home: {rep.home}", f"flags: {len(rep.flags)}",
        "tags: [travel, conditions, morning-report]", "---", "",
        f"# Conditions — {rep.home} · {rep.as_of}", "",
    ]
    if wx and wx.days:
        d0 = wx.days[0]
        tunit = wx.temperature_unit
        out.append(f"**Today:** {d0.condition}, high {_fmt(d0.temp_max)}{tunit} / "
                   f"low {_fmt(d0.temp_min)}{tunit}.")
        out.append("")
    if rep.armed_trips:
        out.append(f"**Watching (finalized trips):** {', '.join(rep.armed_trips)}")
        out.append("")
    if rep.flags:
        out.append(f"## ⚠️ {len(rep.flags)} alert(s)")
        out += [f"- **{f.kind}** ({f.date}) — {f.message}" for f in rep.flags]
        out.append("")
    else:
        out.append("*Quiet — nothing crossed the alert thresholds (heat / smoke / wet-day / snow).*")
        out.append("")
    if wx and wx.days:
        punit = wx.precipitation_unit[0]
        out.append("| Date | Conditions | High | Low | Rain | Snow | AQI |")
        out.append("|---|---|---|---|---|---|---|")
        for d in wx.days:
            rain = f"{d.precip_hours:.0f}h" if d.precip_hours else "—"
            snow = f"{d.snowfall_sum:.1f}{punit}" if d.snowfall_sum else "—"
            a = air_by_date.get(d.date)
            aqi = f"{a.us_aqi_max} ({a.category})" if a and a.us_aqi_max is not None else "—"
            out.append(f"| {d.date} | {d.condition} | {_fmt(d.temp_max)} | {_fmt(d.temp_min)} | "
                       f"{rain} | {snow} | {aqi} |")
        out.append("")
    return "\n".join(out)


def write_morning_report(rep: ConditionsReport, vault_root: Path) -> Path:
    """Write the digest to travel/conditions/reports/{date}.md (the doc-series, accreting newest-first).
    Overwrites the same day (an idempotent re-run just refreshes today). Returns the path."""
    out_dir = vault_root / "travel" / "conditions" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{rep.as_of}.md"
    path.write_text(render_morning_report(rep))
    return path


def morning_event(rep: ConditionsReport, report_rel: str | None = None) -> EventDraft:
    """The always-fires 6am morning-report bus event — the welcome daily ritual as ONE notification
    that ESCALATES when a flag crosses (by design: a single morning push, louder on a threshold,
    not separate pings). Once per day (keyed on the date). `report_rel` is the vault-relative report
    path → a payload.ref deep-link the bus-app Inbox opens with 'GO TO →'."""
    sev: Severity = "info"
    if any(f.kind in ("snow", "smoke") for f in rep.flags):
        sev = "alert"
    elif rep.flags:
        sev = "warn"
    payload: dict[str, object] = {"as_of": rep.as_of, "flags": len(rep.flags)}
    if report_rel:
        payload["ref"] = {"zone": "vault", "doc": report_rel}
    return EventDraft(
        producer="travel.conditions",
        lane="travel",
        kind="morning_report",
        subject=rep.home,
        title=f"Conditions — {rep.home}",
        body=report_summary(rep),
        severity=sev,
        payload=payload,
        idempotency_key=f"travel.conditions:morning_report:{rep.home}:{rep.as_of}",
    )
