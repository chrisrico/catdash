# syntax=docker/dockerfile:1

# ---- Stage 1: build the Svelte dashboard bundle ----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ---- Stage 2: Python runtime (no Node) ----
FROM python:3.12-slim

# tzdata so a TZ override (or a symlinked host /etc/localtime) resolves to a real
# zone; without it glibc falls back to UTC. The compose file bind-mounts the host's
# /etc/localtime, so by default the container follows the host timezone.
# git so `uv sync` can fetch the pylitterbot dependency from its git source (the
# feeder schedule-write fork, until that support is released on PyPI).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata git \
    && rm -rf /var/lib/apt/lists/*

# The process holds Whisker credentials in its environment, so it runs as a
# non-root user — an app or dependency compromise shouldn't also hand out root.
RUN useradd --uid 1000 --create-home app

# uv for fast, reproducible dependency installs from the lockfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    DB_PATH=/data/catdash.db \
    PORT=8080

# Install dependencies first (cached layer), then the app code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY catdash ./catdash
COPY --from=web /web/dist ./catdash/static

# /data must be owned by `app` in the image so fresh named volumes inherit it.
# Volumes created by older (root-running) versions need a one-time
# `chown -R app:app /data` — see "Updating" in the README.
RUN mkdir -p /data && chown app:app /data
USER app

VOLUME ["/data"]
EXPOSE 8080

# Exec the venv's uvicorn directly: uv is only a build-time tool here, and
# `uv run` wants a home directory — which an arbitrary `user:` override
# (compose PUID/PGID) doesn't have.
CMD ["/app/.venv/bin/uvicorn", "catdash.main:app", "--host", "0.0.0.0", "--port", "8080"]
