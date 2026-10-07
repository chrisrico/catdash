"""Per-cat data on a multi-cat account: feeders assigned to a cat (or shared, or
none — a manually fed cat), and shared-litter-box weigh-ins attributed to the
cat that made them. Stdlib unittest: `python -m unittest discover tests`.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")

from catdash import db  # noqa: E402
from catdash.config import get_settings  # noqa: E402

get_settings.cache_clear()

BIG, SMALL = "PET-big", "PET-small"  # ~11 lb and ~7.5 lb cats


def _reset() -> None:
    # Other test modules may have re-pointed DB_PATH at import; use ours.
    os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")
    get_settings.cache_clear()
    db.init_db()
    with db.connect() as conn:
        for table in ("pets", "feeders", "feedings", "food_level", "activities", "weight_readings"):
            conn.execute(f"DELETE FROM {table}")
    db.upsert_pets([{"id": BIG, "name": "Big"}, {"id": SMALL, "name": "Small"}])


def tearDownModule():
    _TMP.cleanup()


class FeederAssignmentTest(unittest.TestCase):
    def setUp(self):
        _reset()
        db.upsert_feeders([{"id": "1", "name": "Bowl", "serial": "RF1"}])
        db.upsert_feedings(
            [
                {"feeder_id": "1", "timestamp": "2026-10-01T12:00:00+00:00",
                 "type": "meal", "amount_cups": 0.25, "name": "Lunch"},
            ]
        )
        db.record_food_level("1", 80)

    def test_unassigned_feeder_is_shared(self):
        for pet in (BIG, SMALL, None):
            self.assertEqual(len(db.get_feedings(pet_id=pet)), 1)

    def test_assigned_feeder_only_feeds_its_cat(self):
        self.assertTrue(db.set_feeder_pet("1", BIG))
        self.assertEqual(len(db.get_feedings(pet_id=BIG)), 1)
        self.assertEqual(len(db.get_daily_food(pet_id=BIG)), 1)
        self.assertEqual(len(db.get_food_levels(pet_id=BIG)), 1)
        # The other cat is fed manually: no food data at all, not BIG's.
        self.assertEqual(db.get_feedings(pet_id=SMALL), [])
        self.assertEqual(db.get_daily_food(pet_id=SMALL), [])
        self.assertEqual(db.get_food_levels(pet_id=SMALL), [])
        feeder = db.get_stats(pet_id=SMALL)["feeder"]
        self.assertEqual((feeder["feedings"], feeder["food_level"]), (0, None))
        # "All cats" still sees everything.
        self.assertEqual(len(db.get_feedings()), 1)

    def test_assignment_survives_collection_upsert(self):
        db.set_feeder_pet("1", BIG)
        db.upsert_feeders([{"id": "1", "name": "Renamed", "serial": "RF1"}])
        self.assertEqual(db.get_feeders()[0]["pet_id"], BIG)

    def test_two_feeders_same_schedule_dont_collide(self):
        db.upsert_feeders([{"id": "2", "name": "Bowl 2"}])
        added = db.upsert_feedings(
            [{"feeder_id": "2", "timestamp": "2026-10-01T12:00:00+00:00",
              "type": "meal", "amount_cups": 0.25, "name": "Lunch"}]
        )
        self.assertEqual(added, 1)


class LegacyFeedingMigrationTest(unittest.TestCase):
    """A DB from before feeders were tracked migrates in place, and a feeder's
    first (full) re-fetch claims the legacy rows instead of duplicating them."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE feedings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                type TEXT NOT NULL, amount_cups REAL, name TEXT,
                inserted_at TEXT NOT NULL, UNIQUE (timestamp, type));
            CREATE TABLE food_level (
                timestamp TEXT PRIMARY KEY, level INTEGER, inserted_at TEXT NOT NULL);
            INSERT INTO feedings (timestamp, type, amount_cups, name, inserted_at)
                VALUES ('2026-01-01T08:00:00+00:00', 'meal', 0.25, 'Breakfast', 'x');
            INSERT INTO food_level VALUES ('2026-01-01T08:00:00+00:00', 90, 'x');
            """
        )
        conn.commit()
        conn.close()
        os.environ["DB_PATH"] = str(path)
        get_settings.cache_clear()

    def tearDown(self):
        os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")
        get_settings.cache_clear()
        self.dir.cleanup()

    def test_migrate_then_claim(self):
        db.init_db()
        self.assertEqual(len(db.get_feedings()), 1)
        self.assertIsNone(db.latest_feeding_timestamp("42"))
        # Two feeders' full backfills: 42 re-fetches the legacy meal (claims it),
        # 43 has its own meal at the same time (a new row, no collision).
        meal = {"timestamp": "2026-01-01T08:00:00+00:00", "type": "meal",
                "amount_cups": 0.25, "name": "Breakfast"}
        self.assertEqual(db.upsert_feedings([{**meal, "feeder_id": "42"}]), 0)
        self.assertEqual(db.upsert_feedings([{**meal, "feeder_id": "43"}]), 1)
        self.assertEqual(db.latest_feeding_timestamp("42"), meal["timestamp"])
        self.assertEqual(len(db.get_feedings()), 2)  # not 3: no NULL leftover
        # Hopper snapshots can't be re-fetched; the sole feeder claims them.
        db.claim_legacy_food_levels("42")
        self.assertFalse(db.record_food_level("42", 90))  # unchanged level
        db.init_db()  # idempotent on an already-migrated DB
        self.assertEqual(len(db.get_feedings()), 2)


def _weighin(conn, ts: str, lbs: float) -> None:
    conn.execute(
        "INSERT INTO activities (timestamp, action, weight_lbs, inserted_at) VALUES (?, ?, ?, ?)",
        (ts, f"Pet Weight Recorded: {lbs} lbs", lbs, ts),
    )


def _event(conn, ts: str, action: str) -> None:
    conn.execute(
        "INSERT INTO activities (timestamp, action, inserted_at) VALUES (?, ?, ?)",
        (ts, action, ts),
    )


class WeighInAttributionTest(unittest.TestCase):
    def setUp(self):
        _reset()
        with db.connect() as conn:
            # Old weigh-in with no curated twin: falls back to the closest weight.
            _weighin(conn, "2026-01-01T10:00:00+00:00", 11.0)
            # Recent weigh-ins, each with a curated twin a few seconds later.
            _weighin(conn, "2026-10-06T10:00:00+00:00", 7.5)
            _weighin(conn, "2026-10-06T12:00:00+00:00", 11.1)
            # A partial/odd reading near neither cat stays unattributed.
            _weighin(conn, "2026-10-06T14:00:00+00:00", 3.0)
            # Near SMALL's weight but before SMALL was ever tracked: not SMALL's
            # (and too light for BIG, the only cat around then).
            _weighin(conn, "2026-01-02T10:00:00+00:00", 7.0)
        db.upsert_weight_readings(
            [
                # BIG was tracked long before SMALL joined the household.
                {"pet_id": BIG, "timestamp": "2026-06-15T10:00:00+00:00", "weight_lbs": 11.0},
                {"pet_id": SMALL, "timestamp": "2026-10-06T10:00:04+00:00", "weight_lbs": 7.5},
                {"pet_id": BIG, "timestamp": "2026-10-06T12:00:03+00:00", "weight_lbs": 11.1},
            ]
        )

    def _times(self, pet):
        return [r["timestamp"][:10] + " " + r["timestamp"][11:13] for r in db.get_raw_weights(pet_id=pet)]

    def test_each_cat_gets_its_own_weighins(self):
        self.assertEqual(self._times(BIG), ["2026-01-01 10", "2026-10-06 12"])
        self.assertEqual(self._times(SMALL), ["2026-10-06 10"])
        self.assertEqual(len(db.get_raw_weights()), 5)  # all cats: unfiltered

    def test_single_cat_owns_every_weighin(self):
        with db.connect() as conn:
            conn.execute("DELETE FROM pets WHERE id = ?", [SMALL])
        self.assertEqual(len(db.get_raw_weights(pet_id=BIG)), 5)

    def test_time_in_box_follows_the_weighin(self):
        with db.connect() as conn:
            # SMALL's visit: detected 09:59, weighed 10:00, cycle at 10:20.
            _event(conn, "2026-10-06T09:59:00+00:00", "Cat Detected")
            _event(conn, "2026-10-06T10:20:00+00:00", "Clean Cycle In Progress")
            # BIG's visit: detected 11:59, weighed 12:00, cycle at 12:20.
            _event(conn, "2026-10-06T11:59:00+00:00", "Cat Detected")
            _event(conn, "2026-10-06T12:20:00+00:00", "Clean Cycle In Progress")
            # A visit with no weigh-in (never settled on the scale) shortly after
            # BIG's must not inherit BIG.
            _event(conn, "2026-10-06T12:25:00+00:00", "Cat Detected")
            _event(conn, "2026-10-06T12:43:00+00:00", "Clean Cycle In Progress")
        cycles = lambda pet: [ts for ts, _ in db.get_visit_durations(pet_id=pet)["samples"]]  # noqa: E731
        self.assertEqual(cycles(SMALL), ["2026-10-06T10:20:00+00:00"])
        self.assertEqual(cycles(BIG), ["2026-10-06T12:20:00+00:00"])
        self.assertEqual(len(cycles(None)), 3)


if __name__ == "__main__":
    unittest.main()
