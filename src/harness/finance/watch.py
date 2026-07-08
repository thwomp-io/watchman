"""Standing-watch layer — the one-shot vigilance pass behind `hn finance watch`.

Composes mechanical checks the corpus previously held only as prose:
- **Rebalance-band drift** — positions vs. the configured bands in `portfolio.yaml`; the corpus's
  rebalance-band drift check made mechanical. Symbols without bands aren't checked.
- **Concentrated-holding wash-sale window** — vest dates (manual-synced from a vesting calendar)
  poison loss-sales ±30 days; reports poisoned/clean *today* + the next clean window.
- **Days-to-print** — CONFIRMED earnings dates (nasdaq analyst API, day-TTL cache) where
  available, honest `est.` from 10-Q/10-K filing cadence where not. The label is the contract:
  a consumer can always tell an announcement from a projection (the wire-integrity fix for the
  month-off cadence-estimate class).
- **Ratings wire** — consensus PT + rating-mix snapshots per held name (same nasdaq API, same
  TTL), diffed run-over-run; a material move surfaces as a `[RATING]` wire item so a price-target
  cut on a holding is first-class news, not luck-of-the-RSS.
- **New headlines only** — the news scan filtered through a seen-cache
  (`~/.cache/harness/news-seen.json`), so repeat watches surface deltas, not repeats.

Read-only by doctrine. Cron-able later (scheduling — cron/launchd — is left to the user); v1 is on-demand.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from harness.finance.models import ConsensusPT, EarningsDate, NewsItem, Position, PrintCountdown, Quote

SEEN_CACHE = Path.home() / ".cache" / "harness" / "news-seen.json"
EARNINGS_CACHE = Path.home() / ".cache" / "harness" / "earnings-dates.json"
CONSENSUS_STATE = Path.home() / ".cache" / "harness" / "ratings-consensus.json"
_WASH_DAYS = 30


class DriftFlag(BaseModel):
    symbol: str
    pct_of_account: float
    band_min: float
    band_max: float
    status: str  # "in-band" | "above" | "below"


class WashSaleStatus(BaseModel):
    today_poisoned: bool
    reason: str  # which vest poisons today, or "clean"
    next_clean_start: str | None = None
    next_clean_end: str | None = None
    note: str = ""
    # lot-aware enrichment: the harvestable-loss inventory at the current price,
    # populated by the service when the holding has per-lot basis. None = lots not available.
    harvestable_loss: float | None = None  # sum of underwater-lot losses (negative)
    harvestable_shares: float | None = None


def check_drift(
    positions_pct: dict[str, float], bands: dict[str, dict[str, float]]
) -> list[DriftFlag]:
    out: list[DriftFlag] = []
    for sym, band in bands.items():
        pct = positions_pct.get(sym)
        if pct is None:
            continue
        lo, hi = float(band.get("min", 0)), float(band.get("max", 100))
        status = "above" if pct > hi else "below" if pct < lo else "in-band"
        out.append(
            DriftFlag(symbol=sym, pct_of_account=round(pct, 2), band_min=lo, band_max=hi, status=status)
        )
    return out


def wash_sale_status(vest_dates: list[str], today: date | None = None) -> WashSaleStatus:
    """Is a concentrated-holding loss-sale wash-sale-poisoned today, and when is the next fully-clean window?

    A window is "clean" when no vest lands within ±30 days. (The share-count-cap path — harvest
    bigger than the replacement shares — works even inside poisoned windows; see the TLH plan.)"""
    today = today or date.today()
    vests = sorted(date.fromisoformat(v) for v in vest_dates)
    poison: list[tuple[date, date, date]] = [
        (v - timedelta(days=_WASH_DAYS), v + timedelta(days=_WASH_DAYS), v) for v in vests
    ]
    hit = next((p for p in poison if p[0] <= today <= p[1]), None)

    # next clean day ≥ today: scan forward past merged poison ranges
    cursor = today
    for lo, hi, _v in poison:
        if cursor >= lo and cursor <= hi:
            cursor = hi + timedelta(days=1)
    # clean window ends the day before the next poison range begins after cursor
    nxt = next((lo for lo, _hi, _v in poison if lo > cursor), None)
    end = (nxt - timedelta(days=1)) if nxt else None

    known_through = vests[-1].isoformat() if vests else "—"
    return WashSaleStatus(
        today_poisoned=hit is not None,
        reason=(f"±30d window of the {hit[2].isoformat()} vest" if hit else "clean"),
        next_clean_start=cursor.isoformat(),
        next_clean_end=end.isoformat() if end else None,
        note=f"vest schedule known through {known_through} — confirm forward schedule with your broker",
    )


class WatchlistMove(BaseModel):
    """A watchlist (non-held) symbol's day read.

    `available=False` = not on the IEX feed (e.g. OTC ADRs) — quoted nowhere, but still news-covered;
    the miss is stated, never a false-empty."""

    symbol: str
    note: str = ""  # the one-line why from portfolio.yaml (lane / screen status)
    available: bool = True
    price: float | None = None
    day_change_pct: float | None = None


class WatchDigest(BaseModel):
    """One-shot standing-watch digest — everything `hn finance watch` composes.

    Read-only observation. Drift bands are illustrative until the user sets real targets in portfolio.yaml."""

    as_of: str
    day_moves: list[Position] = Field(default_factory=list)  # live positions w/ day change
    watchlist_moves: list[WatchlistMove] = Field(default_factory=list)  # non-held watched symbols
    drift: list[DriftFlag] = Field(default_factory=list)
    wash_sale: WashSaleStatus | None = None
    prints: list[PrintCountdown] = Field(default_factory=list)
    fresh_news: list[NewsItem] = Field(default_factory=list)  # seen-cache-filtered deltas
    notes: list[str] = Field(default_factory=list)


class OpenOrderStatus(BaseModel):
    """A resting order vs the live price — pulse's trap-distance view."""

    symbol: str
    side: str
    qty: float
    limit: float
    price: float | None = None
    distance_pct: float | None = None  # (price - limit) / price * 100 for buys; sign-flipped for sells
    day_pct: float | None = None  # underlying's signed day move — negative = fell today = trap got CLOSER
    expires: str = ""
    note: str = ""


