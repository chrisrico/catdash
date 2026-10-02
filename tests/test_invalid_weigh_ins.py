"""Marking a weigh-in invalid flags the row instead of deleting it (a delete
would be undone by the next idempotent collection), flags its curated twin so
the weight stats skip it, and is reversible. Also covers the ALTER-in migration
for databases created before `invalid_at` existed.

Stdlib unittest, like the other tests: `python -m unittest discover tests`.
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

RAW_TS = "2026-06-16T22:30:33+00:00"
CURATED_TS = "2026-06-16T22:30:37+00:00"  # Whisker stamps curated a few seconds later


def tearDownModule():
    _TMP.cleanup()


class InvalidWeighInTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.connect() as conn:
            conn.execute("DELETE FROM activities")
            conn.execute("DELETE FROM weight_readings")
        db.upsert_activities(
            [
                {"timestamp": RAW_TS, "action": "Pet Weight Recorded: 11.03 lbs", "weight_lbs": 11.03},
                {"timestamp": "2026-06-16T22:40:00+00:00", "action": "Clean Cycle In Progress"},
                {"timestamp": "2026-06-17T03:00:00+00:00", "action": "Pet Weight Recorded: 10.9 lbs", "weight_lbs": 10.9},
            ]
        )
        db.upsert_weight_readings(
            [
                {"pet_id": "p1", "timestamp": CURATED_TS, "weight_lbs": 11.03},
                {"pet_id": "p1", "timestamp": "2026-06-17T03:00:04+00:00", "weight_lbs": 10.9},
            ]
        )

    def _raw(self, ts=RAW_TS):
        return next(r for r in db.get_raw_weights() if r["timestamp"] == ts)

    def test_rows_start_valid(self):
        self.assertFalse(any(r["invalid"] for r in db.get_raw_weights()))
        self.assertFalse(any(r["invalid"] for r in db.get_weights()))
        self.assertEqual(db.get_stats()["weight"]["count"], 2)

    def test_mark_and_restore(self):
        row = self._raw()
        updated = db.set_weigh_in_invalid(row["id"], True)
        self.assertEqual(updated, {**row, "invalid": True})
        self.assertTrue(self._raw()["invalid"])
        # Still present, just flagged — both for raw and the activity feed.
        self.assertEqual(len(db.get_raw_weights()), 2)
        feed = {a["id"]: a for a in db.get_activities()}
        self.assertTrue(feed[row["id"]]["invalid"])

        db.set_weigh_in_invalid(row["id"], False)
        self.assertFalse(self._raw()["invalid"])
        self.assertFalse(any(r["invalid"] for r in db.get_weights()))

    def test_curated_twin_follows_and_stats_skip_it(self):
        db.set_weigh_in_invalid(self._raw()["id"], True)
        curated = {r["timestamp"]: r["invalid"] for r in db.get_weights()}
        self.assertEqual(curated, {CURATED_TS: True, "2026-06-17T03:00:04+00:00": False})

        stats = db.get_stats()["weight"]
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["latest"], 10.9)
        self.assertEqual(stats["max"], 10.9)
        self.assertEqual(stats["first"], "2026-06-17T03:00:04+00:00")

        db.set_weigh_in_invalid(self._raw()["id"], False)
        self.assertEqual(db.get_stats()["weight"]["count"], 2)

    def test_curated_twin_needs_same_weight_and_nearby_time(self):
        # A curated reading with the same weight but hours away is a different visit.
        db.upsert_weight_readings(
            [{"pet_id": "p1", "timestamp": "2026-06-16T10:00:00+00:00", "weight_lbs": 11.03}]
        )
        db.set_weigh_in_invalid(self._raw()["id"], True)
        flagged = {r["timestamp"] for r in db.get_weights() if r["invalid"]}
        self.assertEqual(flagged, {CURATED_TS})

    def test_survives_recollection(self):
        row = self._raw()
        db.set_weigh_in_invalid(row["id"], True)
        new = db.upsert_activities(
            [{"timestamp": RAW_TS, "action": "Pet Weight Recorded: 11.03 lbs", "weight_lbs": 11.03}]
        )
        db.upsert_weight_readings([{"pet_id": "p1", "timestamp": CURATED_TS, "weight_lbs": 11.03}])
        self.assertEqual(new, 0)
        self.assertTrue(self._raw()["invalid"])
        self.assertTrue(next(r for r in db.get_weights() if r["timestamp"] == CURATED_TS)["invalid"])

    def test_only_weigh_ins_can_be_invalidated(self):
        with db.connect() as conn:
            clean_id = conn.execute(
                "SELECT id FROM activities WHERE action = 'Clean Cycle In Progress'"
            ).fetchone()["id"]
        self.assertIsNone(db.set_weigh_in_invalid(clean_id, True))
        self.assertIsNone(db.set_weigh_in_invalid(999999, True))

    def test_visits_still_count(self):
        # Invalid means the weight is wrong, not that the cat wasn't there.
        db.set_weigh_in_invalid(self._raw()["id"], True)
        self.assertEqual(sum(d["weighins"] for d in db.get_usage()), 2)


class MigrationTest(unittest.TestCase):
    """A DB created by an older release lacks `invalid_at`; init_db adds it."""

    def test_adds_missing_columns_to_existing_tables(self):
        path = Path(_TMP.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE weight_readings (
                pet_id TEXT NOT NULL, timestamp TEXT NOT NULL, weight_lbs REAL NOT NULL,
                inserted_at TEXT NOT NULL, PRIMARY KEY (pet_id, timestamp));
            CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                action TEXT NOT NULL, weight_lbs REAL, inserted_at TEXT NOT NULL,
                UNIQUE (timestamp, action));
            INSERT INTO activities (timestamp, action, weight_lbs, inserted_at)
                VALUES ('2026-01-01T00:00:00+00:00', 'Pet Weight Recorded: 9 lbs', 9, 'x');
            """
        )
        conn.commit()
        conn.close()

        os.environ["DB_PATH"] = str(path)
        get_settings.cache_clear()
        try:
            db.init_db()
            db.init_db()  # idempotent: a second run must not fail on the existing column
            with db.connect() as conn:
                for table in ("weight_readings", "activities"):
                    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                    self.assertIn("invalid_at", cols, table)
            # Pre-existing rows read as valid.
            self.assertEqual([r["invalid"] for r in db.get_raw_weights()], [False])
        finally:
            os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
