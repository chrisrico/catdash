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
- **Visualizes** it: a weight chart (weigh-in trend + 7-day average, with raw
  weigh-ins toggleable from the legend), a usage chart (cycles + weigh-ins per day),
  a feeder chart (cups dispensed per day + hopper level), summary stat cards, and a
  recent-activity feed. Light/dark theme follows the OS.

Runs as **one small container, one process** — a FastAPI app that both serves the
dashboard and runs the scheduled collector in-process (no cron, no second service).
The dashboard is a **Svelte + ECharts** single-page app, built at image-build time
and served as a fully **self-hosted static bundle** (no CDN dependencies, works
offline / on isolated networks).

## Quick start (Docker)

No clone, no build — the image is published to GHCR, so two files in an empty
folder are all you need:

```bash
mkdir catdash && cd catdash
curl -O https://raw.githubusercontent.com/chrisrico/catdash/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/chrisrico/catdash/main/.env.example
# edit .env: add your Whisker email + password (optionally set DATA_DIR / PORT / TZ)
docker compose up -d
```

`docker compose up` pulls the prebuilt `ghcr.io/chrisrico/catdash:latest` image.
Open **http://localhost:8080** (or your server's IP). On first launch it backfills
everything Whisker still has, then keeps appending on schedule. Click **Collect
now** any time to pull immediately.

