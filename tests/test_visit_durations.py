"""Time-in-box is scored against the delay (clean-cycle wait time) that was
actually in effect at each visit — 7 min before 2026-06-14, 15 min on/after.

Regression guard for the bug where every post-6/14 visit came out 8 min too long
because the wait_time series lacked the 7 -> 15 transition (see
db._wait_time_history). Stdlib unittest so it needs no extra dependency:
`python -m unittest discover tests`.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Point the storage layer at a throwaway DB *before* importing it, so neither the
# real .env DB_PATH nor a cached Settings leaks in.
_TMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")

from catdash import db  # noqa: E402
from catdash.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _add(conn, timestamp: str, action: str) -> None:
    conn.execute(
        "INSERT INTO activities (timestamp, action, inserted_at) VALUES (?, ?, ?)",
        (timestamp, action, timestamp),
    )


class VisitDurationDelayTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    def setUp(self):
        db.init_db()  # also seeds the known wait-time history
        with db.connect() as conn:
            conn.execute("DELETE FROM activities")
            # Before 6/14: delay was 7 min. 10-min gap -> 3-min time-in-box.
            _add(conn, "2026-06-10T10:00:00+00:00", "Cat Detected")
            _add(conn, "2026-06-10T10:10:00+00:00", "Clean Cycle In Progress")
            # On/after 6/14: delay is 15 min. 20-min gap -> 5-min time-in-box.
            _add(conn, "2026-06-16T10:00:00+00:00", "Cat Detected")
            _add(conn, "2026-06-16T10:20:00+00:00", "Clean Cycle In Progress")

    def test_delay_applied_per_visit(self):
        result = db.get_visit_durations()
        by_cycle = {ts: dur for ts, dur in result["samples"]}
        # 600s gap - 7*60 = 180s
        self.assertEqual(by_cycle["2026-06-10T10:10:00+00:00"], 180)
        # 1200s gap - 15*60 = 300s  (was 720s when the old 7-min delay was used)
        self.assertEqual(by_cycle["2026-06-16T10:20:00+00:00"], 300)


if __name__ == "__main__":
    unittest.main()
