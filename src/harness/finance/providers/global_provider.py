"""Global markets quotes — futures, world indices, energy, metals, FX (keyless Yahoo).

The follow-the-sun surface: what the US tape can't show after its close. Yahoo's crumb-free
`/v8/finance/chart/{symbol}` meta covers index futures (ES=F), world indices (^N225), commodities
(BZ=F), and FX (EURUSD=X) — one polite self-paced call per card, no cookie/crumb handshake at all
(the v7 batch quote endpoint needs the crumb, and its getcrumb gate burst-throttles shared IPs).
Rails mirror the pre-market/Asia/Europe/oil/metals tab grammar of mainstream market sites, so an
overnight read (is the futures market panicking? did Asia confirm?) is one command instead of a
browser tour. Per-card resilience: one dead symbol never bricks a rail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from harness._http import get_with_retry
from harness.errors import ProviderError
from harness.finance.providers.futures_calendar import FUTURES_ROOTS, roll_check

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_PACE_SECONDS = 0.15  # self-pacing between per-symbol calls (good-citizen posture: pace, don't burst)

# Browser UA is endpoint-required (query1 429s tool UAs; zero PII) — but DISTINCT from the
# ratings provider's string on purpose: Yahoo buckets its burst throttle per UA+IP, so sharing one
# string lets the ratings wire's daily diff starve this surface (identical requests with a
# shared UA get 429s while a distinct one succeeds). One static UA per provider + self-pacing keeps each
# component inside its own budget — good-citizen posture, not rotation. Note:
# Stooq's light-quote CSV endpoint is retired (404) and every keyed free tier gates this
# coverage — v8 chart is the last keyless source standing for futures/world-indices/commodities.
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_HDRS = {"User-Agent": _BROWSER_UA}

# The rails — section → [(yahoo symbol, display name)]. Ordered as read: US-overnight first,
# then the sun (Asia → Europe), then the barrels/metals/FX that price every session.
GLOBAL_RAILS: dict[str, list[tuple[str, str]]] = {
    "futures": [
        ("ES=F", "S&P 500 fut"),
        ("NQ=F", "Nasdaq 100 fut"),
        ("YM=F", "Dow fut"),
        ("RTY=F", "Russell 2000 fut"),
        ("^TNX", "US 10-yr yield"),
        ("^VIX", "VIX (spot)"),  # vol regime rides the US-risk rail with the 10-yr (level, not a future)
        ("^SKEW", "SKEW (tail)"),  # tail-hedge demand: what the options market pays for crash cover
    ],
    # The rates rail — the live tenors Yahoo publishes (3m/5y/10y/30y indices) plus the 2-year
    # NOTE FUTURES as a PRICE (Yahoo has no reliable live 2-year yield index; the cash 2-year
    # LEVEL comes from the Treasury curve — see treasury_provider). ZT=F's day% is the intraday
    # 2-year DIRECTION tell: a falling note price is a rising yield (~duration 1.9y, so −0.2% on
    # the price ≈ +10bp on the yield). The 10-year is deliberately listed on both this rail and
    # the US-risk rail (fetched once) — a curve reads as a curve.
    "rates": [
        ("^IRX", "US 3-mo bill"),
        ("ZT=F", "2-yr note fut (px)"),
        ("^FVX", "US 5-yr yield"),
        ("^TNX", "US 10-yr yield"),
        ("^TYX", "US 30-yr yield"),
    ],
    "asia": [
        ("^N225", "Nikkei 225"),
        ("^HSI", "Hang Seng"),
        ("000001.SS", "Shanghai Comp"),
        ("^TWII", "Taiwan TAIEX"),
        ("^KS11", "KOSPI"),
        ("^AXJO", "ASX 200"),
        ("^NSEI", "Nifty 50"),
    ],
    "europe": [
        ("^FTSE", "FTSE 100"),
        ("^GDAXI", "DAX"),
        ("^FCHI", "CAC 40"),
        ("^STOXX50E", "Euro Stoxx 50"),
    ],
    "oil": [
        ("CL=F", "WTI crude"),
        ("BZ=F", "Brent crude"),
        ("NG=F", "Nat gas"),
        ("RB=F", "RBOB gasoline"),
        ("HO=F", "ULSD diesel"),
    ],
    "metals": [
        ("GC=F", "Gold"),
        ("SI=F", "Silver"),
        ("HG=F", "Copper"),
        ("PL=F", "Platinum"),
    ],
    "fx": [
        ("DX-Y.NYB", "Dollar index"),
        ("EURUSD=X", "EUR/USD"),
        ("USDJPY=X", "USD/JPY"),
        ("USDCNY=X", "USD/CNY"),
        ("BTC-USD", "Bitcoin"),  # the risk-appetite currency — rides the FX rail as a currency, not a stock
    ],
}


# Flat scalar keys for dashboard stat tiles (value_path "scalars.<key>") — the same
# tile-first pattern as market's breadth scalars: tiles want one number, not an array walk.
SCALAR_KEYS: dict[str, str] = {
    "ES=F": "es_pct", "NQ=F": "nq_pct", "YM=F": "ym_pct", "RTY=F": "rty_pct",
    "^TNX": "us10y",  # level, not pct — the tile shows the yield itself
    "^VIX": "vix",  # level, not pct — regime reads care where vol SITS (16 vs 28), not its day%
    "^SKEW": "skew",  # level — where tail-hedge demand sits (~115 calm … 150+ = crash cover bid)
    "^IRX": "us3m", "^FVX": "us5y", "^TYX": "us30y",  # levels — the live curve tenors
    "ZT=F": "zt_pct",  # the 2-yr note future's day% (price) — the intraday 2-yr direction tell
    "^N225": "nikkei_pct", "^HSI": "hsi_pct", "000001.SS": "shanghai_pct",
    "^TWII": "taiex_pct", "^KS11": "kospi_pct", "^AXJO": "asx_pct", "^NSEI": "nifty_pct",
    "^FTSE": "ftse_pct", "^GDAXI": "dax_pct", "^FCHI": "cac_pct", "^STOXX50E": "stoxx_pct",
    "CL=F": "wti_pct", "BZ=F": "brent_pct", "NG=F": "natgas_pct", "RB=F": "rbob_pct", "HO=F": "ulsd_pct",
    "GC=F": "gold_pct", "SI=F": "silver_pct", "HG=F": "copper_pct", "PL=F": "platinum_pct",
    "DX-Y.NYB": "dxy_pct", "EURUSD=X": "eurusd_pct", "USDJPY=X": "usdjpy_pct",
    "USDCNY=X": "usdcny_pct", "BTC-USD": "btc_pct",
}
# Scalars that carry the LEVEL rather than the day% — yields + vol/tail indices, where the
# regime datapoint is where the number SITS.
LEVEL_KEYS: frozenset[str] = frozenset({"us10y", "vix", "skew", "us3m", "us5y", "us30y"})


def rail_scalars(rails: dict[str, list[GlobalQuote]]) -> dict[str, float]:
    """Tile-ready flat scalars from fetched rails (pct for most; LEVEL for yields + VIX/SKEW)."""
    out: dict[str, float] = {}
    for cards in rails.values():
        for q in cards:
            key = SCALAR_KEYS.get(q.symbol)
            if key is None:
                continue
            val = q.price if key in LEVEL_KEYS else q.change_pct
            if val is not None:
                out[key] = round(float(val), 2)
    return out


def rail_sessions(rails: dict[str, list[GlobalQuote]]) -> dict[str, str]:
    """Tile-ready session labels keyed like the scalars (`sessions.taiex_pct` → "live" or
    "Fri 09-18 close") — a stat tile's `title_path` shows which session its number measures."""
    out: dict[str, str] = {}
    for cards in rails.values():
        for q in cards:
            key = SCALAR_KEYS.get(q.symbol)
            if key is not None and q.session_label:
                out[key] = q.session_label
    return out


