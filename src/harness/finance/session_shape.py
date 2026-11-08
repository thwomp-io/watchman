"""The intraday shape of one regular session — pure: bars in, metrics out.

What a take asks after nearly every close: where was the low and when, how far off it did the close
finish, what did the last 15/30 minutes do, and was that move on volume. Everything here is computed
from regular-session bars (09:30-16:00 ET); extended hours are out. Times are New York time.

Prices can't say who bought: this reports shape and volume only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from harness.finance.models import Bar, SessionBar, SessionShape
from harness.finance.sessions import CLOSE, ET, OPEN


def _et(bar: Bar) -> datetime:
    return datetime.fromisoformat(bar.t.replace("Z", "+00:00")).astimezone(ET)


def regular_bars(bars: list[Bar], session: date, minutes: int) -> list[tuple[datetime, Bar]]:
    """The bars that start inside `session`'s regular hours, with their ET start times, in order."""
    start = datetime.combine(session, OPEN, tzinfo=ET)
    end = datetime.combine(session, CLOSE, tzinfo=ET)
    out = [(t, b) for b in bars if start <= (t := _et(b)) < end]
    return sorted(out, key=lambda tb: tb[0])


def shape(
    symbol: str,
    session: date,
    bars: list[Bar],
    *,
    minutes: int,
    complete: bool,
    official_close: float | None = None,
) -> SessionShape:
    """Summarize `session` from its bars. `complete` = the session has closed; then `official_close`
    (the consolidated close) is the close when given, else the last bar's close (labeled)."""
    reg = regular_bars(bars, session, minutes)
    out = SessionShape(symbol=symbol, session=session.isoformat(), bar=f"{minutes}Min",
                       state="complete" if complete else "partial")
    if not reg:
        out.state = "no data"
        out.notes.append("no regular-session bars (a holiday, a weekend, or not traded)")
        return out
    span = timedelta(minutes=minutes)
    first_t, first = reg[0]
    out.open = first.o
    hi_t, hi = max(reg, key=lambda tb: tb[1].h)
    lo_t, lo = min(reg, key=lambda tb: tb[1].low)
    out.high, out.high_bar = hi.h, f"{hi_t:%H:%M}"
    out.low, out.low_bar = lo.low, f"{lo_t:%H:%M}"
    last_t, last = reg[-1]
    if complete and official_close is not None:
        out.close, out.close_basis = official_close, "official close"
    else:
        out.close = last.c
        out.close_basis = "last bar close" if complete else f"latest ({last_t + span:%H:%M} ET)"
    out.close_vs_low_pct = (out.close / out.low - 1.0) * 100.0 if out.low else None
    out.close_vs_high_pct = (out.close / out.high - 1.0) * 100.0 if out.high else None

    anchor = last_t + span  # the end of the newest bar (16:00 for a complete session)

    def price_at(cutoff: datetime) -> float | None:
        before = [b.c for t, b in reg if t + span <= cutoff]
        return before[-1] if before else None

    for back, attr in ((15, "last15_pct"), (30, "last30_pct")):
        ref = price_at(anchor - timedelta(minutes=back))
        if ref:
            setattr(out, attr, (out.close / ref - 1.0) * 100.0)

    out.volume = sum(b.v for _, b in reg)
    elapsed = (anchor - datetime.combine(session, OPEN, tzinfo=ET)).total_seconds() / 60.0
    out.avg_15min_volume = out.volume / (elapsed / 15.0) if elapsed > 0 else None
    out.last15_volume = sum(b.v for t, b in reg if t >= anchor - timedelta(minutes=15))
    if out.avg_15min_volume:
        out.last15_rel_volume = out.last15_volume / out.avg_15min_volume
    out.bars = [SessionBar(t=f"{t:%H:%M}", o=b.o, h=b.h, low=b.low, c=b.c, v=b.v) for t, b in reg]
    return out
