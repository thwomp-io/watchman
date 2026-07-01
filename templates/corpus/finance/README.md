# finance/

Your finance corpus. The one file you maintain by hand is `config/portfolio.yaml` (holdings + watchlist
+ thresholds). The rest accretes as you + your agent work:

- `config/portfolio.yaml` — holdings/watchlist/thresholds (read-only valuation; the toolkit never trades).
- `networth-history.json` — appended by `hn finance networth --log`; the net-worth trend reads it.
- `research/` — per-symbol deep-dives (`hn finance research SYM` writes here).
- `market/takes/` — your agent's dated market reads (the dashboard's take browser).
- `execution/` — trade tickets (the dashboard's ticket browser). You execute; the toolkit never does.
- `plans/` — decision folder-notes (diversification, tax-loss-harvest, etc.).
- `reference/` — slow-changing facts (vesting calendars, a glossary).