# Round-the-clock instruments legitimately print on a weekend; an exchange index does not.
_WEEKEND_OK = ("BTC-USD",)


def off_session_notes(rails: dict[str, list[GlobalQuote]]) -> list[str]:
    """A print stamped on a Saturday/Sunday for an exchange-traded row is a feed artifact (a closed
    Taipei 'moved' +3.31% on a Sunday): say so instead of letting it read as a move."""
    notes: list[str] = []
    seen: set[str] = set()
    for cards in rails.values():
        for q in cards:
            if q.symbol in seen or q.symbol in _WEEKEND_OK or q.symbol.endswith("=X"):
                continue
            seen.add(q.symbol)
            if q.session_note:
                notes.append(q.session_note)
                continue
            if q.session_date and date.fromisoformat(q.session_date).weekday() >= 5 and not q.live:
                notes.append(
                    f"{q.symbol}: print stamped {q.session_label.replace(' close', '')} — a day the venue "
                    f"does not trade; its day % is not a session move (read the last weekday close)"
                )
    return notes


@dataclass
class GlobalQuote:
    """One rail card: last price + day change vs previous close, with venue state/time.

    Futures rows also name the DATED contract they quote (see futures_calendar): `symbol` stays
    the rail's continuous key (`BZ=F` — scalars and dashboards key on it), `contract` is what was
    actually fetched (`BZX26.NYM`), `contract_month` is the printed month ('Nov 26'), and on a roll
    day `rolled_from` names the month the rule left. `contract_month` None on a future = the dated
    symbol was unavailable and the row fell back to Yahoo's continuous alias — the notes say so.
    """

    symbol: str
    name: str
    price: float | None
    change: float | None
    change_pct: float | None
    market_state: str  # Yahoo's venue state (REGULAR/CLOSED/PRE/POST…)
    market_time: int | None  # epoch seconds of the quote, venue-local meaning
    contract: str | None = None
    contract_month: str | None = None
    roll_day: bool = False
    rolled_from: str | None = None
    # Session labeling: a closed market's row keeps showing its LAST session's move, and a bare
    # time ("04:01Z") cannot say which day that was — a Friday close read as a live Sunday move.
    session_date: str | None = None  # the quote's date in the EXCHANGE's timezone (ISO)
    live: bool = False  # now inside Yahoo's current regular trading period for the venue
    session_label: str = ""  # "live" | "Fri 09-18 close" — what the day % measures
    session_note: str = ""  # a venue-clock caveat (open but no print yet) — rides the notes



