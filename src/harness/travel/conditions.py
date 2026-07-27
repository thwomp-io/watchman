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
from harness.travel.models import (
    AirQualityReport,
    CurrentConditions,
    HourlyWeather,
    OutdoorPlan,
    OutdoorWindow,
    Trip,
    WeatherForecast,
)
from harness.travel.ranking.weights import ConditionsThresholds

# kind -> bus severity (snow/smoke are the loud ones; heat + gusts warn; wet day + UV are
# informational — UV is a bring-sunscreen note, not a change-your-plans event).
_SEVERITY: dict[str, Severity] = {
    "snow": "alert", "smoke": "alert", "heat": "warn", "wind": "warn", "wet_day": "info", "uv": "info",
}


class ConditionFlag(BaseModel):
    """One deterministic conditions flag. The model narrates; this detects."""

    kind: str  # heat | smoke | wet_day | snow | uv | wind
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
    outdoor: OutdoorPlan | None = None  # today's outdoor-windows read (v1.5 tier B; None = hourly down)
    now: CurrentConditions | None = None  # the right-now read (v1.5 tier C; None = current down)
    tiles: dict[str, float | str | None] = Field(default_factory=dict)  # the FLAT dashboard-tile
    #                    contract (tier C) — stable keys the Conditions-subtab stat row plucks
    #                    (now_temp/feels/uv/gusts/aqi/sunset/window). Derived by conditions_tiles().


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
        # Heat is FEELS-AWARE (v1.5): apparent temp when the provider carries it, air temp as the
        # fallback — a 84° humid day that feels 91° should flag; a dry 84° that feels 80° shouldn't.
        heat_val = d.feels_max if d.feels_max is not None else d.temp_max
        if heat_val is not None and heat_val >= th.heat_high_f:
            felt = (
                f"feels {d.feels_max:.0f}°{tunit} (air {d.temp_max:.0f}°)"
                if d.feels_max is not None and d.temp_max is not None
                else f"{heat_val:.0f}°{tunit} high"
            )
            flags.append(ConditionFlag(
                kind="heat", scope=scope, place=place, date=d.date,
                message=f"{place}: {felt} {d.date} "
                        f"— above the configured {th.heat_high_f:.0f}° heat threshold",
            ))
        if d.uv_index_max is not None and d.uv_index_max >= th.uv:
            flags.append(ConditionFlag(
                kind="uv", scope=scope, place=place, date=d.date,
                message=f"{place}: UV index {d.uv_index_max:.0f} {d.date} "
                        f"— at/above the configured {th.uv:.0f} (very-high) bar",
            ))
        if d.wind_gusts_max is not None and d.wind_gusts_max >= th.gusts_mph:
            flags.append(ConditionFlag(
                kind="wind", scope=scope, place=place, date=d.date,
                message=f"{place}: gusts to {d.wind_gusts_max:.0f} {weather.wind_unit} {d.date} "
                        f"— above the configured {th.gusts_mph:.0f} bar",
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


# ── the outdoor-windows solver (v1.5 tier B) ────────────────────────────────


class OutdoorPrefs(BaseModel):
    """Personal outdoor-comfort thresholds — the solver's bar. Lives in the USER OVERLAY
    (`travel.global_settings.outdoor` in harness.yaml; generalize-first — comfort is config,
    never engine constants). Defaults are a temperate-climate starting point."""

    feels_min_f: float = 45.0  # below this an hour is "too cold"
    feels_max_f: float = 85.0  # above this "too hot" (feels, not air — the comfort axis)
    precip_prob_max: int = 30  # % — above this "rain risk"
    gusts_max_mph: float = 25.0  # above this "gusty"
    day_start_hour: int = 7  # waking window the solver considers
    day_end_hour: int = 22  # exclusive


def outdoor_prefs_from_overlay() -> OutdoorPrefs:
    """Resolve solver prefs from the user overlay, defaults for anything unset."""
    from harness.settings import overlay_get

    raw = overlay_get("travel", "outdoor", default=None)
    if isinstance(raw, dict):
        return OutdoorPrefs(**{k: v for k, v in raw.items() if k in OutdoorPrefs.model_fields})
    return OutdoorPrefs()


def _hour_verdict(h: HourlyWeather, p: OutdoorPrefs) -> str | None:
    """None = a good outdoor hour; else the disqualifier label. Missing data passes (an unknown
    never blocks a window — the daily flags cover the loud cases; the solver is a planner)."""
    feels = h.feels if h.feels is not None else h.temp
    if feels is not None and feels > p.feels_max_f:
        return f"feels {p.feels_max_f:.0f}°+"
    if feels is not None and feels < p.feels_min_f:
        return f"feels below {p.feels_min_f:.0f}°"
    if h.precip_prob is not None and h.precip_prob > p.precip_prob_max:
        return f"rain risk {p.precip_prob_max}%+"
    if h.gusts is not None and h.gusts > p.gusts_max_mph:
        return f"gusts {p.gusts_max_mph:.0f}+"
    return None


def _fmt_hour(iso_time: str) -> str:
    return iso_time[11:16] if len(iso_time) >= 16 else iso_time


def _span_end(iso_time: str) -> str:
    """The exclusive end label for a span whose last hour starts at iso_time (07:00 → 08:00)."""
    try:
        hh = int(iso_time[11:13])
        return f"{min(hh + 1, 24):02d}:00"
    except (ValueError, IndexError):
        return _fmt_hour(iso_time)


def compute_outdoor_plan(day: str, hours: list[HourlyWeather], prefs: OutdoorPrefs) -> OutdoorPlan:
    """PURE: one day's hourly forecast + comfort prefs -> good windows + avoid spans + the
    one-liner ("Best outdoor window 08:00–11:00 · avoid 14:00–19:00 (feels 85°+)"). Deterministic,
    fixture-testable, zero model in the loop — the standing agent's morning report renders it."""
    in_day = [
        h for h in hours
        if h.time.startswith(day) and prefs.day_start_hour <= int(h.time[11:13]) < prefs.day_end_hour
    ]
    windows: list[OutdoorWindow] = []
    avoid: list[OutdoorWindow] = []
    run: list[HourlyWeather] = []
    run_verdict: str | None = None
    run_open = False

    def _close() -> None:
        if not run:
            return
        span = OutdoorWindow(
            start=_fmt_hour(run[0].time), end=_span_end(run[-1].time), hours=len(run),
            why=run_verdict or _good_why(run),
        )
        (windows if run_verdict is None else avoid).append(span)

    for h in in_day:
        v = _hour_verdict(h, prefs)
        if not run_open or v != run_verdict:
            _close()
            run, run_verdict, run_open = [h], v, True
        else:
            run.append(h)
    _close()

    # Note assembly: lead with the LONGEST good window; name at most one avoid span (the longest) —
    # the tile/report one-liner, not an exhaustive dump (the spans themselves carry the rest).
    note = "no comfortable outdoor window today"
    if windows:
        best = max(windows, key=lambda w: w.hours)
        if len(windows) == 1 and not avoid and best.hours >= len(in_day):
            note = "comfortable outdoors all day"
        else:
            note = f"best outdoor window {best.start}–{best.end}"
    if avoid:
        worst = max(avoid, key=lambda w: w.hours)
        note += f" · avoid {worst.start}–{worst.end} ({worst.why})"
    return OutdoorPlan(date=day, windows=windows, avoid=avoid, note=note)


def _good_why(run: list[HourlyWeather]) -> str:
    feels = [h.feels if h.feels is not None else h.temp for h in run]
    known = [f for f in feels if f is not None]
    if not known:
        return "conditions clear"
    lo, hi = min(known), max(known)
    return f"feels {lo:.0f}–{hi:.0f}°" if lo != hi else f"feels {lo:.0f}°"


def conditions_tiles(rep: ConditionsReport) -> dict[str, float | str | None]:
    """PURE: the report → the FLAT tile contract for the Conditions-subtab stat row (tier C).

    Stable keys, one level deep (the dashboard plucks by value_path — the daygl/global flat-scalars
    precedent, never array-index paths). "Now" values prefer the current-conditions read and fall
    back to today's daily aggregates, honestly suffixed in the tile title, not silently swapped —
    a missing sense renders "—" (None), never a fabricated number."""
    d0 = rep.weather.days[0] if rep.weather and rep.weather.days else None
    a0 = rep.air.days[0] if rep.air and rep.air.days else None
    best = max(rep.outdoor.windows, key=lambda w: w.hours) if rep.outdoor and rep.outdoor.windows else None

    def r0(v: float | None) -> float | None:
        # Tile-display rounding (whole degrees/mph — "71°", never "71.20°"; eye-caught polish).
        return round(v) if v is not None else None

    uv = rep.now.uv if rep.now and rep.now.uv is not None else (d0.uv_index_max if d0 else None)
    gusts = (rep.now.gusts if rep.now and rep.now.gusts is not None
             else (d0.wind_gusts_max if d0 else None))
    return {
        "now_temp": r0(rep.now.temp if rep.now else None),
        "feels": r0(rep.now.feels if rep.now else None),
        "condition": (rep.now.condition if rep.now and rep.now.condition else
                      (d0.condition if d0 else None)),
        "uv": round(uv, 1) if uv is not None else None,  # UV keeps one decimal (a 0-11 scale)
        "gusts": r0(gusts),
        "aqi": float(a0.us_aqi_max) if a0 and a0.us_aqi_max is not None else None,
        "sunset": d0.sunset[11:16] if d0 and d0.sunset else None,
        "window": f"{best.start}–{best.end}" if best else "none",
    }


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
        feels = f" (feels {_fmt(d0.feels_max)})" if d0.feels_max is not None else ""
        head = f"{rep.home}: {d0.condition}, {_fmt(d0.temp_max)}{rep.weather.temperature_unit}{feels}"
    tail = f" · {rep.outdoor.note}" if rep.outdoor and rep.outdoor.note else ""
    if rep.flags:
        kinds = ", ".join(sorted({f.kind for f in rep.flags}))
        return f"{head} — {len(rep.flags)} alert(s): {kinds}{tail}"
    return f"{head} — quiet{tail}"


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
        feels = f" (feels {_fmt(d0.feels_max)})" if d0.feels_max is not None else ""
        out.append(f"**Today:** {d0.condition}, high {_fmt(d0.temp_max)}{tunit}{feels} / "
                   f"low {_fmt(d0.temp_min)}{tunit}.")
        sun = ""
        if d0.sunrise and d0.sunset:
            sun = f"Sun {d0.sunrise[11:16]}–{d0.sunset[11:16]}"
        extras = " · ".join(x for x in (
            sun,
            f"UV {d0.uv_index_max:.0f}" if d0.uv_index_max is not None else "",
            f"gusts {d0.wind_gusts_max:.0f} {wx.wind_unit}" if d0.wind_gusts_max is not None else "",
        ) if x)
        if extras:
            out.append(f"*{extras}*")
        out.append("")
    if rep.outdoor and rep.outdoor.note:
        out.append(f"**Outdoors:** {rep.outdoor.note}.")
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
        out.append("| Date | Conditions | High (feels) | Low | Rain | Snow | UV | Gusts | AQI |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for d in wx.days:
            rain = f"{d.precip_hours:.0f}h" if d.precip_hours else "—"
            snow = f"{d.snowfall_sum:.1f}{punit}" if d.snowfall_sum else "—"
            a = air_by_date.get(d.date)
            aqi = f"{a.us_aqi_max} ({a.category})" if a and a.us_aqi_max is not None else "—"
            high = _fmt(d.temp_max)
            if d.feels_max is not None:
                high += f" ({_fmt(d.feels_max)})"
            out.append(f"| {d.date} | {d.condition} | {high} | {_fmt(d.temp_min)} | "
                       f"{rain} | {snow} | {_fmt(d.uv_index_max)} | {_fmt(d.wind_gusts_max)} | {aqi} |")
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