class PulseFlag(BaseModel):
    """One deterministic escalation flag. The model narrates; this detects."""

    kind: str  # day_move | trap_proximity | print_soon
    symbol: str
    message: str


class PulseReport(BaseModel):
    """The scheduled-agent contract: quiet=True -> end silently;
    flags -> notify. Composes the watch digest underneath (delta news rides the seen-cache)."""

    as_of: str
    quiet: bool = True
    flags: list[PulseFlag] = Field(default_factory=list)
    orders: list[OpenOrderStatus] = Field(default_factory=list)
    indexes: list[Quote] = Field(default_factory=list)  # index-level tape sense
    digest: WatchDigest | None = None


class SeenCache(BaseModel):
    """URL-keyed seen-set for delta news. Tiny, local, transparent."""

    seen: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path = SEEN_CACHE) -> SeenCache:
        if path.exists():
            try:
                return cls.model_validate(json.loads(path.read_text()))
            except (json.JSONDecodeError, ValueError):
                return cls()
        return cls()

    def filter_new(self, items: list[NewsItem]) -> list[NewsItem]:
        s = set(self.seen)
        return [i for i in items if i.url and i.url not in s]

    def mark(self, items: list[NewsItem]) -> None:
        s = set(self.seen)
        s.update(i.url for i in items if i.url)
        self.seen = sorted(s)[-2000:]  # bounded; oldest URLs age out

    def save(self, path: Path = SEEN_CACHE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json())