def _quote_one(client: httpx.Client, symbol: str) -> GlobalQuote | None:
    """One v8 chart-meta quote. None on any per-symbol failure — the rail omits the card."""
    venue = VENUES.get(symbol)
    try:
        resp = get_with_retry(
            _CHART_URL.format(symbol=symbol), client=client, headers=_HDRS,
            params={"interval": "1d", "range": "1d"}, retries=2,
        )
        result = resp.json()["chart"]["result"][0]
        meta = result["meta"]
    except Exception:  # noqa: BLE001 — per-card resilience is the contract
        return None
    price = meta.get("regularMarketPrice")
    if venue:
        history = None
        if usable_prev(price, meta.get("previousClose") or meta.get("chartPreviousClose")) is None:
            history = _history_bars(client, symbol)  # one extra call, only on a broken prior close
        return label_venue(symbol, meta, _daily_bars(result), now_epoch=time.time(), history=history)
    # At range=1d Yahoo now omits previousClose; chartPreviousClose is then the prior bar's close.
    # For a DATED contract that is its own prior settle (verified live against the 5d bars); for
    # a continuous `=F` alias it can be the previous month's close — why futures never quote the
    # alias when a dated contract resolves.
    raw_prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    prev = usable_prev(price, raw_prev)
    change = change_pct = None
    if price is not None and prev:
        change = price - prev
        change_pct = (price / prev - 1.0) * 100.0
    note = "" if prev or raw_prev is None else _no_prior_note(symbol, raw_prev)
    q = GlobalQuote(
        symbol=symbol,
        name=meta.get("shortName") or symbol,
        price=price,
        change=change,
        change_pct=change_pct,
        market_state=meta.get("marketState") or "",
        market_time=meta.get("regularMarketTime"),
        session_note=note,
    )
    return label_session(q, meta, now_epoch=time.time())


