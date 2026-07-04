# Security

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (the repository's **Security** tab →
*Report a vulnerability*). Don't open a public issue for anything security-sensitive. You'll get an
acknowledgement; this is a personal project iterating in public, so please allow reasonable time for a fix.

## Security model — why the attack surface is small by design

Watchman is built to be **read-rich and execute-gated**, which is also its security posture:

- **No transactional execution.** The tools observe, research, and recommend. They do **not** trade, move
  money, book travel, or write to any third-party account. Execution is a deliberately separate escalation
  that is not part of this codebase.
- **No brokerage / account credentials.** The finance lane reads **public** market data and a **local**
  file (your own `portfolio.yaml`); no broker is ever in the loop, and no account login is required or
  accepted.
- **Secrets stay local.** API keys live in a git-ignored `.env` (see `.env.example`). Nothing in the repo
  contains real keys, and every key is independently optional — most senses are keyless.
- **The served surfaces are opt-in, token-gated, and read-only.** Nothing listens unless you run
  `hn bus serve`; it binds localhost unless you deliberately bind wider. Every `/api` route requires a
  bearer token (constant-time compare; auto-generated to a `0600` file), and the web console's RPC door
  mirrors only *read* commands — its sole write is marking notifications read. The token is
  defense-in-depth, not the perimeter: keep the transport private (mesh ACLs / VPN / TLS in front).
- **Non-PII outbound identity.** Every external HTTP call carries a tool-only `User-Agent` (no name, no
  email). The harness does not phone home.
- **Your corpus stays out of the repo.** Real personal data lives outside this repository (config-pointed);
  only **fictional** sample personas are bundled. Don't commit real data — see `CONTRIBUTING.md`.
- **Deterministic core, agent periphery.** No model call sits in a dashboard render loop or an alert loop;
  the runtime is deterministic and inspectable.

## Dependency & supply-chain scanning

Every push and pull request runs a layered scan in CI (see `.github/workflows/`):

- **Trivy** — filesystem scan (dependency CVEs + secrets + misconfig) **and** a scan of the published
  container image. The build **fails on a fixable HIGH/CRITICAL** finding — the gate before any release.
- **CodeQL** — static analysis (SAST) of the Python + TypeScript source.
- **Dependabot** — continuous dependency-vulnerability alerts and version-bump PRs across every ecosystem
  (Python, npm, Cargo, Docker, GitHub Actions).
- A pre-publish `osv-scanner` pass over the lockfiles is run locally as a final gate.

### Accepted, documented advisories

A short list of advisories are knowingly accepted because they have **no upstream fix** and **do not ship
in the container image** (which is Python-only — the desktop GUI ships as native bundles):

- The **GTK 3 Rust bindings** (`atk` / `gdk` / `gtk` / `gdkx11` / `glib` / `gtk3-macros` and the
  `unic-*` / `proc-macro-error` transitive crates) carry RustSec *unmaintained* advisories. They are
  pulled in by the desktop shell's GUI toolkit (Tauri's Linux backend) and have no fixed release; they
  are not part of the headless engine or its container image.
- `esbuild` (dev-only, a transitive build dependency of the test runner) — a low-severity advisory with
  no exploit path in a local dev/test context.

These are below the release gate (which fails only on *fixable* HIGH/CRITICAL) and are revisited as
upstream fixes land.

## Not advice

The finance lane is an **observation and sounding-board tool, not financial advice**, and the project is not
a licensed advisor. Outputs are informational; decisions are yours. Likewise the travel and career lanes
surface options and rankings — always verify anything before you act on it.
