"""The standing watch: one pass over the book, behind `hn finance watch`.

It composes checks that are each mechanical:

- Day moves for what is held and what is watched.
- Rebalance-band drift: each banded position against its band in `portfolio.yaml`.
- The wash-sale window of the holding that vests on a schedule (`wash_sale`).
- Days to each held stock's next print. A confirmed date (Nasdaq's analyst API) is labeled
  confirmed; otherwise the date is an estimate from the filing cadence and labeled `est.`, so a
  reader can always tell an announcement from a projection.
- Rating changes: a material move in a held stock's consensus price target or rating mix, as a
  `[RATING]` item beside the headlines.
- New headlines only: every feed is filtered through the seen-cache, so a repeat run reports what
  changed since the last one.

Read-only. What it remembers between runs lives in `watch_state`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import date
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from harness.finance import catalysts, service
from harness.finance.models import NewsItem, Position, PrintCountdown
from harness.finance.providers import nasdaq_provider, news_provider
from harness.finance.providers.base import ProviderError
from harness.finance.providers.news_provider import fetch_recent_filings
from harness.finance.research import estimate_next_print
from harness.finance.scan import run_parallel
from harness.finance.unwind import classify_lots
from harness.finance.wash_sale import WashSaleStatus, wash_sale_status
from harness.finance.watch_state import ConsensusState, EarningsDateCache, SeenCache

if TYPE_CHECKING:
    from harness.finance.corpus.reader import PortfolioSeed
    from harness.finance.models import PortfolioSnapshot
    from harness.finance.service import FinanceService


class DriftFlag(BaseModel):
    symbol: str
    pct_of_account: float
    band_min: float
    band_max: float
    status: str  # "in-band" | "above" | "below"


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
    # the catalyst junk filter's catch (quote/option-chain pages) — out of fresh_news, kept for the
    # Inbox to count and reveal; each item's `filtered` names the rule
    filtered_news: list[NewsItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Section(StrEnum):
    """The independent parts of the digest. A caller that needs one (a dashboard widget showing the
    day's moves) asks for it alone and pays only for what that part reads."""

    MOVES = "moves"
    WATCHLIST = "watchlist"
    DRIFT = "drift"
    WASH_SALE = "wash-sale"
    PRINTS = "prints"
    NEWS = "news"


_READS_POSITIONS = {Section.MOVES, Section.DRIFT, Section.WASH_SALE}


def watch(
    svc: FinanceService,
    *,
    mark_seen: bool = True,
    news_limit: int = 4,
    ticker_news: bool = True,
    only: Collection[Section] | None = None,
) -> WatchDigest:
    """Build the digest, or just the sections named in `only`.

    `mark_seen` records the headlines this run reports, so the next run doesn't repeat them; pass
    False to look without consuming them. `ticker_news` adds a news search per held stock, the
    slowest part of the news section.
    """
    wanted = set(only or Section)
    seed = svc.reader.read_portfolio()
    today = date.today()
    digest = WatchDigest(as_of=today.isoformat())
    snap = svc.positions() if wanted & _READS_POSITIONS else None

    if snap and Section.MOVES in wanted:
        digest.day_moves = _day_moves(snap)
    if Section.WATCHLIST in wanted and seed.watchlist:
        digest.watchlist_moves = _watchlist_moves(svc, seed, digest.notes)
    if snap and Section.DRIFT in wanted and seed.rebalance_bands:
        digest.drift = _band_drift(snap, seed)
        digest.notes.append(
            "Bands are illustrative until real targets are set in portfolio.yaml; "
            "drift % is of the holding's own brokerage account, not net worth."
        )
    if snap and Section.WASH_SALE in wanted and seed.vest_dates:
        digest.wash_sale = _wash_sale(seed, snap, today)
    if Section.PRINTS in wanted:
        digest.prints = _print_dates(svc, seed, today, digest.notes)
    if Section.NEWS in wanted:
        digest.fresh_news, digest.filtered_news = _news(
            svc, seed, today, digest.notes,
            mark_seen=mark_seen, news_limit=news_limit, ticker_news=ticker_news,
        )
    digest.notes.append(
        "Read-only observation, not advice. Wash-sale math covers KNOWN vests only — "
        "confirm the forward schedule with your broker before acting on a harvest window."
    )
    return digest


def _day_moves(snap: PortfolioSnapshot) -> list[Position]:
    """The live-priced positions, biggest day move first."""
    live = [p for p in snap.positions if p.valuation == "live" and p.day_change_pct is not None]
    return sorted(live, key=lambda p: abs(p.day_change_pct or 0), reverse=True)


def _news(
    svc: FinanceService,
    seed: PortfolioSeed,
    today: date,
    notes: list[str],
    *,
    mark_seen: bool,
    news_limit: int,
    ticker_news: bool,
) -> tuple[list[NewsItem], list[NewsItem]]:
    """What is new since the last run, as (fresh, filtered): rating changes, then each symbol's
    headlines, then the custom feeds, all passed through the seen-cache. `filtered` is what the
    catalyst rules took out (quote and option-chain pages are not news), kept so a reader can count
    it. A source that fails is a note; the rest still report."""
    seen = SeenCache.load()
    found = _rating_changes(seed, today)
    symbols = [h.symbol for h in seed.holdings if h.asset_type in {"stock", "etf"}]
    symbols += [w.symbol for w in seed.watchlist if w.symbol not in symbols]
    for symbol_news in svc.news(symbols=symbols, limit=news_limit):
        if symbol_news.headline_error:
            notes.append(f"{symbol_news.symbol} headlines failed: {symbol_news.headline_error}")
        found.extend(seen.filter_new(symbol_news.headlines))
    for scan in run_parallel(_feed_jobs(svc, seed, news_limit, ticker_news)):
        if scan.error is not None:
            notes.append(f"{scan.label} failed: {scan.error}")
        elif scan.value:
            found.extend(seen.filter_new(scan.value))

    fresh, filtered = catalysts.triage(found, catalysts.config_rules())
    if mark_seen and (fresh or filtered):
        seen.mark(fresh + filtered)
        seen.save()
    return fresh, filtered


def _watchlist_moves(
    svc: FinanceService, seed: PortfolioSeed, notes: list[str]
) -> list[WatchlistMove]:
    """Day reads for the non-held symbols the user keeps tabs on, biggest move first. A symbol the
    quote feed doesn't carry (an OTC ADR, say) comes back available=False; the news scan still
    covers it."""
    notes_by_sym = {w.symbol: w.note for w in seed.watchlist}
    try:
        quotes = svc.quote([w.symbol for w in seed.watchlist])
    except ProviderError as e:
        notes.append(f"watchlist quotes failed: {e}")
        quotes = []
    moves = []
    for q in quotes:
        day_pct: float | None = None
        if q.available and q.price is not None and q.prev_close:
            day_pct = (q.price - q.prev_close) / q.prev_close * 100.0
        moves.append(WatchlistMove(
            symbol=q.symbol,
            note=notes_by_sym.get(q.symbol, ""),
            available=q.available,
            price=q.price,
            day_change_pct=round(day_pct, 2) if day_pct is not None else None,
        ))
    return sorted(moves, key=lambda m: abs(m.day_change_pct or 0), reverse=True)


def _band_drift(snap: PortfolioSnapshot, seed: PortfolioSeed) -> list[DriftFlag]:
    """Each banded position as a % of its OWN brokerage account: a band means "% of this account",
    so a position at a separate broker must not dilute the denominator. Static balances are out."""
    marked = [
        p for p in snap.positions if p.market_value is not None and p.valuation in {"live", "last_known"}
    ]
    account_totals: dict[str, float] = {}
    for p in marked:
        account_totals[p.account] = account_totals.get(p.account, 0.0) + (p.market_value or 0.0)
    pct = {
        p.symbol: (p.market_value or 0.0) / account_totals[p.account] * 100.0
        for p in marked
        if account_totals.get(p.account)
    }
    return check_drift(pct, seed.rebalance_bands)


def _wash_sale(seed: PortfolioSeed, snap: PortfolioSnapshot, today: date) -> WashSaleStatus:
    """The concentrated holding's wash-sale window from its vest dates. When that holding carries
    lots and has a live price, the loss lots are sized too, so the digest can say "$X harvestable"
    rather than only poisoned or clean."""
    status = wash_sale_status(seed.vest_dates)
    conc = next((h for h in seed.holdings if h.lots), None)
    pos = next((p for p in snap.positions if p.symbol == conc.symbol), None) if conc else None
    if conc and pos and pos.price:
        losses = [
            lt
            for lt in classify_lots(conc.lots, pos.price, poisoned=status.today_poisoned, today=today)
            if lt.klass == "loss"
        ]
        status.harvestable_loss = round(sum(lt.unrealized_gl for lt in losses), 2)
        status.harvestable_shares = round(sum(lt.qty for lt in losses), 4)
    return status


def _print_dates(
    svc: FinanceService, seed: PortfolioSeed, today: date, notes: list[str]
) -> list[PrintCountdown]:
    """Days to each held stock's next print. A CONFIRMED date (Nasdaq's analyst API, cached for the
    day) comes first; otherwise an estimate from the EDGAR filing cadence. The label is the contract:
    "= … (confirmed · nasdaq)" vs "≈ … (est. …)", so a reader can always tell an announcement from a
    projection.

    Every stock not looked up today is fetched on the bounded pool, and a miss is cached like a hit,
    so a name between prints isn't asked again every run. A throttle is not an answer: that symbol
    stays unknown and the next run asks again.
    """
    cache = EarningsDateCache.load()
    dirty = False
    stocks = [h for h in seed.holdings if h.asset_type == "stock"]
    to_fetch = [h.symbol for h in stocks if not cache.known(h.symbol, today)]
    unchecked: list[str] = []
    jobs = [(sym, partial(nasdaq_provider.fetch_earnings_date, sym)) for sym in to_fetch]
    for r in run_parallel(jobs):
        if r.value is not None:
            cache.put(r.value, today)
        elif r.error is not None and r.error.transient:
            unchecked.append(r.label)
            continue
        else:
            cache.put_miss(r.label, today)  # an honest miss: ETF-shaped, or no date set yet
        dirty = True
    if unchecked:
        notes.append(
            f"Confirmed print dates weren't checked for {len(unchecked)} symbols (nasdaq refused the "
            f"requests): {', '.join(unchecked)}. Their dates below are estimates."
        )

    prints = []
    for h in stocks:
        ed = cache.fresh(h.symbol, today)
        if ed is not None and ed.confirmed:
            prints.append(PrintCountdown(
                symbol=h.symbol,
                estimate=f"= {ed.report_date.isoformat()} (confirmed · nasdaq)",
                days_out=(ed.report_date - today).days,
                confirmed=True,
            ))
            continue
        # Nasdaq's own projected date is the same class of guess; the filing-cadence one can be
        # audited from the filings, so it is the one shown, labeled est.
        estimate = _estimated_print(svc, h.symbol, today, notes)
        if estimate:
            prints.append(estimate)
    if dirty:
        cache.save()
    return prints


def _estimated_print(
    svc: FinanceService, symbol: str, today: date, notes: list[str]
) -> PrintCountdown | None:
    cik = svc.cik_for(symbol)
    if not cik:
        return None
    try:
        est = estimate_next_print(fetch_recent_filings(cik, limit=14))
    except ProviderError as e:
        notes.append(f"{symbol} print estimate failed: {e}")
        return None
    if not est:
        return None
    days: int | None = None
    try:
        days = (date.fromisoformat(est.split()[1]) - today).days
    except (ValueError, IndexError):
        pass
    return PrintCountdown(symbol=symbol, estimate=est, days_out=days)


def _rating_changes(seed: PortfolioSeed, today: date) -> list[NewsItem]:
    """A material move in a held stock's consensus price target or rating mix, as a synthetic
    `[RATING]` news item, so it rides the fresh-news rendering and the catalyst → bus path.

    Each symbol is fetched at most once a day against the unofficial API and diffed against the
    stored baseline; the first sighting only seeds that baseline. No coverage is an honest miss,
    never a made-up baseline.
    """
    state = ConsensusState.load()
    dirty = False
    items = []
    for h in seed.holdings:
        if h.asset_type != "stock" or not state.stale(h.symbol, today):
            continue
        try:
            pt = nasdaq_provider.fetch_price_target(h.symbol)
        except ProviderError:
            continue
        change = state.diff(pt)
        if change:
            items.append(NewsItem(
                symbol=h.symbol,
                title=f"[RATING] {h.symbol}: {change}",
                # a key per change, so the seen-cache dedupes it like any headline
                url=f"consensus://{h.symbol}/{today.isoformat()}/{pt.mean:.2f}",
                source="nasdaq-consensus",
                published=today.isoformat(),
            ))
        state.put(pt, today)
        dirty = True
    if dirty:
        state.save()
    return items


def _feed_jobs(
    svc: FinanceService, seed: PortfolioSeed, news_limit: int, ticker_news: bool
) -> list[tuple[str, Callable[[], list[NewsItem]]]]:
    """The custom feeds as one bounded, host-paced fan-out: feeds.yaml wires and thesis topics, each
    held stock's EDGAR 8-K feed, and (with `ticker_news`) a Google News search per held stock for the
    single-name catalysts a per-ticker feed misses. Results come back in job order, so the digest
    matches a serial run; each job's failure is the same one-line note."""
    jobs: list[tuple[str, Callable[[], list[NewsItem]]]] = []
    for feed in service.load_feeds():  # called through the module, where tests replace it
        fetch = partial(news_provider.fetch_rss, feed["url"], feed["name"], limit=news_limit)
        jobs.append((f"feed {feed['name']}", fetch))
    held_stocks = [h for h in seed.holdings if h.asset_type == "stock"]
    for h in held_stocks:
        cik = svc.cik_for(h.symbol)
        if cik:
            fetch = partial(news_provider.fetch_edgar_filing_feed, cik, h.symbol, limit=3)
            jobs.append((f"EDGAR feed {h.symbol}", fetch))
    if ticker_news:
        for h in held_stocks:
            fetch = partial(news_provider.fetch_gnews_ticker, h.symbol, limit=news_limit)
            jobs.append((f"gnews {h.symbol}", fetch))
    return jobs
