"""Runtime settings: Alpaca keys from env, tracker corpus path, packaged portfolio-seed path.

`tracker_path` + the blank-falls-back behavior live in `harness.settings.BaseToolkitSettings`
(shared across lanes); this module adds the finance-specific keys + corpus paths.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from harness.settings import BaseToolkitSettings

# Packaged machine-readable portfolio seed lives next to this module (the weights.yaml analog).
PORTFOLIO_PATH = Path(__file__).parent / "portfolio.yaml"

# SEC's canonical ticker→CIK map (company_tickers.json), bundled as reference data. SEC serves it
# only from www.sec.gov, which WAF-blocks non-browser clients — so it's fetched once via browser and
# committed here (refresh = re-download + replace). The data.sec.gov XBRL APIs we hit at runtime are
# open with our normal UA; only this static phonebook needs bundling. See edgar_provider.
COMPANY_TICKERS_PATH = Path(__file__).parent / "company_tickers.json"

__all__ = ["COMPANY_TICKERS_PATH", "PORTFOLIO_PATH", "Settings", "get_settings"]


class Settings(BaseToolkitSettings):
    """Environment-driven config. Alpaca keys are optional (absent -> live calls raise clearly)."""

    alpaca_api_key_id: str | None = None
    alpaca_api_secret_key: str | None = None
    # Financial Modeling Prep — clean PRE-COMPUTED TTM valuation ratios (sidesteps the EDGAR
    # Q4-in-10-K TTM-assembly trap). Free tier (250 req/day). Optional: absent →
    # `multiples` uses the keyless EDGAR path; present → it prefers FMP (auto). Set FMP_API_KEY.
    fmp_api_key: str | None = None
    # SEC fair-access contact for www.sec.gov Archives fetches (N-PORT + the filing reader).
    # Resolving it HERE (not bare os.environ) is the fix for a latent papercut: the var's documented
    # home is the gitignored .env, which pydantic-settings loads into Settings fields — a direct
    # os.environ read only ever saw a shell-exported value. Env var still overrides the file
    # (pydantic-settings precedence), so both setups work. Sent ONLY to www.sec.gov, never shipped.
    harness_sec_contact: str = ""

    @property
    def portfolio_path(self) -> Path:
        """The holdings seed, resolved in precedence order:
        1. the active weight pack's `finance/portfolio.yaml` (when a pack is loaded);
        2. a TRACKER-RESIDENT `finance/config/portfolio.yaml` if the user scaffolded one (so finance is
           corpus-resident + TRACKER_PATH-sealed like career/travel — the `hn init` scaffold writes it);
        3. the packaged default (back-compat: an existing install with no tracker-resident file is
           unchanged).
        Additive + non-breaking: a tracker file only takes effect if it exists."""
        if pack := self.pack_file("finance", "portfolio.yaml"):
            return pack
        tracker_resident = self.tracker_path / "finance" / "config" / "portfolio.yaml"
        if tracker_resident.is_file():
            return tracker_resident
        return PORTFOLIO_PATH

    @property
    def feeds_path(self) -> Path:
        """The news-wire roster (feeds.yaml), resolved with the same precedence as the portfolio
        seed: pack > tracker-resident > packaged. The packaged default carries only the generic
        broad-market wire; a user's tuned roster (geopolitics picks, thesis topics) is corpus
        data and lives tracker-resident — media taste is a personal tell, same class as holdings."""
        if pack := self.pack_file("finance", "feeds.yaml"):
            return pack
        tracker_resident = self.tracker_path / "finance" / "config" / "feeds.yaml"
        if tracker_resident.is_file():
            return tracker_resident
        return Path(__file__).parent / "feeds.yaml"

    @property
    def finance_corpus_file(self) -> Path:
        """The human source-of-truth prose (portfolio.yaml is kept in manual sync with this)."""
        return self.tracker_path / "narratives" / "finance.md"

    @property
    def has_alpaca_keys(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)

    @property
    def has_fmp_key(self) -> bool:
        return bool(self.fmp_api_key)

    @property
    def scorecards_path(self) -> Path | None:
        """The print-scorecard registry (`finance/reference/print-scorecards.yaml`), pack-first
        then tracker-resident. Appended by the operating agent at grade time; the PRINTS
        scorecard-book tab renders from it via `hn finance scorecards --json`. None if absent —
        the verb degrades to an honest empty state, never an invented card."""
        pack = self.pack_file("finance", "print-scorecards.yaml")
        if pack and pack.is_file():
            return pack
        tr = self.tracker_path / "finance" / "reference" / "print-scorecards.yaml"
        return tr if tr.is_file() else None

    @property
    def projections_path(self) -> Path | None:
        """The scenario-projection params registry (`finance/reference/projection-params.yaml`),
        pack-first then tracker-resident. Authored by the operating agent at research/grade time
        (forward EPS + growth band + multiple band per name, with provenance); the PROJECTIONS
        tab renders from it via `hn finance projections --json`. None if absent — the verb
        degrades to an honest empty state, never an invented grid."""
        pack = self.pack_file("finance", "projection-params.yaml")
        if pack and pack.is_file():
            return pack
        tr = self.tracker_path / "finance" / "reference" / "projection-params.yaml"
        return tr if tr.is_file() else None

    @property
    def quotes_path(self) -> Path | None:
        """Offline static-quote fixture (`finance/quotes.json`), resolved pack-first then
        tracker-resident. When present AND Alpaca keys are absent, the finance lane renders from it
        (the keyless clone-and-run path — see `static_provider`). None if no fixture exists."""
        pack = self.pack_file("finance", "quotes.json")
        if pack and pack.is_file():
            return pack
        tr = self.tracker_path / "finance" / "quotes.json"
        return tr if tr.is_file() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
