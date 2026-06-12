"""FastAPI app: serves the dashboard, exposes JSON APIs, and runs the scheduled
collector in-process via APScheduler (one container, one process)."""

from __future__ import annotations

import asyncio
import logging
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
    db.init_db()
    logger.info("DB ready at %s", settings.db_path)

    # Kick off an immediate collection, then run on the configured interval.
    asyncio.create_task(collect())
    scheduler.add_job(
        collect,
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


app = FastAPI(title="Catdash", lifespan=lifespan)
if STATIC_DIR is not None:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/pets")
async def api_pets() -> list[dict]:
    return db.get_pets()


@app.get("/api/weights")
async def api_weights(
    pet_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    return {
        "curated": db.get_weights(pet_id=pet_id, start=start, end=end),
        "raw": db.get_raw_weights(start=start, end=end),
    }


@app.get("/api/usage")
async def api_usage(
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> list[dict]:
    return db.get_usage(start=start, end=end)


@app.get("/api/activities")
async def api_activities(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    types: str | None = Query(None, description="Comma-separated category keys"),
) -> list[dict]:
    categories = [t for t in types.split(",") if t] if types else None
    return db.get_activities(start=start, end=end, limit=limit, categories=categories)


@app.get("/api/feedings")
async def api_feedings(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict]:
    return db.get_feedings(start=start, end=end, limit=limit)


@app.get("/api/food")
async def api_food(
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    """Feeder data: daily cups dispensed + hopper food-level snapshots."""
    return {
        "daily": db.get_daily_food(start=start, end=end),
        "levels": db.get_food_levels(start=start, end=end),
    }


@app.get("/api/faults")
async def api_faults(
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict]:
    """Fault events (Drawer Full, *Fault, Pinch Detect) from the activity stream."""
    return db.get_faults(start=start, end=end, limit=limit)


@app.get("/api/stats")
async def api_stats(pet_id: str | None = Query(None)) -> dict:
    return db.get_stats(pet_id=pet_id)


@app.post("/api/collect")
async def api_collect() -> JSONResponse:
    """Trigger a collection now (handy for testing / first-run backfill)."""
    result = await collect()
    return JSONResponse(result, status_code=200 if result.get("ok") else 502)


@app.get("/")
async def index():
    if STATIC_DIR is None:
        return PlainTextResponse(
            "Dashboard bundle not built. Run `npm run build` in web/ "
            "(or use `npm run dev` for the Vite dev server).",
            status_code=503,
        )
    return FileResponse(STATIC_DIR / "index.html")
