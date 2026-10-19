"""Which US equity session a price-derived number measures — pure, `now` injected.

A day % built from quotes is only meaningful relative to a session. Outside the regular session
(09:30-16:00 ET) the fund proxy's "EOD estimate" still divides today's thin after-hours ADR ticks
by the prior close — it measures a session whose NAV is already struck, drifting on no new
information, while the next session (Asia's) is not in the number at all. Naming the session is
what keeps an overnight read from passing as a live one.

Weekends are handled; exchange holidays are not modeled (a holiday reads as a closed session
dated the holiday's weekday — the label is still honest that no session is live).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)

SessionState = Literal["live", "pre-open", "closed"]


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def us_session(now: datetime | None = None) -> tuple[SessionState, date]:
    """(state, the session date a quote-derived day % measures right now)."""
    et = (now or datetime.now(UTC)).astimezone(ET)
    today = et.date()
    if today.weekday() >= 5:
        return "closed", _prev_weekday(today)
    if et.time() < OPEN:
        return "pre-open", _prev_weekday(today)
    if et.time() < CLOSE:
        return "live", today
    return "closed", today
