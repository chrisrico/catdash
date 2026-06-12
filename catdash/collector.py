"""Pull data from the Whisker API via pylitterbot and persist it.

One `collect()` does:
  1. Connect (loading robots + pets).
  2. For each pet: upsert the profile and its curated weight history.
  3. For each Litter-Robot: upsert the full activity stream (with raw weigh-ins
     parsed out) and the daily clean-cycle counts from insights.
  4. For each Feeder-Robot: upsert meal/snack feedings and snapshot the hopper
     food level.

Everything is idempotent. The FIRST run backfills the maximum history each
source still has (feedings span years; activity/cycles back to the robot's
setup). Once data exists, later runs are INCREMENTAL — they only request items
newer than the latest stored timestamp of that type (with a small overlap
margin), so a routine collection pulls a handful of rows in a second or two
instead of re-downloading the entire history. All failures are caught and
reported so a transient API/auth hiccup never crashes the scheduler — the next
run retries.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from pylitterbot import Account
from pylitterbot.robot.feederrobot import FEEDER_ENDPOINT
from pylitterbot.utils import to_timestamp

from . import db
from .config import get_settings

logger = logging.getLogger("collector")

# Page size for backfilling the feeder's full feeding history from Hasura.
_FEEDING_PAGE = 5000

# Earliest date we request activity/weigh-in history from on a first (backfill)
# run. The API caps at the robot's actual setup date, so a fixed early date
# simply returns everything.
_ACTIVITY_START = "2020-01-01"

_WEIGHT_RE = re.compile(r"([\d]+(?:\.[\d]+)?)\s*lbs", re.IGNORECASE)


def _incremental_start_date(latest_iso: str | None, margin_days: int = 1) -> str | None:
    """A date-only (YYYY-MM-DD) start = the latest stored day minus a margin, or
    None when nothing is stored yet (caller then does a full backfill).

    The margin re-pulls the last day or two so events near a UTC/local-day
    boundary are never missed; the idempotent upsert dedups the overlap."""
    if not latest_iso:
        return None
    try:
        dt = datetime.fromisoformat(latest_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (dt - timedelta(days=margin_days)).date().isoformat()


def _action_label(action: object) -> str:
    """Normalize an Activity.action (str or LitterBoxStatus enum) to a label."""
    if isinstance(action, str):
        return action
    return getattr(action, "text", str(action))


def _parse_weight(label: str) -> float | None:
    match = _WEIGHT_RE.search(label)
    return float(match.group(1)) if match else None


async def _fetch_activities(
    robot: object, limit: int, start: str = _ACTIVITY_START
) -> list[dict]:
    """Fetch Litter-Robot activity history (cycle status + raw weigh-ins) from
    `start` (a date-only YYYY-MM-DD) onward.

    pylitterbot's get_activity_history() sends no startTimestamp, so the API
    returns only a recent slice (~a week). Passing an explicit date-only
    startTimestamp returns everything from that date to now — a fixed early date
    backfills the full history (the app's "Download Data" export), while the
    latest stored day makes routine runs incremental. We query directly and
    reuse pylitterbot's own row parser so labels match the rest of the stream.
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
            "variables": {"serial": robot.serial, "start": start, "limit": limit},
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


def _insight_request(latest_date: str | None, full_days: int, margin: int = 2) -> int:
    """Days of daily-cycle history to request this run.

    First run (no stored usage): the full window, to backfill. Afterwards: only
    enough days to cover since the latest stored day plus a small margin — so a
    routine run refreshes just the last day or two (also sidestepping the
    insights API's flaky behavior on large, near-setup windows)."""
    if not latest_date:
        return full_days
    try:
        gap = (datetime.now(timezone.utc).date() - datetime.fromisoformat(latest_date).date()).days
    except (ValueError, TypeError):
        return full_days
    return min(full_days, max(margin + 1, gap + margin))


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


async def _fetch_feedings(
    account: Account, robot: object, since: str | None = None
) -> list[dict]:
    """Fetch the feeder's dispensed feeding history directly from Hasura.

    pylitterbot only exposes the last 24h of feedings (a hard-coded filter on
    the unit-state query), but the underlying table holds the entire history
    (years). On a first run (`since` is None) we paginate the whole table; once
    feedings are stored we pass the latest stored timestamp as a `_gte` bound so
    routine runs fetch only the few new rows. The idempotent upsert dedups the
    boundary row, so no feeding is ever missed or duplicated.
    """
    # feeding_snack has no meal_* columns; request each table's own fields.
    tables = {
        "feeding_meal": "timestamp amount meal_name meal_total_portions",
        "feeding_snack": "timestamp amount",
    }
    # `since` is our own stored ISO timestamp (originally from Hasura), so it's
    # safe to inline; only add the predicate when we have a cursor.
    ts_pred = f', timestamp: {{ _gte: "{since}" }}' if since else ""
    rows: list[dict] = []
    for table, fields in tables.items():
        kind = "meal" if table == "feeding_meal" else "snack"
        offset = 0
        while True:
            query = f"""
                query History($id: Int!, $limit: Int!, $offset: Int!) {{
                    feeder_unit_by_pk(id: $id) {{
                        {table}(
                            where: {{ status: {{ _eq: dispensed }}{ts_pred} }}
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

        # ---- Incremental cursors: only fetch items newer than what's stored ----
        # On a first run these are None/early, so the whole history backfills;
        # afterwards each type resumes just after its latest stored timestamp.
        activity_start = (
            _incremental_start_date(db.latest_activity_timestamp()) or _ACTIVITY_START
        )
        feeding_since = db.latest_feeding_timestamp()
        # Daily cycle counts: refresh only the days since the last stored one
        # (plus margin) once we have history; full window on a first run.
        insight_request = _insight_request(db.latest_usage_date(), settings.insight_days)
        logger.info(
            "collecting from cursors: activities>=%s, feedings>=%s, insight_days=%s",
            activity_start,
            feeding_since or "(full backfill)",
            insight_request,
        )

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
                        await _fetch_activities(
                            robot, settings.activity_limit, activity_start
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("activity history failed for %s", robot.serial)

                if hasattr(robot, "get_insight"):
                    try:
                        insight = await _collect_insight(robot, insight_request)
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
                    feeding_rows.extend(
                        await _fetch_feedings(account, robot, feeding_since)
                    )
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
        # The summary surfaces on the unauthenticated /api/refresh/status, so
        # expose only the exception class; the full traceback is in the logs.
        return {"ok": False, "error": type(exc).__name__}
    finally:
        try:
            await account.disconnect()
        except Exception:  # noqa: BLE001
            pass
