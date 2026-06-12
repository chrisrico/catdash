"""FastAPI app: serves the dashboard, exposes JSON APIs, and runs the scheduled
collector in-process via APScheduler (one container, one process)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .collector import collect
from .config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).parent
scheduler = AsyncIOScheduler()

# In-process collection state. Collection takes several seconds — too long to
# hold an HTTP response open — so the manual trigger starts it in the background
# and the dashboard polls /api/refresh/status. The lock prevents a manual run
# from overlapping the scheduled one.
_collect_lock = asyncio.Lock()
_collect_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_error": None,
}


async def run_collection() -> dict:
    """Run one collection, recording status and never overlapping. Shared by
    startup, the scheduler, and the manual trigger."""
    if _collect_lock.locked():
        logger.info("collection already running; skipping overlapping trigger")
        return {"ok": False, "skipped": "already_running"}
    async with _collect_lock:
        _collect_state.update(running=True, started_at=time.time(), last_error=None)
        try:
            result = await collect()
            _collect_state["last_result"] = result
            if not result.get("ok"):
                _collect_state["last_error"] = result.get("error")
            return result
        except Exception as exc:  # noqa: BLE001 - a collection must never crash the loop
            logger.exception("collection crashed")
            # /api/refresh/status is unauthenticated, so expose only the
            # exception class; the full traceback stays in the logs.
            _collect_state["last_error"] = type(exc).__name__
            return {"ok": False, "error": type(exc).__name__}
        finally:
            _collect_state.update(running=False, finished_at=time.time())


def _find_static_dir() -> Path | None:
    """The built Svelte bundle: baked into catdash/static in Docker, or
    web/dist locally after `npm run build`."""
    for candidate in (BASE_DIR / "static", BASE_DIR.parent / "web" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


STATIC_DIR = _find_static_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        db.init_db()
    except db.DatabaseNotWritable as exc:
        # The classic cause: upgrading from the old root-running image without
        # the one-time chown (README "Updating"). Letting the exception escape
        # would dump a traceback on every restart-loop iteration and explain
        # nothing — log the fix and stop the process instead. os._exit (not
        # SystemExit) because the lifespan machinery wraps any exception in a
        # fresh traceback dump, which is exactly the noise we're avoiding.
        logger.error(str(exc))
        os._exit(1)
    logger.info("DB ready at %s", settings.db_path)

    # Kick off an immediate collection, then run on the configured interval.
    asyncio.create_task(run_collection())
    scheduler.add_job(
        run_collection,
        "interval",
        hours=settings.collect_interval_hours,
        id="collect",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started: collecting every %sh", settings.collect_interval_hours)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


# Interactive docs are off: the API is documented in the README, and a deployment
# reachable beyond localhost shouldn't advertise a "Try it out" console for it.
app = FastAPI(
    title="Catdash", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)
if STATIC_DIR is not None:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# The read endpoints are deliberately sync (`def`, not `async def`): FastAPI runs
# them in its threadpool, so the blocking SQLite reads can't stall the event loop
# that the scheduler and collector share.
@app.get("/api/pets")
def api_pets() -> list[dict]:
    return db.get_pets()


@app.get("/api/weights")
def api_weights(
    pet_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    return {
        "curated": db.get_weights(pet_id=pet_id, start=start, end=end),
        "raw": db.get_raw_weights(start=start, end=end),
    }


@app.get("/api/usage")
def api_usage(
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> list[dict]:
    return db.get_usage(start=start, end=end)


@app.get("/api/activities")
def api_activities(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    types: str | None = Query(None, description="Comma-separated category keys"),
) -> list[dict]:
    categories = [t for t in types.split(",") if t] if types else None
    return db.get_activities(start=start, end=end, limit=limit, categories=categories)


@app.get("/api/feedings")
def api_feedings(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict]:
    return db.get_feedings(start=start, end=end, limit=limit)


@app.get("/api/food")
def api_food(
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    """Feeder data: daily cups dispensed + hopper food-level snapshots."""
    return {
        "daily": db.get_daily_food(start=start, end=end),
        "levels": db.get_food_levels(start=start, end=end),
    }


@app.get("/api/faults")
def api_faults(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict]:
    """Fault events (Drawer Full, *Fault, Pinch Detect) from the activity stream."""
    return db.get_faults(start=start, end=end, limit=limit)


@app.get("/api/stats")
def api_stats(pet_id: str | None = Query(None)) -> dict:
    return db.get_stats(pet_id=pet_id)


# Named /api/refresh, NOT /api/collect: ad blockers (uBlock/EasyPrivacy, Firefox
# tracking protection) cancel requests to a "/collect" path because it matches
# analytics-beacon filters (Google Analytics posts to /collect). That silently
# breaks the button — the request never leaves the browser — so we avoid the path.
@app.post("/api/refresh")
async def api_refresh() -> JSONResponse:
    """Start a collection in the background and return immediately (202). The
    work takes several seconds — too long to hold the response open — so the
    client polls /api/refresh/status for progress and the result."""
    if _collect_state["running"] or _collect_lock.locked():
        return JSONResponse({"ok": True, "status": "already_running"}, status_code=202)
    # Cooldown: this endpoint is unauthenticated and each collection performs a
    # fresh credentialed login to Whisker, so without a floor between runs anyone
    # who can reach the dashboard could drive thousands of logins a day against
    # the account — indistinguishable from credential stuffing, and a lockout
    # loses history that rolls off Whisker's servers within days. The scheduled
    # collection bypasses this (it calls run_collection directly).
    cooldown = get_settings().refresh_cooldown_minutes * 60
    finished_at = _collect_state["finished_at"]
    if cooldown and finished_at is not None and (elapsed := time.time() - finished_at) < cooldown:
        retry_after = max(1, int(cooldown - elapsed))
        return JSONResponse(
            {"ok": False, "status": "cooldown", "retry_after_seconds": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    # Reflect "running" synchronously so the client's first status poll is
    # consistent even before the background task has acquired the lock.
    _collect_state["running"] = True
    asyncio.create_task(run_collection())
    return JSONResponse({"ok": True, "status": "started"}, status_code=202)


@app.get("/api/refresh/status")
async def api_refresh_status() -> dict:
    """Current/most-recent collection state, for the dashboard to poll."""
    return _collect_state


@app.get("/")
async def index():
    if STATIC_DIR is None:
        return PlainTextResponse(
            "Dashboard bundle not built. Run `npm run build` in web/ "
            "(or use `npm run dev` for the Vite dev server).",
            status_code=503,
        )
    # index.html references hash-named assets, so it must never be cached stale —
    # otherwise a browser keeps loading an old bundle after a rebuild. Force a
    # revalidation each load (the assets under /static are content-hashed and may
    # cache forever). no-store is belt-and-suspenders for Firefox's heuristic cache.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
