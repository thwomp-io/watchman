"""Maintainer tool — generate `finance/quotes.json` fixtures for the sample packs.

The keyless clone-and-run path needs offline prices (see `finance/providers/static_provider.py`). This
writes a deterministic, plausible `quotes.json` for every bundled pack: the union of the pack's holdings,
its watchlist, and the whole-market basket (`hn finance market`'s indices/sectors/Mag7/semis) — so both
the Core finance tab AND the Market tab render with no Alpaca keys. Re-runnable; prices are FICTIONAL.

Run:  uv run python scripts/gen_demo_quotes.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from harness.finance.market import all_symbols as market_symbols

PACKS = Path(__file__).resolve().parents[1] / "samples" / "packs"

# Curated plausible last-prices for the common/market symbols (fictional but realistic-looking, so the
# demo reads credibly). Anything not here gets a deterministic per-symbol fallback (see _price).
PRICE_MAP: dict[str, float] = {
    # broad index / sector / semis ETFs (the `market` basket)
    "SPY": 601.4, "QQQ": 528.7, "DIA": 444.2, "IWM": 233.1, "RSP": 182.6,
    "XLK": 258.3, "XLC": 109.7, "XLY": 224.5, "XLP": 81.2, "XLE": 92.8, "XLF": 52.4,
    "XLV": 138.6, "XLI": 146.9, "XLB": 92.1, "XLRE": 42.7, "XLU": 81.5,
    "SMH": 287.4, "SOXX": 241.8,
    # mega-caps
    "AAPL": 229.8, "MSFT": 451.2, "GOOGL": 191.3, "AMZN": 222.6, "NVDA": 138.4,
    "META": 612.5, "TSLA": 347.9,
    # broad funds / common holdings
    "VTI": 296.7, "VXUS": 65.3, "BND": 72.4, "VOO": 552.1, "VT": 122.8, "VEA": 52.6,
    "VWO": 46.9, "SCHD": 27.8, "VIG": 198.4, "VYM": 132.5, "BNDX": 48.7, "VTEB": 50.1,
    "COST": 982.3, "BRK.B": 471.6, "JNJ": 152.4, "PG": 169.8, "KO": 62.7, "HD": 412.5,
    "V": 312.9, "JPM": 248.1, "UNH": 512.3, "WMT": 92.4, "CRM": 338.7,
    # concentrated single-stock positions used in the demo "unwind" stories
    "CRWD": 381.6, "ORCL": 192.4, "IBM": 274.8, "DDOG": 138.9,
}

# Per-symbol day-move spread (deterministic): prev_close = price / (1 + move), so day_change_pct ≈ move.
_MOVE_RANGE = 0.05  # ±2.5%


def _hash01(s: str) -> float:
    """A stable [0,1) from a symbol (deterministic across runs — no RNG)."""
    h = hashlib.sha256(s.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _price(sym: str) -> float:
    if sym in PRICE_MAP:
        return PRICE_MAP[sym]
    # fallback: a plausible $15–$400 price seeded from the symbol, so a new persona's holdings still quote
    return round(15 + _hash01(sym) * 385, 2)


def _quote(sym: str) -> dict[str, float]:
    price = _price(sym)
    move = (_hash01(sym + "·move") - 0.5) * _MOVE_RANGE  # ±2.5%, deterministic
    prev = round(price / (1 + move), 2)
    return {"price": price, "prev_close": prev}


def _pack_symbols(pack: Path) -> list[str]:
    """Holdings + watchlist (from portfolio.yaml) ∪ the whole-market basket, de-duplicated."""
    syms: dict[str, None] = {}
    pf = pack / "finance" / "portfolio.yaml"
    if pf.is_file():
        data = yaml.safe_load(pf.read_text()) or {}
        for h in data.get("holdings") or []:
            sym = str(h.get("symbol", "")).upper()
            # only share-priced positions need a quote; static balances (retirement/cash) are valued
            # from their `balance`, not a price, so they don't belong in the quotes fixture.
            if sym and h.get("shares") is not None:
                syms[sym] = None
        for w in data.get("watchlist") or []:
            sym = str(w.get("symbol", "")).upper()
            if sym:
                syms[sym] = None
    for sym in market_symbols():
        syms[sym.upper()] = None
    return list(syms)


def main() -> int:
    packs = sorted(p for p in PACKS.iterdir() if (p / "pack.yaml").is_file())
    for pack in packs:
        if not (pack / "finance").is_dir():
            continue
        quotes = {sym: _quote(sym) for sym in _pack_symbols(pack)}
        out = pack / "finance" / "quotes.json"
        out.write_text(json.dumps(quotes, indent=2) + "\n", encoding="utf-8")
        print(f"  {pack.name}/finance/quotes.json — {len(quotes)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
