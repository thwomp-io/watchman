# Architecture

How the code is laid out and why, for someone opening the repository for the first time.

## The shape in one picture

```
 plain files you own            the engine (Python, `hn`)                 what you look at
┌────────────────────┐   ┌──────────────────────────────────────┐   ┌──────────────────────────┐
│ corpus (TRACKER_   │   │ providers → service → CLI + MCP      │   │ web console (React)      │
│ PATH): portfolio,  │──▶│ one lane per domain:                 │──▶│ served by `hn bus serve  │
│ trips, role hunt,  │   │ finance · career · travel            │   │ --console`               │
│ or a sample pack   │   │ plus bus, console, pocket, packs     │   │ desktop shell (Tauri)    │
└────────────────────┘   └───────────────┬──────────────────────┘   │ phone (the same console) │
                                         │ events                   └────────────▲─────────────┘
                                   ┌─────▼──────┐                                │
                                   │ bus (SQLite)│────────────────────────────────┘
                                   └────────────┘
```

Three rules hold everywhere:

- **Deterministic core.** Thresholds, alerts and dashboards are plain commands. No model sits in a
  render loop or an alert path; an agent is a separate, opt-in consumer of the same commands.
- **Read-only by default.** A lane observes. Anything that changes state (a ledger write, a recorded
  submission) is a separate verb with its own gate, and nothing trades, sends or submits by itself.
- **The corpus is the source of truth.** The engine holds code and neutral templates. Your data
  lives outside the repository, and the bundled sample packs are fiction.

## A lane

`src/harness/<lane>/` has the same layers in each of the three lanes:

| Layer | Where | What it does |
|---|---|---|
| Providers | `providers/` | One module per outside source. Turns transport errors into `ProviderError`, paces its own requests, and documents the source's traps in its header |
| Corpus | `corpus/` | Reads the user's files into typed models. Extraction is anchored to headings and keys, never to prose |
| Service | `service.py` | The one surface both adapters call, so the CLI and the MCP tools cannot drift. Larger areas live in their own modules and the service indexes them |
| CLI | `cli/` | A Typer group, one module per noun. Thin: parse, call the service, render |
| MCP | `mcp_server.py` | The same verbs as tools (`finance_*`, `career_*`, `travel_*`), mounted on one root server |

The root CLI (`cli.py`) holds no logic. It maps group names to modules and imports a group when it is
first asked for, because most invocations are one command spawned by a widget or an agent.

## Failures

One contract, defined in `errors.py` and `_cli.py`:

- A verb raises `HarnessError(message, hint=…)`; it never prints and exits. `ProviderError` is the
  subtype for an outside source, `UsageError` for a command called wrong (exit 2).
- The command group prints `error:` and `hint:` on stderr and exits 1. Anything else is a bug: one
  line, exit 70, the traceback only under `HARNESS_DEBUG=1`.
- With `HARNESS_ERROR_FORMAT=json` the same report is one JSON line. The console sets it, so a failed
  widget shows the kind of failure, a sentence, the hint and a retry instead of a wall of stderr.

## The bus

`bus/` is the only path by which software reaches a person.

- One SQLite file; `BusService` owns dedup and idempotency (`store.py`, `service.py`).
- Standing agents publish events. `alert` and `warn` push to devices; `info` is a stream to skim.
- `bus/server.py` serves the same service over HTTP: localhost by default, every route but `/health`
  behind a bearer token, one connection per request. It takes extra routes, which is how the console
  shares its port and token without the bus importing the console.

## The console

One backend serves every form factor.

- **The door** (`console/api.py`): `POST /api/invoke/{cmd}`. A widget names a command; the door runs
  the `hn` verb as a subprocess and returns its JSON. Writes are an allowlist set per serve.
- **Dashboards** are JSON files: a packaged default seeds a lane's file when it is absent, and the
  console discovers lanes from what is on disk. A widget asks for exactly the field it shows.
- **Contracts are generated.** Models the console reads are declared in `console/contracts.py`;
  the TypeScript types in `bus-app/src/generated/` are built from them, and both test suites fail
  when the two drift.
- **The web app** (`bus-app/src/`) is React and Vite. **The desktop shell** (`bus-app/src-tauri/`) is
  a thin Tauri wrapper: it starts a loopback serve, shows the same console, and keeps only what a
  browser cannot do (tray, native notifications, autostart, updates).

## The pocket agent

`pocket/` answers prompts queued from the console's CHAT tab with an agent running on your own
machine. The runner opens no port, runs the agent read-only inside an OS sandbox, tests that seal on
every start, and serves nothing while a check fails. `docs/POCKET.md` has the threat model.

## Packs and the sealed demo

- A **pack** is a portable bundle of corpus files for one persona (`samples/packs/`). `--pack` on
  any lane verb, or `WEIGHTS_PACK`, points the lanes at it.
- Every path the engine reads or writes resolves through `paths.py`, which honors a small set of
  environment variables (state, cache, config, bus database, corpus root). `hn console demo` sets
  all of them to a throwaway directory, so the demo cannot fall back to real data on the machine.

## State

| What | Where | Notes |
|---|---|---|
| Your data | the corpus directory | Plain files under version control |
| History | one SQLite file in the state directory | Typed rows (`history_store.py`); each store exports JSON lines into the corpus after every write and rebuilds from that export |
| The bus | one SQLite file in the state directory | |
| Caches | JSON files in the cache directory | Losing one costs a refetch, never a failed run |

## Standing agents

A scheduled job is a deterministic verb with a `--json` contract. Side effects sit behind `--notify`;
each run appends to a log, dedups per incident or per day, degrades per source, and reaches a person
only through the bus. `autocommit.py` commits what a run wrote into the corpus: its own path only,
a fast-forward push, never a pull or a rebase.

## Diagrams

`viz/` is a small Node and D3 renderer driven from Python (`viz.py`). Each diagram type renders to a
static SVG and has an interactive twin in the console.

## Tests and gates

- Python: `pytest` on fixtures with no network, `ruff`, `mypy --strict`.
- Console: `vitest` for components and models, and Playwright end-to-end against a sealed serve of
  the sample packs (every lane: no error walls, no stuck widgets, no clipped tables).
- Shell: `cargo clippy` with warnings denied, and `cargo test`.
- Releases: the suites, then a vulnerability scan over the Python, npm and Cargo lockfiles.
- This repository ships the suites for the shared core, the console, the bus server, the packs, and
  the smaller lanes. The finance, career and travel suites run against captured provider responses
  and are not published; fictional equivalents are planned.