# No index or future moves 2x in a session; a prior close outside this band is a feed artifact.
_PREV_BAND = (0.5, 2.0)


def usable_prev(price: float | None, prev: float | None) -> float | None:
    """The previous close if it can measure a day move, else None. After a long exchange holiday
    Yahoo's range=1d chartPreviousClose can be a near-zero placeholder (Shanghai's first sessions
    after the October holiday read 0.0002) — a Day % of +1.8 billion percent.
    Zero, negative, or a ratio outside the band is rejected rather than rendered."""
    if prev is None or prev <= 0:
        return None
    if price is None:
        return prev
    lo, hi = _PREV_BAND
    return prev if lo <= price / prev <= hi else None


def _no_prior_note(symbol: str, raw_prev: float) -> str:
    return (f"{symbol}: the feed's previous close ({raw_prev:g}) is unusable and no earlier close was "
            f"found — the day move is n/a, not a number")


def _history_bars(client: httpx.Client, symbol: str) -> list[tuple[int, float | None]]:
    """The last few daily bars (range=5d) — where the real prior close lives when the 1d meta's is
    broken. The 5d chart's own chartPreviousClose is not the prior SESSION (it is the close before
    the window), so the caller reads the bars, never that field. Empty on any failure."""
    try:
        resp = get_with_retry(
            _CHART_URL.format(symbol=symbol), client=client, headers=_HDRS,
            params={"interval": "1d", "range": "5d"}, retries=2,
        )
        return _daily_bars(resp.json()["chart"]["result"][0])
    except Exception:  # noqa: BLE001 — the fallback degrades to n/a, never a dead card
        return []


def _close_before(
    history: list[tuple[int, float | None]], session: date, tz: ZoneInfo,
) -> float | None:
    """The last real (non-None) close from a bar dated before `session` — across a holiday gap,
    the last session the venue actually traded."""
    prior = [c for t, c in history if c is not None and datetime.fromtimestamp(t, tz).date() < session]
    return prior[-1] if prior else None


# Cash-index venues: exchange timezone + regular hours (local; a lunch break counts as in session).
# The session of a cash index is decided HERE plus the daily bars, not from the chart meta alone:
# in the minutes around an open Yahoo's meta disagrees with itself: the trading period can still
# hold yesterday's bounds while a new print arrives (a live row reads CLOSED), and the print time
# can roll before the price does (yesterday's close wears today's label).
# Exchange holidays need no table: a holiday has no bar, so the row keeps the last real session.
VENUES: dict[str, tuple[str, str, str]] = {
    "^N225": ("Asia/Tokyo", "09:00", "15:30"),
    "^HSI": ("Asia/Hong_Kong", "09:30", "16:10"),
    "000001.SS": ("Asia/Shanghai", "09:30", "15:00"),
    "^TWII": ("Asia/Taipei", "09:00", "13:30"),
    "^KS11": ("Asia/Seoul", "09:00", "15:30"),
    "^AXJO": ("Australia/Sydney", "10:00", "16:00"),
    "^NSEI": ("Asia/Kolkata", "09:15", "15:30"),
    "^FTSE": ("Europe/London", "08:00", "16:30"),
    "^GDAXI": ("Europe/Berlin", "09:00", "17:30"),
    "^FCHI": ("Europe/Paris", "09:00", "17:30"),
    "^STOXX50E": ("Europe/Berlin", "09:00", "17:30"),
}


