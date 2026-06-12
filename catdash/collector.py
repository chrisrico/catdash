"""Pull data from the Whisker API via pylitterbot and persist it.

One `collect()` does:
  1. Connect (loading robots + pets).
  2. For each pet: upsert the profile and its curated weight history.
  3. For each Litter-Robot: upsert the full activity stream (with raw weigh-ins
     parsed out) and the daily clean-cycle counts from insights.
  4. For each Feeder-Robot: upsert meal/snack feedings and snapshot the hopper
     food level.

Everything is idempotent, so the first run backfills the maximum history each
source still has (feedings span years; daily cycles back to the robot's setup)
and later runs just append. All failures are caught and reported so a transient
API/auth hiccup never crashes the scheduler — the next run retries.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from pylitterbot import Account
from pylitterbot.robot.feederrobot import FEEDER_ENDPOINT
from pylitterbot.utils import to_timestamp

from . import db
from .config import get_settings

logger = logging.getLogger("collector")

# Page size for backfilling the feeder's full feeding history from Hasura.
_FEEDING_PAGE = 5000

# Earliest date we request activity/weigh-in history from. The API caps at the
# robot's actual setup date, so a fixed early date simply returns everything.
_ACTIVITY_START = "2020-01-01"

_WEIGHT_RE = re.compile(r"([\d]+(?:\.[\d]+)?)\s*lbs", re.IGNORECASE)


def _action_label(action: object) -> str:
    """Normalize an Activity.action (str or LitterBoxStatus enum) to a label."""
    if isinstance(action, str):
        return action
    return getattr(action, "text", str(action))


def _parse_weight(label: str) -> float | None:
    match = _WEIGHT_RE.search(label)
    return float(match.group(1)) if match else None


async def _fetch_activities(robot: object, limit: int) -> list[dict]:
    """Fetch the full Litter-Robot activity history (cycle status + raw weigh-ins).

    pylitterbot's get_activity_history() sends no startTimestamp, so the API
    returns only a recent slice (~a week). Passing an explicit date-only
    startTimestamp returns everything back to the robot's setup — the same data
    the app's "Download Data" exports. We query directly and reuse pylitterbot's
    own row parser so stored labels match the rest of the activity stream.
    """
    query = """
        query History($serial: String!, $start: String, $limit: Int) {
            getLitterRobot4Activity(
                serial: $serial, startTimestamp: $start, limit: $limit, consumer: "app"
            ) { timestamp value actionValue }
        }
    """
    data = await robot._post(  # noqa: SLF001
        json={
            "query": query,
            "variables": {"serial": robot.serial, "start": _ACTIVITY_START, "limit": limit},
        }
    )
    rows = (data or {}).get("data", {}).get("getLitterRobot4Activity") or []
    out: list[dict] = []
    for raw in rows:
        try:
            ts = to_timestamp(raw.get("timestamp"))
            if ts is None:
                continue
            label = _action_label(robot._parse_activity(raw))  # noqa: SLF001
            out.append(
                {"timestamp": ts.isoformat(), "action": label, "weight_lbs": _parse_weight(label)}
            )
        except Exception:  # noqa: BLE001
            continue  # tolerant: skip any unparseable row
    return out


def _insight_days(robot: object, requested: int) -> int:
    """Clamp the insight window so its start stays near (but not past) setup.

    The insights endpoint returns a broken all-zero history when startTimestamp
    predates the unit's setupDateTime, so we never ask for more than the robot's
    age (minus a day of margin)."""
    setup = (getattr(robot, "_data", None) or {}).get("setupDateTime")
    if not setup:
        return requested
    try:
        setup_dt = datetime.fromisoformat(str(setup).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - setup_dt).days
    except (ValueError, TypeError):
        return requested
    return max(1, min(requested, age_days - 1))


async def _collect_insight(robot: object, requested_days: int):
    """Fetch the daily clean-cycle history, tolerating the insights API's
    intermittent all-zero responses near the robot's full age.

    The endpoint randomly returns an empty (all-zero) history even for windows
    that otherwise work, so we step the window back a few days until real data
    comes back. Returns None if every attempt is empty — the caller then skips
    the write rather than overwriting stored counts with zeros."""
    start = _insight_days(robot, requested_days)
    for days in range(start, max(0, start - 6), -1):
        insight = await robot.get_insight(days=days)
        if insight.total_cycles:
            return insight
    return None


def _meal_cups(entry: dict) -> float | None:
    """Cups dispensed for a meal = per-portion amount × number of portions."""
    amount = entry.get("amount")
    if amount is None:
        return None
    portions = entry.get("meal_total_portions") or 1
    return round(amount * portions, 4)


def _feeding_row(entry: dict, kind: str) -> dict:
    """Map a Hasura feeding_meal/feeding_snack row to a storable feeding."""
    if kind == "meal":
        return {
            "timestamp": str(entry["timestamp"]),
            "type": "meal",
            "amount_cups": _meal_cups(entry),
            "name": entry.get("meal_name") or "Meal",
        }
    return {
        "timestamp": str(entry["timestamp"]),
        "type": "snack",
        "amount_cups": entry.get("amount"),
        "name": entry.get("name") or entry.get("meal_name") or "Snack",
    }


async def _fetch_feedings(account: Account, robot: object) -> list[dict]:
    """Fetch the feeder's full dispensed feeding history directly from Hasura.

    pylitterbot only exposes the last 24h of feedings (a hard-coded filter on
    the unit-state query), but the underlying table holds the entire history
    (years). We query it directly with pagination and mirror all of it; the
    idempotent upsert dedups, so re-pulling each run is cheap and self-healing
    (any past gap is filled). At a few thousand rows this is comfortably small.
    """
    # feeding_snack has no meal_* columns; request each table's own fields.
    tables = {
        "feeding_meal": "timestamp amount meal_name meal_total_portions",
        "feeding_snack": "timestamp amount",
    }
    rows: list[dict] = []
    for table, fields in tables.items():
        kind = "meal" if table == "feeding_meal" else "snack"
        offset = 0
        while True:
            query = f"""
                query History($id: Int!, $limit: Int!, $offset: Int!) {{
                    feeder_unit_by_pk(id: $id) {{
                        {table}(
                            where: {{ status: {{ _eq: dispensed }} }}
                            order_by: {{ timestamp: asc }}
                            limit: $limit
                            offset: $offset
                        ) {{ {fields} }}
                    }}
                }}
            """
            resp = await account.session.post(
                FEEDER_ENDPOINT,
                json={
                    "query": query,
                    "variables": {"id": robot.id, "limit": _FEEDING_PAGE, "offset": offset},
                },
            )
            unit = (resp.get("data") or {}).get("feeder_unit_by_pk") or {}
            batch = unit.get(table) or []
            rows.extend(_feeding_row(e, kind) for e in batch if e.get("timestamp"))
            if len(batch) < _FEEDING_PAGE:
                break
            offset += _FEEDING_PAGE
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
                    activity_rows.extend(
                        await _fetch_activities(robot, settings.activity_limit)
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("activity history failed for %s", robot.serial)

                if hasattr(robot, "get_insight"):
                    try:
                        insight = await _collect_insight(robot, settings.insight_days)
                        if insight is not None:
                            for day, cycles in insight.cycle_history:
                                usage_rows.append({"date": day.isoformat(), "cycles": cycles})
                        else:
                            logger.warning(
                                "insight returned only zeros for %s; keeping stored counts",
                                robot.serial,
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("insight failed for %s", robot.serial)

            if hasattr(robot, "food_level"):  # Feeder-Robot
                feeders += 1
                try:
                    feeding_rows.extend(await _fetch_feedings(account, robot))
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