Data persists across `docker compose down` and updates. By default it lives in a
Docker-managed `catdash_data` volume; set `DATA_DIR` to a host folder to keep the
SQLite DB somewhere you can back it up (see [Deploying in production](#deploying-in-production)).
Tune the schedule, port, and timezone via `.env` — see [Configuration](#configuration).

## Deploying in production

The published image is the supported deployment — run it on whatever's always on
(a NAS, home server, or VPS). Nothing to clone or build on the host: you only need
the `docker-compose.yml` and a `.env` from the [Quick start](#quick-start-docker)
above.

**Synology / Container Manager.** Make a folder for the app (e.g.
`/volume1/docker/catdash`), put `docker-compose.yml` + `.env` in it, then either run
`docker compose up -d` over SSH, or create a **Container Manager → Project** pointed
at that folder — no SSH and no source required.

The container has a healthcheck and `restart: unless-stopped`, so it comes back on
its own after a reboot or crash.

**Data & backups.** The SQLite DB is the whole point — the permanent history you
can't re-fetch from Whisker. Keep it in a host folder you already back up by
setting a path in `.env` *before first start*:

```bash
DATA_DIR=/volume1/docker/catdash   # e.g. a Synology shared folder
```

Leaving `DATA_DIR` unset stores it in a Docker-managed `catdash_data` volume
instead. Either way, back up `catdash.db` on your normal schedule.

**Updating** — pull the new image and recreate; your data is untouched:

```bash
docker compose pull && docker compose up -d
```

> **Upgrading from a version that ran as root:** the container now runs as a
> non-root user, so a DB created by an older version is root-owned and
> unwritable until ownership is fixed once. The nicest fix: set `PUID`/`PGID`
> in `.env` to the host user who owns your data folder (values from `id -u` /
> `id -g`) and chown the folder to that user — ownership then matches what you
> already manage:
>
> ```bash
> sudo chown -R "$(id -u):$(id -g)" /volume1/docker/catdash   # your DATA_DIR
> ```
>
> Or skip `PUID`/`PGID` and chown to the image's default user (uid 1000):
>
> ```bash
> docker compose run --rm --user root dashboard chown -R app:app /data
> ```
>
> If the app can't write its DB it now says so at startup, with the exact
> `chown` to run.

**Logs & health:**

```bash
docker compose logs -f          # follow collector + request logs
docker compose ps               # container state + health
curl localhost:8080/healthz     # -> {"status":"ok"}
```

**Exposure.** The dashboard is plain HTTP on port `8080` with **no authentication** —
anyone who can reach it sees your data and the *Collect now* button (manual
collections are rate-limited by `REFRESH_COOLDOWN_MINUTES` so strangers can't hammer
Whisker with your credentials). This matters even more if you turn on
[Remote control](#remote-control) (`CONTROLS_ENABLED`), which lets anyone who can
reach the page actually command your robots. The recommended way to reach it from outside your
LAN is [Tailscale](https://tailscale.com) (or any VPN): install it on the host and
on your devices, open `http://<host>:8080` over the tailnet, and nothing is exposed
to the public internet. If you genuinely want it public, front it with a reverse
proxy that adds TLS and auth (Caddy, nginx, or Synology's built-in reverse proxy).
Change the published port with `PORT` in `.env`.

**Building it yourself.** A GitHub Actions workflow
([`.github/workflows/publish.yml`](.github/workflows/publish.yml)) builds the image
and pushes `ghcr.io/<owner>/catdash:latest` on every push to `main`. To build
locally instead: `docker build -t ghcr.io/chrisrico/catdash:latest .` (then
`docker compose up -d`), or run it without Docker via [Local development](#local-development).

## Configuration

All via environment variables (see [`.env.example`](.env.example)):

| Variable                             | Default | Purpose |
|--------------------------------------| --- | --- |
| `WHISKER_EMAIL` / `WHISKER_PASSWORD` | — | Whisker account credentials (**required**) |
| `COLLECT_INTERVAL_HOURS`             | `6` | How often to pull from Whisker |
| `REFRESH_COOLDOWN_MINUTES`           | `10` | Minimum minutes between manual *Collect now* runs (`429` inside the window; `0` disables) |
| `CONTROLS_ENABLED`                   | `false` | Add the **Robots** panel: live status + **remote control** (start cycle, night light, wait time, panel lock, power, feeder snack, …). No dashboard auth, so only enable on a trusted network — see [Remote control](#remote-control). Off → the control endpoints `404` and catdash stays read-only |
| `PUID` / `PGID`                      | `1000` | Host user/group to run the container as (compose only) — set to the owner of `DATA_DIR` (`id -u` / `id -g`) |
| `PORT`                               | `8080` | Dashboard port |
| `TZ`                                 | _(host)_ | Timezone for log timestamps; defaults to the host's (via the `/etc/localtime` mount) — set to override |
| `ACTIVITY_LIMIT`                     | `50000` | Max activity rows requested per robot per run (the first backfill is large; later runs are incremental) |
| `WEIGHT_LIMIT`                       | `5000` | Per-pet weight measurements per run |
| `INSIGHT_DAYS`                       | `365` | Days of daily clean-cycle history requested (auto-clamped to the robot's setup date) |
| `DATA_DIR`                           | _(named volume)_ | Host folder for the DB (compose only); unset → a Docker-managed volume |
| `DB_PATH`                            | `/data/catdash.db` | SQLite path **inside** the container |

## API

The dashboard is built on a small JSON API you can also use directly:

- `GET /api/pets` — pets/cats on the account
- `GET /api/weights?pet_id=&start=&end=` — `{curated, raw}` weight series
- `GET /api/usage?start=&end=` — daily cycles + weigh-ins
- `GET /api/activities?start=&end=&limit=` — litter-box activity feed
- `GET /api/feedings?start=&end=&limit=` — feeder meal/snack events
- `GET /api/food?start=&end=` — `{daily, levels}` cups dispensed + hopper level
- `GET /api/stats?pet_id=` — summary stats (weight + usage + feeder)
- `POST /api/refresh` — start a collection now (returns `202` immediately; runs in
  the background — poll `GET /api/refresh/status` for progress/result). Returns
  `429` with `retry_after_seconds` within `REFRESH_COOLDOWN_MINUTES` of the last
  run. Named `refresh`, not `collect`, so ad blockers don't cancel it as an
  analytics beacon.
- `GET /healthz` — health check
- `GET /api/config` — feature flags the dashboard reads at load (`{controls_enabled}`)

**Control** (only when `CONTROLS_ENABLED=true`; otherwise these `404`) — see
[Remote control](#remote-control):

- `GET /api/robots` — live snapshot of every Litter-Robot + Feeder (status,
  litter/drawer levels, wait time, night light, power, food level, …)
- `POST /api/robots/{id}/clean` — start a clean cycle
- `POST /api/robots/{id}/power` · `/wait-time` · `/night-light` · `/panel-lock` ·
  `/panel-brightness` · `/name` · `/reset` · `/hopper` · `/firmware-update`
- `POST /api/feeders/{id}/snack` · `/gravity-mode` · `/meal-insert-size` ·
  `/night-light` · `/panel-lock` · `/name`

Each command returns the robot's post-command snapshot (`{ok, robot}`).

## Remote control

By default catdash is **read-only** — it collects and visualizes history and
never sends a command to a robot. Set **`CONTROLS_ENABLED=true`** to unlock a
**Robots** panel that mirrors the core of the Whisker app: each unit's live
status and gauges plus controls to start a clean cycle, change the wait time,
toggle the night light / panel lock / power, enable the LitterHopper, and — for
the Feeder — give a snack, switch gravity mode, or set the portion size. It talks
to Whisker through the same [`pylitterbot`](https://github.com/natekspencer/pylitterbot)
client the collector uses, over one reused, lazily-connected session.

> **This is a real switch, not a view.** The dashboard has **no authentication**,
> so with controls on, anyone who can reach the page can power off your robot or
> start a cycle. Only enable it on a trusted network — the same
> [Tailscale/LAN](#deploying-in-production) isolation the read-only dashboard
> already relies on. With the flag off, the control endpoints don't exist at all.

Not yet implemented (the Whisker app has them; `pylitterbot` doesn't expose
them, so they're deferred): editing the **sleep schedule**, **resetting the
waste-drawer gauge**, and **push notifications**.

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

Whisker's SmartScale sometimes records implausible weigh-ins — a cat half-on the
scale reads far low (the infamous `3.3 lbs`), a double/glitch weigh-in reads far
high. The weight chart builds its trend from the raw weigh-ins (median per
day/week/month bucket) but first drops these artifacts: any reading deviating more
than ~15% from the rolling median of its time-neighbors is rejected — in either
direction — while genuine slow drift is kept. The raw weigh-ins are always stored
(nothing is discarded) and can be shown on the chart by toggling the **Raw
weigh-ins** series in its legend.

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