def _daily_bars(result: dict[str, Any]) -> list[tuple[int, float | None]]:
    """(timestamp, close) per daily bar; the close can be None (Yahoo leaves holes). Empty on any
    shape surprise."""
    try:
        ts = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return []
    return [(int(t), None if c is None else float(c)) for t, c in zip(ts, closes, strict=False)]


def label_venue(
    symbol: str, meta: dict[str, Any], bars: list[tuple[int, float | None]], *, now_epoch: float,
    history: list[tuple[int, float | None]] | None = None,
) -> GlobalQuote:
    """A cash-index row from its venue clock + the range=1d bar (pure; `now` injected).

    The 1d chart's one bar is the newest session Yahoo has; `chartPreviousClose` is the close
    before THAT bar (the 5d bars were tried and have holes — a day can be None on every venue, so a
    bar-derived previous close skipped a day). So:
    - LIVE only when the venue is in its regular hours today AND today has printed (today's bar, or
      a price stamped today that differs from the bar's close — a print newer than the bar).
    - Otherwise the row is the bar's session: its date, its close, its move vs chartPreviousClose.
      A value is never labeled with a newer day than the bar it came from.
    - In hours with no print yet (the first minutes, or an exchange holiday) the row says so.
    - A broken chartPreviousClose (see `usable_prev`) is replaced by the last real close before the
      session from `history` (the 5d bars); with none, the move is n/a and the row says so."""
    tz_name, open_s, close_s = VENUES[symbol]
    tz = ZoneInfo(tz_name)
    now = datetime.fromtimestamp(now_epoch, tz)
    today = now.date()
    in_hours = now.weekday() < 5 and open_s <= f"{now:%H:%M}" <= close_s
    price = meta.get("regularMarketPrice")
    mt = meta.get("regularMarketTime")
    raw_prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    chart_prev = usable_prev(price, raw_prev)
    q = GlobalQuote(symbol=symbol, name=meta.get("shortName") or symbol, price=price, change=None,
                    change_pct=None, market_state=meta.get("marketState") or "", market_time=mt)
    if not bars:  # no bar: the meta-only labeler
        if price is not None and chart_prev:
            q = replace(q, change=price - chart_prev, change_pct=(price / chart_prev - 1.0) * 100.0)
        elif raw_prev is not None:
            q = replace(q, session_note=_no_prior_note(symbol, raw_prev))
        return label_session(q, meta, now_epoch=now_epoch)
    bar_t, bar_close = bars[-1]
    bar_day = datetime.fromtimestamp(bar_t, tz).date()
    mt_day = datetime.fromtimestamp(mt, tz).date() if mt else None
    newer_print = (
        mt_day == today and bar_day < today and price is not None and bar_close is not None
        and abs(price - bar_close) > 1e-9 * abs(price)
    )
    if newer_print:  # today printed before Yahoo cut today's bar: measure vs the bar's close
        session, prev = today, bar_close
    else:
        session, prev = bar_day, chart_prev
        if bar_close is not None:
            price = bar_close if bar_day < today or not in_hours else price
        if mt_day != session:  # the stamp ran ahead of the value: stamp it at that session's close
            close_t = datetime.strptime(close_s, "%H:%M").time()
            mt = int(datetime.combine(session, close_t, tz).timestamp())
    note = ""
    if prev is None and raw_prev is not None:  # the meta's prior close was rejected
        prev = _close_before(history or [], session, tz)
        if prev is None:
            note = _no_prior_note(symbol, raw_prev)
    change = change_pct = None
    if price is not None and prev:
        change, change_pct = price - prev, (price / prev - 1.0) * 100.0
    live = in_hours and session == today
    if in_hours and session < today:
        note = (note + " · " if note else "") + (
            f"{symbol}: the venue is in session ({open_s}-{close_s} {tz_name}) but has not printed "
            f"today — the row is {session:%a %m-%d}'s close (first minutes, or a holiday)")
    return replace(
        q, price=price, change=change, change_pct=change_pct, market_time=mt,
        session_date=session.isoformat(), live=live,
        session_label="live" if live else f"{session:%a %m-%d} close", session_note=note,
    )


