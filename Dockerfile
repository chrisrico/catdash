3# syntax=docker/dockerfile:1

# ---- Stage 1: build the Svelte dashboard bundle ----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

# ---- Stage 2: Python runtime (no Node) ----
FROM python:3.12-slim

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

VOLUME ["/data"]
EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "catdash.main:app", "--host", "0.0.0.0", "--port", "8080"]
