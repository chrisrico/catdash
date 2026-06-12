# 🐈‍⬛ Catdash

Self-hosted system that periodically pulls your **Litter-Robot 4** (cat weight +
usage) and **Feeder-Robot** (feedings + food level) data from the Whisker API and
stores it **permanently** — so you keep a full history instead of the rolling
~30 days the Whisker app retains — and serves a dashboard to view it over time.

## What it does

- **Collects** on a schedule (default every 6h) via
  [`pylitterbot`](https://github.com/natekspencer/pylitterbot):
  - **Per-cat weight** — the curated SmartScale readings Whisker attributes to each
    pet (`pet.fetch_weight_history`). Multi-cat ready out of the box.
  - **Litter-box usage / activity** — the full event stream (cat detections, clean
    cycles, litter dispensed, raw weigh-ins) and authoritative daily cycle counts.
  - **Feeder** — meal/snack feedings (with cups dispensed) and hopper food-level
    snapshots over time.
- **Stores** everything in a SQLite database on a persistent volume. All writes are
  idempotent, so overlapping runs never duplicate rows.
- **Visualizes** it: a weight chart (curated readings + 7-day average + optional raw
  weigh-ins), a usage chart (cycles + weigh-ins per day), a feeder chart (cups
  dispensed per day + hopper level), summary stat cards, and a recent-activity feed.

Runs as **one small container, one process** — a FastAPI app that both serves the
dashboard and runs the scheduled collector in-process (no cron, no second service).
The dashboard is a **Svelte + ECharts** single-page app, built at image-build time
and served as a fully **self-hosted static bundle** (no CDN dependencies, works
offline / on isolated networks).

## Quick start (Docker)

```bash
cp .env.example .env          # then edit .env with your Whisker email + password
docker compose up --build -d
```

Open **http://localhost:8080** (or your server's IP). On first launch it backfills
everything Whisker still has, then keeps appending on schedule. Click **Collect
now** any time to pull immediately.

Data lives in the `catdash_data` Docker volume (`/data/catdash.db`), so it survives
`docker compose down` / rebuilds. To change the schedule or port, set
`COLLECT_INTERVAL_HOURS` / `PORT` / `TZ` in `.env`.

## Configuration

All via environment variables (see [`.env.example`](.env.example)):

| Variable                             | Default | Purpose |
|--------------------------------------| --- | --- |
| `WHISKER_EMAIL` / `WHISKER_PASSWORD` | — | Whisker account credentials (**required**) |
| `COLLECT_INTERVAL_HOURS`             | `6` | How often to pull from Whisker |
| `PORT`                               | `8080` | Dashboard port |
| `TZ`                                 | `UTC` | Container timezone |
| `ACTIVITY_LIMIT`                     | `1000` | Activity rows requested per robot per run |
| `WEIGHT_LIMIT`                       | `5000` | Per-pet weight measurements per run |
| `INSIGHT_DAYS`                       | `365` | Days of daily clean-cycle history requested (auto-clamped to the robot's setup date) |
| `DB_PATH`                            | `/data/catdash.db` | SQLite location |

## API

The dashboard is built on a small JSON API you can also use directly:

- `GET /api/pets` — pets/cats on the account
- `GET /api/weights?pet_id=&start=&end=` — `{curated, raw}` weight series
- `GET /api/usage?start=&end=` — daily cycles + weigh-ins
- `GET /api/activities?start=&end=&limit=` — litter-box activity feed
- `GET /api/feedings?start=&end=&limit=` — feeder meal/snack events
- `GET /api/food?start=&end=` — `{daily, levels}` cups dispensed + hopper level
- `GET /api/stats?pet_id=` — summary stats (weight + usage + feeder)
- `POST /api/collect` — trigger a collection now
- `GET /healthz` — health check

## Local development

Backend (Python via `uv`) and frontend (Svelte via Node ≥ 20) are separate builds:

```bash
uv sync
cp .env.example .env          # add your credentials
uv run python scripts/probe.py        # sanity-check the Whisker connection
uv run uvicorn catdash.main:app --port 8080

# Frontend — either hot-reload dev mode (Vite on :5173, proxies /api to :8080):
cd web && npm install && npm run dev

# ...or a production build (FastAPI then serves web/dist itself at :8080):
cd web && npm install && npm run build
```

In Docker none of this matters — the image build compiles the frontend in a
Node stage and bakes the bundle in; the runtime image has no Node at all.

## How weights work (and the `3.3 lbs` mystery)

Whisker's SmartScale sometimes records partial/spurious weigh-ins (e.g. a cat
half-on the scale). The **curated** per-pet series filters these out and is what the
weight chart shows by default. The **raw** weigh-ins (every `Pet Weight Recorded`
event from the activity stream) are still stored and viewable via the *“Show raw
weigh-ins”* toggle — nothing is discarded.

With a single cat, every weigh-in belongs to that cat. With multiple cats, Whisker's
own per-pet attribution is used, so each cat gets its own series automatically.

## History & backfill

How far back Whisker still has data varies a lot by source, and the first
collection grabs the maximum of each automatically:

- **Feedings** — the full history (years; back to first setup) is pulled from the
  feeder's backing store on the first run and mirrored idempotently thereafter.
  (`pylitterbot` itself only exposes the last 24h, so the collector queries the
  feeding history directly.)
- **Activity** (clean cycles, raw weigh-ins, litter dispensed — the app's
  "Download Data" stream) — fetched back to the robot's setup date. The trick is
  passing an explicit `startTimestamp`; without one the API returns only a recent
  slice, which is why the default looks like ~a week of retention.
- **Daily clean cycles** — fetched back to the litter robot's setup date
  (`INSIGHT_DAYS`, auto-clamped — the insights API returns broken data if asked
  for a window predating setup).
- **Curated per-pet weights** — Whisker only retains a short window server-side
  for the curated SmartScale series, so it can't be backfilled beyond what it
  still holds (the raw weigh-ins, however, come back in full via the activity
  stream). Its value comes from catdash accumulating it over time.

## Notes

- `pylitterbot` is an unofficial, reverse-engineered client; a Whisker API change
  could break collection. The version is pinned in `uv.lock`; parsers are tolerant
  (unknown/unparseable events are skipped, not fatal).
- Keep credentials in `.env` only — it is gitignored and excluded from the image.
- Because raw activity/weights roll off server-side within days, avoid long
  outages: `restart: unless-stopped` plus the 6h interval keeps a safety margin.