def label_session(q: GlobalQuote, meta: dict[str, Any], *, now_epoch: float) -> GlobalQuote:
    """Stamp the row with its session date + live flag from the chart meta (pure; `now` injected).

    Live = now inside the venue's current regular period AND the print is from inside it — Yahoo
    keeps serving the last period's bounds while a market is closed (a holiday Nikkei still shows
    Friday's 09:00-15:30), so a closed venue can never read as live."""
    tz_name = meta.get("exchangeTimezoneName") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — an unknown zone degrades to UTC, never a dead card
        tz = ZoneInfo("UTC")
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = period.get("start"), period.get("end")
    if q.market_time is None:
        return q
    when = datetime.fromtimestamp(q.market_time, tz)
    live = bool(start and end and start <= now_epoch <= end and q.market_time >= start)
    label = "live" if live else f"{when:%a %m-%d} close"
    return replace(q, session_date=when.date().isoformat(), live=live, session_label=label)


def fetch_global_quotes(symbols: list[str]) -> dict[str, GlobalQuote]:
    """Quote `symbols` via crumb-free v8 chart meta, self-paced. Failures are simply absent."""
    out: dict[str, GlobalQuote] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for i, sym in enumerate(symbols):
            if i:
                time.sleep(_PACE_SECONDS)
            q = _quote_one(client, sym)
            if q is not None:
                out[sym] = q
    if not out:
        raise ProviderError("yahoo global: every symbol failed (endpoint change or network)")
    return out


_ET = ZoneInfo("America/New_York")


def trade_date(now: datetime | None = None) -> date:
    """The futures TRADE date: Globex sessions open at 18:00 ET for the next day's date, so an
    evening read belongs to tomorrow's session (a Sunday-night read is Monday's)."""
    now = now or datetime.now(UTC)
    return (now.astimezone(_ET) + timedelta(hours=6)).date()


@dataclass
class RailsResult:
    """Rails plus the notes a reader must see (fallbacks, roll days, continuity warnings)."""

    rails: dict[str, list[GlobalQuote]]
    notes: list[str] = field(default_factory=list)


