"""Event-study arithmetic over daily closes: a window's per-day moves across names, and what followed a
symbol's worst days. Pure: the service gathers the bars, these functions only count.

Closes are split-adjusted (the bars request asks for it), so a split never reads as a crash. Dividends
stay in, so these are price returns, not total returns.
"""

from __future__ import annotations

from datetime import date

from harness.finance.models import CrashDay, CrashDays, MoveGroup, MoveRow, MovesTable

Closes = list[tuple[date, float]]  # ascending by date; one close per session


def _pct(new: float, old: float) -> float:
    return (new / old - 1.0) * 100.0


def moves(
    series: dict[str, Closes],
    start: date,
    end: date,
    *,
    sources: dict[str, str] | None = None,
    reasons: dict[str, str] | None = None,
    groups: dict[str, list[str]] | None = None,
) -> MovesTable:
    """Each name's close-to-close move on every session in (start, end], and the cumulative from the last
    close on or before `start`. The sessions come from the data: a date any name traded. A name with no
    close on a session gets a blank there, and no cumulative unless it has both ends."""
    sessions = sorted({d for closes in series.values() for d, _ in closes if start < d <= end})
    rows: list[MoveRow] = []
    for sym, closes in series.items():
        row = MoveRow(symbol=sym, source=(sources or {}).get(sym, "alpaca"))
        base = [c for d, c in closes if d <= start]
        by_day = {d: c for d, c in closes}
        if not base:
            row.reason = f"no close on or before {start.isoformat()}"
        prev = base[-1] if base else None
        first = prev
        for d in sessions:
            c = by_day.get(d)
            row.moves.append(None if (c is None or prev is None) else _pct(c, prev))
            if c is not None:
                prev = c
        last = [c for d, c in closes if start < d <= end]
        if first is not None and last and sessions and by_day.get(sessions[-1]) is not None:
            row.cumulative = _pct(last[-1], first)
        rows.append(row)
    for sym, why in (reasons or {}).items():
        rows.append(MoveRow(symbol=sym, source="", reason=why, moves=[None] * len(sessions)))
    out = MovesTable(
        start=start.isoformat(), end=end.isoformat(), sessions=[d.isoformat() for d in sessions], rows=rows
    )
    by_sym = {r.symbol: r for r in rows}
    for name, members in (groups or {}).items():
        got = [by_sym[m] for m in members if m in by_sym]
        g = MoveGroup(name=name, members=members)
        for i in range(len(sessions)):
            vals = [r.moves[i] for r in got if r.moves[i] is not None]
            g.moves.append(sum(vals) / len(vals) if vals else None)  # type: ignore[arg-type]
        cums = [r.cumulative for r in got if r.cumulative is not None]
        g.cumulative = sum(cums) / len(cums) if cums else None
        g.counted = len(cums)
        out.groups.append(g)
    return out


def crash_days(
    symbol: str,
    closes: Closes,
    threshold: float,
    since: date,
    *,
    horizons: tuple[int, ...] = (5, 20, 60, 120, 250),
    low_window: int = 60,
    cluster_days: int = 0,
) -> CrashDays:
    """Every session on or after `since` whose close-to-close move is at or below `threshold` (%), with its
    drawdown from the highest prior close, the forward return at each horizon (sessions), and the lowest
    close in the next `low_window` sessions. A horizon not yet reached is None, never zero.

    `cluster_days` > 0 folds events within that many sessions of the previous one into one episode, shown
    on its deepest day (the forward returns are measured from that day)."""
    out = CrashDays(
        symbol=symbol,
        threshold=threshold,
        since=since.isoformat(),
        horizons=list(horizons),
        low_window=low_window,
        through=closes[-1][0].isoformat() if closes else "",
    )
    events: list[CrashDay] = []
    peak = None
    for i, (d, c) in enumerate(closes):
        if i and d >= since:
            move = _pct(c, closes[i - 1][1])
            if move <= threshold:
                ev = CrashDay(
                    date=d.isoformat(), move_pct=move, index=i, off_high_pct=_pct(c, peak) if peak else None
                )
                ev.forward = {
                    str(k): (_pct(closes[i + k][1], c) if i + k < len(closes) else None) for k in horizons
                }
                if i + low_window < len(closes):
                    ev.worst_low_pct = _pct(min(x for _, x in closes[i + 1 : i + 1 + low_window]), c)
                events.append(ev)
        peak = c if peak is None else max(peak, c)
    if cluster_days > 0:
        folded: list[CrashDay] = []
        for ev in events:
            last = folded[-1] if folded else None
            if last is not None and ev.index - last.episode_last_index <= cluster_days:
                last.episode_days += 1
                last.episode_end = ev.date
                last.episode_last_index = ev.index
                if ev.move_pct < last.move_pct:
                    keep = (last.episode_start, last.episode_days, last.episode_end, ev.index)
                    ev.episode_start, ev.episode_days, ev.episode_end, ev.episode_last_index = keep
                    folded[-1] = ev
                continue
            ev.episode_start = ev.episode_end = ev.date
            ev.episode_last_index = ev.index
            folded.append(ev)
        events = folded
    out.rows = events
    out.count = len(events)
    return out
