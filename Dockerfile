# syntax=docker/dockerfile:1
#
# Watchman — the headless engine image (the `hn` CLI + standing agents + bus).
#
# This is the CONTAINER half of the two-surface split: the desktop console (the Tauri GUI under
# bus-app/) ships as NATIVE bundles (.dmg / .msi / .AppImage), NOT as a container — a GUI desktop app
# is distributed natively, not via Docker. What runs here is the headless, display-free engine: the
# read-only research/CLI lanes, the bus event layer, and the schedulable standing agents.
#
# Build:  docker build -t watchman .
# Run:    docker run --rm -v "$PWD/corpus:/corpus" ghcr.io/thwomp-io/watchman hn --help
# The corpus (a user's vault) is MOUNTED at /corpus (TRACKER_PATH) — never baked into the image.

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

# ---- runtime: slim, non-root, just the venv + the CLI on PATH ------------------------------------
FROM python:3.14-slim-bookworm AS runtime
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

USER watchman
VOLUME ["/corpus"]
ENTRYPOINT ["hn"]
CMD ["--help"]