def resolve_rails(
    sections: list[str] | None = None, *, now: datetime | None = None,
) -> RailsResult:
    """The rails, resolved: one batch call, grouped + ordered per GLOBAL_RAILS.

    Every mapped future is quoted through its DATED contract for today's trade date (the
    futures_calendar rule), labeled with the month, and flagged on a roll day; a dated symbol
    Yahoo does not serve falls back to the continuous alias with a note. Unknown/unavailable
    symbols are omitted from their rail rather than failing the read — an overnight surface must
    degrade gracefully per-card, never brick on one delisted future.
    """
    wanted = {k: v for k, v in GLOBAL_RAILS.items() if sections is None or k in sections}
    td = trade_date(now)
    # de-duplicated, order-preserving: a symbol on two rails (the 10-yr) is fetched once
    flat = list(dict.fromkeys(sym for rail in wanted.values() for sym, _ in rail))
    resolved = {sym: roll_check(sym, td) for sym in flat if sym in FUTURES_ROOTS}
    dated = {sym: pair[0].symbol for sym, pair in resolved.items() if pair[0] is not None}
    quotes = fetch_global_quotes([dated.get(s, s) for s in flat])
    notes: list[str] = []
    # A dated contract Yahoo does not serve: quote the continuous alias instead (labeled as such)
    missing_dated = [s for s, d in dated.items() if d not in quotes]
    if missing_dated:
        try:
            quotes.update(fetch_global_quotes(missing_dated))
        except ProviderError:
            pass
    by_sym: dict[str, GlobalQuote] = {}
    for sym in flat:
        today, rolled = resolved.get(sym, (None, None))
        if today is not None and today.symbol in quotes:
            by_sym[sym] = replace(
                quotes[today.symbol], symbol=sym, contract=today.symbol,
                contract_month=today.label, roll_day=rolled is not None,
                rolled_from=rolled.label if rolled is not None else None,
            )
            if rolled is not None:
                notes.append(
                    f"{sym}: ROLL DAY — now {today.label} ({today.symbol}), was {rolled.label}; "
                    f"the day % is same-contract, the LEVEL steps by the calendar spread"
                )
        elif sym in quotes:
            alias = quotes[sym]
            by_sym[sym] = alias if today is None else replace(alias, contract=sym)
            if today is not None:
                notes.append(
                    f"{sym}: dated contract {today.symbol} unavailable — showing Yahoo's continuous "
                    f"alias (month unverified; its day % can straddle a roll)"
                )
    out: dict[str, list[GlobalQuote]] = {}
    for section, rail in wanted.items():
        cards = []
        for sym, display in rail:
            q = by_sym.get(sym)
            if q is not None:
                # display names are ours, not Yahoo's marketing strings; copy per rail so a shared
                # symbol carries each rail's own label. A future's month rides the name so every
                # table (CLI + the dashboard's name column) shows which contract it is reading.
                name = display
                if q.contract_month:
                    name = f"{display} · {q.contract_month}"
                    if q.rolled_from:
                        name += " (roll)"  # short: the month it left rides the note + JSON
                elif q.contract:
                    name = f"{display} · continuous"
                cards.append(replace(q, name=name))
        out[section] = cards
    notes.extend(off_session_notes(out))
    if "oil" in wanted:
        notes.extend(oil_continuity_notes(out["oil"], now=now))
    return RailsResult(out, notes)


def fetch_global_rails(sections: list[str] | None = None) -> dict[str, list[GlobalQuote]]:
    """The rails without the notes (kept for callers that only want the cards)."""
    return resolve_rails(sections).rails


# The continuity belt: each crude future vs the ETF that holds it. A roll artifact or a bad
# print shows up as a day-% gap the ETF does not share (a roll read as −5% while BNO
# closed −0.5%). ETFs only price in US hours, so the check runs only then — overnight their day %
# is the prior session and the comparison would be noise.
CONTINUITY_PAIRS: dict[str, str] = {"BZ=F": "BNO", "CL=F": "USO"}
CONTINUITY_GAP_PP = 2.0


def _us_session_open(now: datetime) -> bool:
    et = now.astimezone(_ET)
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 35 <= minutes <= 16 * 60  # 5 min in, so the ETF has a real print


def continuity_gaps(
    cards: list[GlobalQuote], etf_quotes: dict[str, GlobalQuote],
) -> list[str]:
    """Pure: warning lines for each future whose day % disagrees with its ETF by > the gap."""
    out: list[str] = []
    for q in cards:
        etf_sym = CONTINUITY_PAIRS.get(q.symbol)
        etf = etf_quotes.get(etf_sym) if etf_sym else None
        if etf is None or q.change_pct is None or etf.change_pct is None:
            continue
        gap = q.change_pct - etf.change_pct
        if abs(gap) > CONTINUITY_GAP_PP:
            out.append(
                f"CONTINUITY: {q.symbol} {q.contract_month or 'continuous'} day {q.change_pct:+.2f}% "
                f"vs {etf_sym} {etf.change_pct:+.2f}% ({gap:+.2f}pp) — suspect a roll or a bad "
                f"print; read the ETF for direction"
            )
    return out


def oil_continuity_notes(cards: list[GlobalQuote], *, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    if not _us_session_open(now):
        return []
    try:
        etfs = fetch_global_quotes(list(CONTINUITY_PAIRS.values()))
    except ProviderError:
        return ["continuity check skipped: BNO/USO unavailable"]
    return continuity_gaps(cards, etfs)
