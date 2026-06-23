# The Harness Message Bus — producer contract (v1)

> The durable **human-event layer** between standing agents (OS-scheduled `hn` commands (cron/launchd/Task Scheduler)) and
> delivery surfaces (the Harness Bus tray app; future transports like self-hosted ntfy). Built to make
> standing-agent signals durable: transient native OS banners get missed, sleep coalesces OS-scheduled
> runs, and logs have no human surface.
> The bus fixes all three: durable unread state survives sleep; the app posts notifications under
> its own authorized identity; the inbox IS the human surface. **Zero model in the loop.**

## Storage

One SQLite database, WAL mode: **`~/.local/state/harness/bus.db`** — override with the
**`HARNESS_BUS_DB`** env var (tests point it at tmp; OSS users relocate freely). Both sides of the
contract resolve the path identically: Python (`harness.bus.store.default_db_path`) and the Rust
app. stdlib `sqlite3` / `rusqlite{bundled}`; `busy_timeout=5000` on every connection.

**Concurrency model**: producers INSERT; transports/UI UPDATE `read_at` / `delivered_via`. Writers
never contend on the same columns; WAL makes the rest a non-event.

## Events schema (v1)

| column | meaning |
|---|---|
| `id` | autoincrement PK |
| `created_at` | UTC ISO-8601, written at publish |
| `producer` | who published — `finance.pulse`, `career.watch`, `manual`, `agent` |
| `lane` | harness lane for filtering/UI grouping — `finance` / `career` / `travel` / `core` / open set |
| `kind` | event kind — open set (`day_move`, `trap_proximity`, `scan_delta`, …) |
| `subject` | filterable subject (ticker, company, …); may be empty |
| `title` / `body` | notification headline / detail line |
| `payload_json` | full structured context (JSON object) — the drill-down surface; an optional `ref` deep-links the bus-app (see below) |
| `severity` | `info` \| `warn` \| `alert` (CHECK-constrained) |
| `idempotency_key` | UNIQUE — the dedup contract, see below |
| `read_at` | NULL = unread. Set by ack (app mark-read, `hn bus ack`) |
| `delivered_via` | JSON list of transport markers, see below |

`meta` table carries `schema_version` (currently `1`); future migrations key off it.

## Deep-links — `payload.ref` (bus-app 0.1.14+)

A producer may put a **`ref`** object inside `payload_json` to make a signal *navigable*: the bus-app
Inbox renders a **"GO TO →"** button that jumps to the referenced spot. Purely additive — **no schema
change** (it rides the open `payload_json`), so any producer can adopt it and older events simply have no
button. Shape (the app's universal `Ref` descriptor):

```json
{ "ref": { "zone": "vault", "dir": "finance/research/positions/NVDA" } }
```

- `zone` (required) — `inbox | dash | surfaces | viz | vault`.
- `doc` — an exact vault-relative doc path · `dir` — a vault-relative dir, opened at its **newest** doc
  (via `list_vault_dir`) · `viz` — a `VizEntry.path` (selects that interactive diagram).
- **Existence-check producer-side** so there are no dead links (e.g. `finance.pulse` only sets `ref` for a
  symbol whose research dir exists — `events_from_pulse` stays pure; the caller does the check). The
  **career watchman** should emit a `ref` to the relevant openings/discoveries doc.

## Dedup — bus-side, by idempotency key

Publish is `INSERT … ON CONFLICT(idempotency_key) DO NOTHING` → result `published | duplicate`.
**Key convention**: `producer:kind:subject:YYYY-MM-DD` (local date) — derived automatically when a
draft's key is blank. This gives every producer once-per-(kind,subject)-per-day semantics by
default (exactly what pulse's `pulse-flags.json` enforced producer-side; that file retires in
Phase 3). Producers needing different windows compose their own keys — the bus only enforces
uniqueness.

## `delivered_via` — transport markers

Each delivery transport appends **only its own marker** after delivering: the tray app appends
`"desktop"` after posting the native notification; a future ntfy transport appends `"ntfy"`.
Rules: a transport delivers events that are **unread AND missing its marker**; markers are never
removed; `read_at` is orthogonal (delivered ≠ read). This is what makes adding a transport purely
additive — no schema change, no producer change.

## Producing events

- **Python (in-process)**: `BusService().publish_many(drafts)` — see `harness/finance/events.py`
  (`events_from_pulse`) for the canonical producer mapping, including the kind→severity map.
- **Any language / script**: `hn bus publish --lane X --kind Y --title T [--subject S] [--body B]
  [--severity info|warn|alert] [--payload '{"k":"v"}'|@file.json] [--producer P] [--key K]` —
  the universal on-ramp; nothing about the bus requires Python.
- Severity vocabulary: `alert` = act-worthy (trap fills, filings) · `warn` = look-soon (±5% moves)
  · `info` = awareness (calendar nearness). Encode the judgment in the producer, deterministically.

## Consuming events

- `hn bus list [--unread] [--lane X] [--kind Y] [--since ISO] [--limit N] [--json]`
- `hn bus ack <ID…> | --all [--lane X]` · `hn bus stats [--json]` · `hn bus purge --before 30d`
- MCP: `bus_list` / `bus_ack` / `bus_stats` / `bus_publish` on the unified server.
- The tray app (Phase 2, `bus-app/`) polls every 30s via rusqlite, posts notifications for
  undelivered unread events, marks `delivered_via`, and acks on user mark-read.

## Run-audit vs human-event (don't conflate)

`~/.local/state/harness/pulse.log` + `pulse-json.log` remain the **did-it-run audit** (every run,
quiet or not, plus bus publish counts: `[bus: 2 published, 1 dup]`). The bus holds only
**human-worthy events**. A quiet run writes a log line and zero bus rows.
