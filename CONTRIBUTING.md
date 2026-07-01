# Contributing

Thanks for looking. Watchman is a personal agentic harness developed in the open — it iterates in public,
so not every feature is finished, and the design is opinionated (see the thesis in the [README](README.md)).
Issues, ideas, and focused PRs are welcome.

## Ground rules (the ones that matter)

- **Never commit real personal data.** Only the bundled **fictional** sample personas
  (`samples/packs/`) ship in the repo. Real corpora live outside it (config-pointed). A PR that adds real
  holdings, locations, or identities won't be merged.
- **Stay read-rich / execute-gated.** Don't add transactional execution (trading, booking, account writes)
  to the read-only surface. Observation, research, and recommendation only.
- **Keep the core deterministic.** No model call belongs in a dashboard render loop or an alert/threshold
  loop. Detection and rendering are deterministic; an agent designs and narrates out-of-band.
- **Outbound stays non-PII.** External calls carry a tool-only `User-Agent` — no name/email.
- **The corpus prose is canonical for rationale; the yaml is canonical for numbers.** Keep machine weights
  in manual sync with the narrative; never parse prose for figures.

## Dev setup

```bash
uv sync # Python env + deps
cd viz && npm install && cd .. # the D3 SVG renderer (needs Node)
cd bus-app && npm install # the desktop console (needs Rust + platform build tools)
```

Run a lane against a bundled persona to sanity-check:

```bash
uv run hn finance networth --pack samples/packs/demo-investor
```

## Gates (green before a PR)

Python (from the repo root):

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

The desktop console (`bus-app/`):

```bash
npm run build # tsc typecheck + vite production build
npx vitest run # the frontend test suite
```

Behavior changes need a test. Mirror the existing style — small, focused, and assert the behavior, not the
implementation. (The pack-swap fixes, for example, ship with regression tests proven to go red without the
fix.)

## Extending it

- **A new lane** mounts in ~one line — a Typer noun-group + a FastMCP tool set over the shared core
  (`_http` / `settings` / `corpus` / `viz`); see `src/harness/cli.py`.
- **A new diagram type** is added by registering a renderer in `viz/render.js`'s `RENDERERS` map and adding
  its name to `KNOWN_TYPES` in `src/harness/viz.py`. Public/GitHub-facing diagrams use the **`noir`** theme
  (the black, high-contrast house style). Render → rasterize → eyeball the SVG before shipping it.
- **A new dashboard widget / pack** — the console reads dashboards + widgets from config and resolves every
  source from the active weight pack; a pack is a complete persona across the lanes it covers.

## Commit / PR style

Keep commits focused and messages descriptive (what changed and why). One logical change per PR where you
can. Don't bundle unrelated reformatting.
