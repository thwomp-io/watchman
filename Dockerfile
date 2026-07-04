# syntax=docker/dockerfile:1
#
# Watchman — the engine + web-console image (the `hn` CLI + standing agents + bus + the served UI).
#
# This is the CONTAINER half of the two-surface split: the desktop console (the Tauri GUI under
# bus-app/) ships as NATIVE bundles (.dmg / .msi / .AppImage), NOT as a container — a GUI desktop app
# is distributed natively, not via Docker. What runs here is the headless, display-free node: the
# read-only research/CLI lanes, the bus event layer, the schedulable standing agents, and the SAME
# React frontend the desktop embeds, served over HTTP by the bus server so any browser/phone is a
# console.
#
# Build:  docker build -t watchman .
# Engine: docker run --rm -v "$PWD/corpus:/corpus" watchman --help
# Console:docker run -p 8787:8787 -v "$PWD/corpus:/corpus" -v watchman-home:/home/watchman \
#           watchman bus serve --host 0.0.0.0 --console --ui /app/ui
#         (token auto-generates into the home volume: /home/watchman/.config/harness/bus-token)
# The corpus (a user's vault) is MOUNTED at /corpus (TRACKER_PATH) — never baked into the image.
# ONE image, many processes by design: a future compose file runs console + standing agents as
# separate services off this same image (the deployment story is a deferred backlog item).

# ---- builder: resolve the locked deps + build/install the project into a venv -------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependency layer first (cached across source-only changes): sync deps without the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then the source + a non-editable install (builds the wheel → self-contained /app/.venv, no src/ at runtime).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- ui: build the React console the server serves (same dist the Tauri shell embeds) -----------
# npm ci is hermetic against the lockfile; playwright is a devDependency but its browser download
# only happens on an explicit `playwright install`, so this stage stays lean.
FROM node:22-slim AS ui
WORKDIR /ui
COPY bus-app/package.json bus-app/package-lock.json ./
RUN npm ci
COPY bus-app/index.html bus-app/vite.config.ts bus-app/tsconfig.json bus-app/tsconfig.node.json ./
COPY bus-app/public ./public
COPY bus-app/src ./src
RUN npm run build

# ---- runtime: slim, non-root, just the venv + the CLI on PATH ------------------------------------
FROM python:3.12-slim-bookworm AS runtime
LABEL org.opencontainers.image.title="Watchman" \
      org.opencontainers.image.description="The headless engine — the hn CLI + standing agents + bus." \
      org.opencontainers.image.source="https://github.com/thwomp-io/watchman" \
      org.opencontainers.image.licenses="Apache-2.0"

# The venv's bin (carrying `hn`/`harness`/`harness-mcp`) goes on PATH; the corpus is read from
# TRACKER_PATH, defaulted to the mount point so `docker run -v …:/corpus` just works.
ENV PATH="/app/.venv/bin:$PATH" \
    TRACKER_PATH="/corpus" \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Least-privilege: a non-root runtime user owns the mounted corpus dir.
RUN useradd --create-home --uid 10001 watchman \
    && mkdir -p /corpus \
    && chown watchman:watchman /corpus
COPY --from=builder --chown=watchman:watchman /app/.venv /app/.venv
# the web console's static UI (served via `bus serve --console --ui /app/ui`)
COPY --from=ui --chown=watchman:watchman /ui/dist /app/ui

USER watchman
VOLUME ["/corpus"]
EXPOSE 8787
ENTRYPOINT ["hn"]
CMD ["--help"]
