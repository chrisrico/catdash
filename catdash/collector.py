"""Pull data from the Whisker API via pylitterbot and persist it.

One `collect()` does:
  1. Connect (loading robots + pets).
  2. For each pet: upsert the profile and its curated weight history.
  3. For each Litter-Robot: upsert the full activity stream (with raw weigh-ins
     parsed out) and the daily clean-cycle counts from insights.
  4. For each Feeder-Robot: upsert meal/snack feedings and snapshot the hopper
     food level.

Everything is idempotent, so running it on a schedule simply accumulates history
beyond the ~30 days Whisker retains. All failures are caught and reported so a
transient API/auth hiccup never crashes the scheduler — the next run retries.
"""

from __future__ import annotations

import logging
import re

from pylitterbot import Account

from . import db
from .config import get_settings

logger = logging.getLogger("collector")

_WEIGHT_RE = re.compile(r"([\d]+(?:\.[\d]+)?)\s*lbs", re.IGNORECASE)


def _action_label(action: object) -> str:
    """Normalize an Activity.action (str or LitterBoxStatus enum) to a label."""
    if isinstance(action, str):
        return action
    return getattr(action, "text", str(action))


def _parse_weight(label: str) -> float | None:
    match = _WEIGHT_RE.search(label)
    return float(match.group(1)) if match else None


def _meal_cups(entry: dict) -> float | None:
    """Cups dispensed for a meal = per-portion amount × number of portions."""
    amount = entry.get("amount")
    if amount is None:
        return None
    portions = entry.get("meal_total_portions") or 1
    return round(amount * portions, 4)


def _feeder_feedings(robot: object) -> list[dict]:
    """Extract meal/snack events from the feeder's data arrays."""
    data = getattr(robot, "_data", None) or {}
    rows: list[dict] = []
    for entry in data.get("feeding_meal") or []:
        ts = entry.get("timestamp")
        if ts:
            rows.append(
                {
                    "timestamp": str(ts),
                    "type": "meal",
                    "amount_cups": _meal_cups(entry),
                    "name": entry.get("meal_name") or "Meal",
                }
            )
    for entry in data.get("feeding_snack") or []:
        ts = entry.get("timestamp")
        if ts:
            rows.append(
                {
                    "timestamp": str(ts),
                    "type": "snack",
                    "amount_cups": entry.get("amount"),
                    "name": entry.get("name") or entry.get("meal_name") or "Snack",
                }
            )
    return rows


async def collect() -> dict:
    """Run one full collection pass. Returns a summary (never raises)."""
    settings = get_settings()
    if not settings.has_credentials:
        logger.error("No Whisker credentials configured; skipping collection.")
        return {"ok": False, "error": "missing credentials"}

    account = Account()
    summary: dict = {"ok": False}
    try:
        await account.connect(
            username=settings.username,
            password=settings.password,
            load_robots=True,
            load_pets=True,
        )

        # ---- Pets + curated per-pet weight history ----
        pet_rows = [{"id": pet.id, "name": pet.name} for pet in account.pets]
        db.upsert_pets(pet_rows)

        weight_rows: list[dict] = []
        for pet in account.pets:
            try:
                history = await pet.fetch_weight_history(limit=settings.weight_limit)
            except Exception:  # noqa: BLE001
                logger.exception("weight history failed for pet %s", pet.id)
                continue
            for m in history:
                weight_rows.append(
                    {
                        "pet_id": pet.id,
                        "timestamp": m.timestamp.isoformat(),
                        "weight_lbs": float(m.weight),
                    }
                )
        new_weights = db.upsert_weight_readings(weight_rows)

        # ---- Litter-Robots (activity + cycles) and Feeder-Robots (feedings) ----
        activity_rows: list[dict] = []
        usage_rows: list[dict] = []
        feeding_rows: list[dict] = []
        litter_robots = 0
        feeders = 0
        food_level_changed = False
        for robot in account.robots:
            if hasattr(robot, "get_activity_history"):  # Litter-Robot
                litter_robots += 1
                try:
                    acts = await robot.get_activity_history(limit=settings.activity_limit)
                    for a in acts:
                        label = _action_label(a.action)
                        activity_rows.append(
                            {
                                "timestamp": a.timestamp.isoformat(),
                                "action": label,
                                "weight_lbs": _parse_weight(label),
                            }
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("activity history failed for %s", robot.serial)

                if hasattr(robot, "get_insight"):
                    try:
                        insight = await robot.get_insight(days=settings.insight_days)
                        for day, cycles in insight.cycle_history:
                            usage_rows.append({"date": day.isoformat(), "cycles": cycles})
                    except Exception:  # noqa: BLE001
                        logger.exception("insight failed for %s", robot.serial)

            if hasattr(robot, "food_level"):  # Feeder-Robot
                feeders += 1
                try:
                    feeding_rows.extend(_feeder_feedings(robot))
                    if db.record_food_level(robot.food_level):
                        food_level_changed = True
                except Exception:  # noqa: BLE001
                    logger.exception("feeder collection failed for %s", robot.serial)

        new_activities = db.upsert_activities(activity_rows)
        db.upsert_daily_usage(usage_rows)
        new_feedings = db.upsert_feedings(feeding_rows)

        summary = {
            "ok": True,
            "pets": len(pet_rows),
            "litter_robots": litter_robots,
            "feeders": feeders,
            "weights_fetched": len(weight_rows),
            "weights_new": new_weights,
            "activities_fetched": len(activity_rows),
            "activities_new": new_activities,
            "usage_days": len(usage_rows),
            "feedings_fetched": len(feeding_rows),
            "feedings_new": new_feedings,
            "food_level_changed": food_level_changed,
        }
        logger.info("collection ok: %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("collection failed")
        return {"ok": False, "error": repr(exc)}
    finally:
        try:
            await account.disconnect()
        except Exception:  # noqa: BLE001
            pass
