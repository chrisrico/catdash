"""SQLite storage layer.

Four tables:
  - pets             : pet profiles (id, name) from the Whisker account.
  - weight_readings  : curated, SmartScale-attributed per-pet weights
                       (from pet.fetch_weight_history). This is the clean,
                       multi-cat weight series that drives the weight chart.
  - activities       : the full robot activity/usage stream
                       (from robot.get_activity_history) — cat detections,
                       clean cycles, litter dispensed, and raw weigh-ins.
  - daily_usage      : authoritative daily clean-cycle counts (from get_insight).
  - feedings         : Feeder-Robot meal/snack events (timestamp, cups, name).
  - food_level       : Feeder-Robot hopper level snapshots over time.

Timestamps are stored as ISO-8601 UTC strings, which sort and range-compare
lexicographically. All writes are idempotent (INSERT OR IGNORE / upsert) so
overlapping collection runs never create duplicates.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS pets (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_readings (
    pet_id      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,           -- ISO-8601 UTC of the weigh-in
    weight_lbs  REAL NOT NULL,
    inserted_at TEXT NOT NULL,
    PRIMARY KEY (pet_id, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_weight_pet_ts ON weight_readings(pet_id, timestamp);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,           -- ISO-8601 UTC of the event
    action      TEXT NOT NULL,           -- human label, e.g. "Cat Detected"
    weight_lbs  REAL,                    -- parsed when action is a weigh-in
    inserted_at TEXT NOT NULL,
    UNIQUE (timestamp, action)
);
CREATE INDEX IF NOT EXISTS idx_activities_ts ON activities(timestamp);
CREATE INDEX IF NOT EXISTS idx_activities_weight ON activities(weight_lbs)
    WHERE weight_lbs IS NOT NULL;

CREATE TABLE IF NOT EXISTS daily_usage (
    date        TEXT PRIMARY KEY,        -- YYYY-MM-DD (robot local day)
    cycles      INTEGER,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,           -- ISO-8601 UTC of the feeding
    type        TEXT NOT NULL,           -- 'meal' | 'snack'
    amount_cups REAL,
    name        TEXT,                    -- e.g. "Breakfast", "snack"
    inserted_at TEXT NOT NULL,
    UNIQUE (timestamp, type)
);
CREATE INDEX IF NOT EXISTS idx_feedings_ts ON feedings(timestamp);

CREATE TABLE IF NOT EXISTS food_level (
    timestamp   TEXT PRIMARY KEY,        -- collection time of the reading
    level       INTEGER,                 -- 0-100 percent (hopper fullness)
    inserted_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = Path(get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _count_new(conn: sqlite3.Connection, fn) -> int:
    before = conn.total_changes
    fn()
    return conn.total_changes - before


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def upsert_pets(pets: Iterable[dict[str, Any]]) -> int:
    rows = [(p["id"], p.get("name"), _now()) for p in pets]
    if not rows:
        return 0
    with connect() as conn:
        return _count_new(
            conn,
            lambda: conn.executemany(
                """INSERT INTO pets (id, name, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                                                 updated_at=excluded.updated_at""",
                rows,
            ),
        )


def upsert_weight_readings(readings: Iterable[dict[str, Any]]) -> int:
    now = _now()
    rows = [(r["pet_id"], r["timestamp"], float(r["weight_lbs"]), now) for r in readings]
    if not rows:
        return 0
    with connect() as conn:
        return _count_new(
            conn,
            lambda: conn.executemany(
                """INSERT OR IGNORE INTO weight_readings
                   (pet_id, timestamp, weight_lbs, inserted_at) VALUES (?, ?, ?, ?)""",
                rows,
            ),
        )


def upsert_activities(activities: Iterable[dict[str, Any]]) -> int:
    now = _now()
    rows = [(a["timestamp"], a["action"], a.get("weight_lbs"), now) for a in activities]
    if not rows:
        return 0
    with connect() as conn:
        return _count_new(
            conn,
            lambda: conn.executemany(
                """INSERT OR IGNORE INTO activities
                   (timestamp, action, weight_lbs, inserted_at) VALUES (?, ?, ?, ?)""",
                rows,
            ),
        )


def upsert_daily_usage(days: Iterable[dict[str, Any]]) -> int:
    now = _now()
    rows = [(d["date"], d["cycles"], now) for d in days]
    if not rows:
        return 0
    with connect() as conn:
        return _count_new(
            conn,
            lambda: conn.executemany(
                """INSERT INTO daily_usage (date, cycles, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET cycles=excluded.cycles,
                                                   updated_at=excluded.updated_at""",
                rows,
            ),
        )


def upsert_feedings(feedings: Iterable[dict[str, Any]]) -> int:
    now = _now()
    rows = [
        (f["timestamp"], f["type"], f.get("amount_cups"), f.get("name"), now)
        for f in feedings
    ]
    if not rows:
        return 0
    with connect() as conn:
        return _count_new(
            conn,
            lambda: conn.executemany(
                """INSERT OR IGNORE INTO feedings
                   (timestamp, type, amount_cups, name, inserted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                rows,
            ),
        )


def record_food_level(level: int | None) -> bool:
    """Snapshot the hopper level, but only when it changed (clean step series)."""
    if level is None:
        return False
    now = _now()
    with connect() as conn:
        last = conn.execute(
            "SELECT level FROM food_level ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if last is not None and last["level"] == level:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO food_level (timestamp, level, inserted_at) VALUES (?, ?, ?)",
            (now, level, now),
        )
    return True


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def _range(column: str, start: str | None, end: str | None) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end + "T23:59:59+00:00" if len(end) == 10 else end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def get_pets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM pets ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_weights(
    pet_id: str | None = None, start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if pet_id:
        clauses.append("pet_id = ?")
        params.append(pet_id)
    if start:
        clauses.append("timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("timestamp <= ?")
        params.append(end + "T23:59:59+00:00" if len(end) == 10 else end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT pet_id, timestamp, weight_lbs FROM weight_readings
                {where} ORDER BY timestamp""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_raw_weights(
    start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    """Raw weigh-ins parsed from the activity stream (not pet-attributed)."""
    where, params = _range("timestamp", start, end)
    clause = " AND weight_lbs IS NOT NULL" if where else " WHERE weight_lbs IS NOT NULL"
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT timestamp, weight_lbs FROM activities
                {where}{clause} ORDER BY timestamp""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_usage(
    start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    """Per-day usage: clean cycles (from insights) + weigh-ins (from activities)."""
    cyc_where, cyc_params = _range("date", start, end)
    with connect() as conn:
        cycles = {
            r["date"]: r["cycles"]
            for r in conn.execute(
                f"SELECT date, cycles FROM daily_usage{cyc_where} ORDER BY date",
                cyc_params,
            ).fetchall()
        }
        w_where, w_params = _range("timestamp", start, end)
        w_clause = (
            " AND weight_lbs IS NOT NULL"
            if w_where
            else " WHERE weight_lbs IS NOT NULL"
        )
        weighins = {
            r["day"]: r["n"]
            for r in conn.execute(
                f"""SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS n
                    FROM activities{w_where}{w_clause} GROUP BY day""",
                w_params,
            ).fetchall()
        }
    days = sorted(set(cycles) | set(weighins))
    return [
        {"date": d, "cycles": cycles.get(d, 0), "weighins": weighins.get(d, 0)}
        for d in days
    ]


def get_activities(
    start: str | None = None,
    end: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = _range("timestamp", start, end)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT timestamp, action, weight_lbs FROM activities
                {where} ORDER BY timestamp DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def get_feedings(
    start: str | None = None, end: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    where, params = _range("timestamp", start, end)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT timestamp, type, amount_cups, name FROM feedings
                {where} ORDER BY timestamp DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_food(
    start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    """Total cups dispensed per day."""
    where, params = _range("timestamp", start, end)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT substr(timestamp, 1, 10) AS date,
                       ROUND(SUM(amount_cups), 4) AS cups,
                       COUNT(*) AS feedings
                FROM feedings {where} GROUP BY date ORDER BY date""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_food_levels(
    start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    where, params = _range("timestamp", start, end)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT timestamp, level FROM food_level{where} ORDER BY timestamp",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats(pet_id: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        wclause, wparams = ("WHERE pet_id = ?", [pet_id]) if pet_id else ("", [])
        weight = conn.execute(
            f"""SELECT COUNT(*) AS count, MIN(weight_lbs) AS min, MAX(weight_lbs) AS max,
                       MIN(timestamp) AS first, MAX(timestamp) AS last
                FROM weight_readings {wclause}""",
            wparams,
        ).fetchone()

        latest = conn.execute(
            f"""SELECT weight_lbs FROM weight_readings {wclause}
                ORDER BY timestamp DESC LIMIT 1""",
            wparams,
        ).fetchone()
        earliest = conn.execute(
            f"""SELECT weight_lbs FROM weight_readings {wclause}
                ORDER BY timestamp ASC LIMIT 1""",
            wparams,
        ).fetchone()

        usage = conn.execute(
            "SELECT COALESCE(SUM(cycles), 0) AS total, COUNT(*) AS days FROM daily_usage"
        ).fetchone()
        busiest = conn.execute(
            "SELECT date, cycles FROM daily_usage ORDER BY cycles DESC, date DESC LIMIT 1"
        ).fetchone()

        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        feed = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(amount_cups), 0) AS total_cups FROM feedings"
        ).fetchone()
        recent = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_cups), 0) AS cups FROM feedings "
            "WHERE timestamp >= ?",
            [since_24h],
        ).fetchone()
        last_feeding = conn.execute(
            "SELECT timestamp, type, amount_cups, name FROM feedings "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        food = conn.execute(
            "SELECT level, timestamp FROM food_level ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    latest_w = latest["weight_lbs"] if latest else None
    earliest_w = earliest["weight_lbs"] if earliest else None
    change = (
        round(latest_w - earliest_w, 2)
        if latest_w is not None and earliest_w is not None
        else None
    )
    total_days = usage["days"] or 0
    return {
        "weight": {
            "count": weight["count"],
            "latest": latest_w,
            "min": weight["min"],
            "max": weight["max"],
            "change": change,
            "first": weight["first"],
            "last": weight["last"],
        },
        "usage": {
            "total_cycles": usage["total"],
            "days": total_days,
            "avg_cycles": round(usage["total"] / total_days, 2) if total_days else 0,
            "busiest_day": dict(busiest) if busiest else None,
        },
        "feeder": {
            "food_level": food["level"] if food else None,
            "feedings": feed["count"] if feed else 0,
            "total_cups": round(feed["total_cups"], 4) if feed else 0,
            "last_24h_cups": round(recent["cups"], 4) if recent else 0,
            "last_24h_feedings": recent["n"] if recent else 0,
            "last_feeding": dict(last_feeding) if last_feeding else None,
        },
    }
