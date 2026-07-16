# Your harness corpus

This directory is **your corpus** — the personal context the harness reads to make everything it does
bespoke to *you*. It was scaffolded by `hn init`; the dirs + template files below are the structure the
toolkit and your agent expect. **Fill them in over time** (by hand, or — better — by letting your agent
build them from normal conversation; see the `corpus-operator` skill).

The console (the Watchman app) reads this corpus when no weight pack is loaded. Point the toolkit at a
different corpus with the `TRACKER_PATH` env var; load a portable *scenario* with a weight pack
(`hn packs list`, or ⚙ Settings → Weight packs in the app).

## Layout

| Path | What it is | Who fills it |
|---|---|---|
| `user_background.md` | Who you are — the root context everything draws on | you / your agent |
| `narratives/` | The longform corpus — your stories, decisions, the *why* (the voice is the moat) | your agent, over time |
| `finance/config/portfolio.yaml` | Your holdings + watchlist + thresholds (the finance "weights") | you |
| `finance/` | Net-worth history, research, market takes, execution tickets | you + agent |
| `role-hunt/watchlist.yml` | Companies to scan + your role filters (the career "weights") | you |
| `role-hunt/` | Applications pipeline, scan results, per-company profiles | you + agent |
| `travel/config/weights.yaml` | Home base + travel preferences (the travel "weights") | you |
| `travel/` | Trips, hosting visits, the reference almanac | you + agent |
| `reports/sessions/` | Session chronicles your agent writes | your agent |

Each subdir has its own README with specifics. Nothing here is required all at once — the toolkit reads
what's present and degrades gracefully on what isn't. **Start with the three `config`/weights files and
`user_background.md`; let the rest accrete.**
