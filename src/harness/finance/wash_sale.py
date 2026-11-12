"""The wash-sale window of a holding that vests on a schedule.

A loss sale within 30 days of acquiring the same security is a wash sale, and a vest is an
acquisition. So every vest date poisons the 30 days either side of it, and a loss can be harvested
cleanly only in the gaps between those ranges.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

WASH_DAYS = 30


class WashSaleStatus(BaseModel):
    today_poisoned: bool
    reason: str  # which vest poisons today, or "clean"
    next_clean_start: str | None = None
    next_clean_end: str | None = None
    note: str = ""
    # What a harvest would realize at the current price, when the holding carries per-lot basis.
    # None means the lots aren't available, not that there is nothing to harvest.
    harvestable_loss: float | None = None  # the sum of the underwater lots' losses (negative)
    harvestable_shares: float | None = None


def wash_sale_status(vest_dates: list[str], today: date | None = None) -> WashSaleStatus:
    """Whether a loss sale today would be a wash sale, and when the next fully clean window is.

    A window is clean when no vest lands within 30 days of it. (Harvesting more shares than the
    vest replaces still works inside a poisoned window; that path isn't modeled here.)
    """
    today = today or date.today()
    vests = sorted(date.fromisoformat(v) for v in vest_dates)
    margin = timedelta(days=WASH_DAYS)
    poisoned = [(v - margin, v + margin, v) for v in vests]
    hit = next((p for p in poisoned if p[0] <= today <= p[1]), None)

    # The next clean day: walk forward past every poisoned range that covers the cursor. The ranges
    # are in date order, so overlapping ones chain.
    cursor = today
    for start, end, _vest in poisoned:
        if start <= cursor <= end:
            cursor = end + timedelta(days=1)
    next_poisoned = next((start for start, _end, _vest in poisoned if start > cursor), None)
    clean_end = next_poisoned - timedelta(days=1) if next_poisoned else None

    known_through = vests[-1].isoformat() if vests else "—"
    return WashSaleStatus(
        today_poisoned=hit is not None,
        reason=f"±{WASH_DAYS}d window of the {hit[2].isoformat()} vest" if hit else "clean",
        next_clean_start=cursor.isoformat(),
        next_clean_end=clean_end.isoformat() if clean_end else None,
        note=f"vest schedule known through {known_through} — confirm forward schedule with your broker",
    )
