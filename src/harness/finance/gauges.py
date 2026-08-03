"""Options-positioning gauges (`hn finance gauges`) — the "who is on the other side?" read.

Put/call ratio (session volume; open interest when the trading API is reachable), IV30 (median
near-the-money implied vol, 20-45 days out), HV30 (trailing realized vol from the daily tape), and
the IV−HV spread with a one-word characterization (braced / neutral / complacent). The broker-card
gauges (the numbers a broker research card shows — P/C, IV30, HV30) as a first-class deterministic
verb instead of a screenshot read.

Determinism doctrine (same as `market.build_overview` / `correlate.build_correlation`):
`build_gauges` is a PURE function over a snapshots dict + closes — no network here (the service
gathers) — so the math is unit-testable with fixtures. The gauges are SENTIMENT THERMOMETERS, never
advice: heavy put volume can be fear or longs buying insurance (the tape can't distinguish), so the
interpretation stays the agent's narrative. Honest boundaries ride in the output notes: indicative
(free) feed, IV coverage on illiquid strikes, and short interest — NOT available via Alpaca; the
broker card / FINRA is the source for short % / days-to-cover.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date

from harness.finance.models import OptionGauges

_TRADING_DAYS = 252
_IV_DTE_MIN, _IV_DTE_MAX = 20, 45  # the "30-day" expiry window (days to expiry)
_IV_STRIKE_BAND = 0.10  # near-the-money = strike within ±10% of spot
_IV_LOW_CONFIDENCE_N = 4  # fewer contracts than this → the median is marked low-confidence
_HV_CLOSES = 31  # 30 trading-day window (broker-card convention) → 30 daily log returns
_SPREAD_THRESHOLD = 8.0  # |IV30 − HV30| points beyond which the read leaves "neutral"


@dataclass(frozen=True)
class ParsedOption:
    """One OCC symbol, decomposed. Parsed from the RIGHT (roots vary in length)."""

    root: str
    expiry: date
    is_call: bool
    strike: float


def parse_occ_symbol(occ: str) -> ParsedOption | None:
    """OCC option symbol → parts, or None if malformed (never a throw — a bad symbol is skipped).

    Layout, right-anchored because the root is variable-length: last 8 chars = strike × 1000,
    the char before = C/P, the 6 before that = YYMMDD, everything left = the root.
    e.g. XYZ270115C00100000 → root XYZ, 2027-01-15, call, $100.00.
    """
    if len(occ) < 16:  # 1-char root minimum: 1 + 6 + 1 + 8
        return None
    strike_part, cp, date_part, root = occ[-8:], occ[-9], occ[-15:-9], occ[:-15]
    if not (root and strike_part.isdigit() and date_part.isdigit() and cp in "CP"):
        return None
    try:
        expiry = date(2000 + int(date_part[:2]), int(date_part[2:4]), int(date_part[4:6]))
    except ValueError:
        return None
    return ParsedOption(root=root, expiry=expiry, is_call=(cp == "C"), strike=int(strike_part) / 1000.0)


def hv30(closes: list[float]) -> tuple[float | None, int]:
    """Trailing realized vol: last 31 closes → 30 daily log returns → sample std ×√252, in %.

    Returns (hv %, n_returns); (None, 0) when the history is too thin to fill the window (honest,
    never a partial-window number that silently means something else). Window + sample-std chosen
    for BROKER-CARD PARITY (the common brokerage HV30 convention) so the gauge reads against a
    broker research card without a methodology gap.
    """
    cs = [c for c in closes if c > 0][-_HV_CLOSES:]
    if len(cs) < _HV_CLOSES:
        return None, 0
    rets = [math.log(cs[i] / cs[i - 1]) for i in range(1, len(cs))]
    m = sum(rets) / len(rets)
    std = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    return round(std * math.sqrt(_TRADING_DAYS) * 100, 2), len(rets)


def _spread_read(spread: float) -> str:
    """IV30 − HV30 → the one-word gauge label (a characterization, not advice): options pricing
    notably MORE movement than the tape delivered = braced; notably less = complacent."""
    if spread >= _SPREAD_THRESHOLD:
        return "braced"
    if spread <= -_SPREAD_THRESHOLD:
        return "complacent"
    return "neutral"


def _ratio(puts: int, calls: int) -> float | None:
    return round(puts / calls, 2) if puts and calls else None


def build_gauges(
    symbol: str,
    snapshots: dict[str, object],
    *,
    spot: float | None,
    closes: list[float],
    open_interest: dict[str, int] | None = None,
    oi_as_of: str | None = None,
    oi_error: str = "",
    today: date | None = None,
) -> OptionGauges:
    """Pure: option-chain snapshots + spot + daily closes (+ optional OI roster) → the gauges.

    `snapshots` is the raw {OCC symbol: snapshot} dict from the indicative feed; unparseable
    symbols are skipped (counted in the notes, never a crash). `open_interest` is optional by
    design — the trading-API roster can be unreachable, in which case the put/call ratio is
    volume-only and says so (`oi_error` carries the reason into the notes).
    """
    today = today or date.today()
    sym = symbol.upper()
    n_calls = n_puts = call_vol = put_vol = 0
    call_oi = put_oi = 0
    iv_reported = 0
    ivs: list[float] = []
    unparseable = 0
    session: str | None = None

    for occ, snap in snapshots.items():
        parsed = parse_occ_symbol(occ)
        if parsed is None:
            unparseable += 1
            continue
        s = snap if isinstance(snap, dict) else {}
        daily = s.get("dailyBar") or {}
        vol = int(daily.get("v") or 0)
        bar_day = str(daily.get("t") or "")[:10]
        if bar_day and (session is None or bar_day > session):
            session = bar_day
        if parsed.is_call:
            n_calls, call_vol = n_calls + 1, call_vol + vol
        else:
            n_puts, put_vol = n_puts + 1, put_vol + vol
        if open_interest is not None and occ in open_interest:
            if parsed.is_call:
                call_oi += open_interest[occ]
            else:
                put_oi += open_interest[occ]
        iv = s.get("impliedVolatility")
        if isinstance(iv, int | float) and iv > 0:
            iv_reported += 1
            dte = (parsed.expiry - today).days
            near = spot is not None and abs(parsed.strike - spot) <= _IV_STRIKE_BAND * spot
            if _IV_DTE_MIN <= dte <= _IV_DTE_MAX and near:
                ivs.append(float(iv))

    iv30 = round(statistics.median(ivs) * 100, 2) if ivs else None
    hv, hv_n = hv30(closes)
    spread = round(iv30 - hv, 2) if (iv30 is not None and hv is not None) else None
    oi_ok = open_interest is not None

    notes = [
        "feed: indicative (free) — indicative quotes, not the OPRA consolidated tape",
        f"IV reported on {iv_reported}/{n_calls + n_puts} contracts (illiquid strikes come back IV-less)",
    ]
    if not snapshots:
        notes.insert(0, "no option chain returned — symbol may have no listed options")
    if iv30 is None and snapshots:
        notes.append(
            f"IV30 unavailable — no IV-bearing contracts {_IV_DTE_MIN}-{_IV_DTE_MAX}d out within "
            f"±{_IV_STRIKE_BAND:.0%} of spot"
        )
    elif len(ivs) < _IV_LOW_CONFIDENCE_N:
        notes.append(f"IV30 is LOW-CONFIDENCE — only {len(ivs)} contract(s) in the window")
    if hv is None:
        notes.append(f"HV30 unavailable — fewer than {_HV_CLOSES} daily closes of history")
    if snapshots and _ratio(put_vol, call_vol) is None:
        notes.append("put/call (volume) unavailable — a zero-volume side in the session")
    if oi_ok:
        notes.append(
            f"open interest as of {oi_as_of or 'unknown'} (exchange-reported, lags the tape ~1 day)"
        )
    else:
        notes.append(
            "open interest unreachable — put/call is session-volume-only this run"
            + (f" ({oi_error})" if oi_error else "")
        )
    if unparseable:
        notes.append(f"{unparseable} unparseable OCC symbol(s) skipped")
    notes.append(
        "short interest / days-to-cover NOT available via Alpaca — the broker card / FINRA is the source"
    )

    return OptionGauges(
        symbol=sym,
        spot=spot,
        chain_session=session,
        n_contracts=n_calls + n_puts,
        n_calls=n_calls,
        n_puts=n_puts,
        call_volume=call_vol,
        put_volume=put_vol,
        pc_ratio_volume=_ratio(put_vol, call_vol),
        call_oi=call_oi if oi_ok else None,
        put_oi=put_oi if oi_ok else None,
        pc_ratio_oi=_ratio(put_oi, call_oi) if oi_ok else None,
        oi_available=oi_ok,
        oi_as_of=oi_as_of if oi_ok else None,
        iv30=iv30,
        iv30_n=len(ivs),
        iv30_low_confidence=bool(ivs) and len(ivs) < _IV_LOW_CONFIDENCE_N,
        iv_reported=iv_reported,
        hv30=hv,
        hv30_returns=hv_n,
        iv_hv_spread=spread,
        spread_read=_spread_read(spread) if spread is not None else None,
        notes=notes,
    )