class EarningsDateCache(BaseModel):
    """Per-symbol confirmed-earnings-date cache (nasdaq provider), day-scale TTL.

    Volume discipline for an unofficial API: the standing pulse runs many times per market day —
    without this cache each run would re-sweep the whole book against api.nasdaq.com. One fetch
    per symbol per `ttl_days` keeps the footprint at ~one sweep/day; the date itself moves on a
    quarter timescale, so a day-stale confirmed date is still better than a cadence estimate.
    """

    # symbol → {"date": "YYYY-MM-DD", "confirmed": bool, "fetched": "YYYY-MM-DD"}
    entries: dict[str, dict[str, str | bool]] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = EARNINGS_CACHE) -> EarningsDateCache:
        if path.exists():
            try:
                return cls.model_validate(json.loads(path.read_text()))
            except (json.JSONDecodeError, ValueError):
                return cls()
        return cls()

    def fresh(self, symbol: str, today: date, ttl_days: int = 1) -> EarningsDate | None:
        """The cached date, if fetched within the TTL — else None (caller refetches)."""
        e = self.entries.get(symbol.upper())
        if not e:
            return None
        try:
            fetched = date.fromisoformat(str(e["fetched"]))
            if (today - fetched).days > ttl_days:
                return None
            return EarningsDate(
                symbol=symbol.upper(),
                report_date=date.fromisoformat(str(e["date"])),
                confirmed=bool(e["confirmed"]),
            )
        except (KeyError, ValueError):
            return None

    def put(self, ed: EarningsDate, today: date) -> None:
        self.entries[ed.symbol] = {
            "date": ed.report_date.isoformat(),
            "confirmed": ed.confirmed,
            "fetched": today.isoformat(),
        }

    def save(self, path: Path = EARNINGS_CACHE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json())


class ConsensusState(BaseModel):
    """Last-seen consensus PT snapshot per symbol — the diff baseline for the ratings wire.

    `diff()` is pure (no I/O): given a fresh snapshot it returns a one-line change description
    when the move is material (mean PT moved ≥ `pt_move_pct`, or the buy/hold/sell mix changed),
    else None. First sight of a symbol just seeds the baseline silently — a baseline is not news.
    The same TTL discipline as EarningsDateCache bounds fetch volume (`stale()` gates refetch).
    """

    # symbol → {"mean": float, "buy": int, "hold": int, "sell": int, "fetched": "YYYY-MM-DD"}
    entries: dict[str, dict[str, float | int | str]] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CONSENSUS_STATE) -> ConsensusState:
        if path.exists():
            try:
                return cls.model_validate(json.loads(path.read_text()))
            except (json.JSONDecodeError, ValueError):
                return cls()
        return cls()

    def stale(self, symbol: str, today: date, ttl_days: int = 1) -> bool:
        e = self.entries.get(symbol.upper())
        if not e:
            return True
        try:
            return (today - date.fromisoformat(str(e["fetched"]))).days > ttl_days
        except (KeyError, ValueError):
            return True

    def diff(self, pt: ConsensusPT, pt_move_pct: float = 2.0) -> str | None:
        """Material-change line vs the stored baseline, or None (incl. first sight)."""
        e = self.entries.get(pt.symbol)
        if not e:
            return None
        try:
            prior_mean = float(e["mean"])
            prior_mix = (int(e["buy"]), int(e["hold"]), int(e["sell"]))
        except (KeyError, TypeError, ValueError):
            return None
        parts: list[str] = []
        if prior_mean > 0:
            move = (pt.mean - prior_mean) / prior_mean * 100
            if abs(move) >= pt_move_pct:
                parts.append(f"consensus PT ${prior_mean:,.2f} → ${pt.mean:,.2f} ({move:+.1f}%)")
        mix = (pt.buy, pt.hold, pt.sell)
        if mix != prior_mix:
            parts.append(
                f"rating mix {prior_mix[0]}B/{prior_mix[1]}H/{prior_mix[2]}S"
                f" → {mix[0]}B/{mix[1]}H/{mix[2]}S"
            )
        return "; ".join(parts) if parts else None

    def put(self, pt: ConsensusPT, today: date) -> None:
        self.entries[pt.symbol] = {
            "mean": pt.mean,
            "buy": pt.buy,
            "hold": pt.hold,
            "sell": pt.sell,
            "fetched": today.isoformat(),
        }

    def save(self, path: Path = CONSENSUS_STATE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json())
