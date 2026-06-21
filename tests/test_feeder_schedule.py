"""Feeder schedule editor merge logic (control._build_schedule_meals): client
edits become the raw replacement meal list for FeederRobot.set_schedule.

Existing meals keep their identity (id/scheduleId) and untouched state
(paused/skip); new meals get a minted number + fresh id; meals absent from the
edit are dropped; bad input is rejected. Pure, no network.
"""

from __future__ import annotations

import unittest

from catdash import control
from catdash.control import ControlError, _build_schedule_meals

ALL = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RAW = [
    {"id": "id1", "scheduleId": None, "mealNumber": 1, "name": "Breakfast",
     "hour": 8, "minute": 0, "days": list(ALL), "portions": 1,
     "paused": False, "skip": control._NO_SKIP},
    {"id": "id2", "scheduleId": None, "mealNumber": 2, "name": "Lunch",
     "hour": 13, "minute": 0, "days": list(ALL), "portions": 1,
     "paused": True, "skip": "2026-06-22T00:00:00.000"},
]


class BuildScheduleMealsTest(unittest.TestCase):
    def test_edit_existing_preserves_identity_and_untouched_state(self):
        out = _build_schedule_meals(RAW, [
            {"meal_number": 1, "name": "Breakfast", "hour": 7, "minute": 30,
             "days": ["Mon", "Wed", "Fri"], "portions": 2},
            {"meal_number": 2, "name": "Lunch", "hour": 13, "minute": 0,
             "days": list(ALL), "portions": 1},
        ])
        self.assertEqual([m["mealNumber"] for m in out], [1, 2])
        b = out[0]
        self.assertEqual(b["id"], "id1")  # identity preserved
        self.assertEqual((b["hour"], b["minute"], b["portions"]), (7, 30, 2))
        self.assertEqual(b["days"], ["Mon", "Wed", "Fri"])
        # The editor doesn't touch pause/skip — meal 2 keeps both.
        self.assertTrue(out[1]["paused"])
        self.assertEqual(out[1]["skip"], "2026-06-22T00:00:00.000")

    def test_add_meal_mints_number_and_fresh_id(self):
        out = _build_schedule_meals(RAW, [
            {"meal_number": 1, "name": "Breakfast", "hour": 8, "minute": 0,
             "days": ["Mon"], "portions": 1},
            {"meal_number": None, "name": "Dinner", "hour": 18, "minute": 0,
             "days": ["Sat", "Sun"], "portions": 3},
        ])
        new = out[1]
        self.assertEqual(new["mealNumber"], 3)  # max(1, 2) + 1
        self.assertNotIn(new["id"], {"id1", "id2"})
        self.assertFalse(new["paused"])
        self.assertEqual(new["skip"], control._NO_SKIP)
        self.assertEqual(new["days"], ["Sat", "Sun"])

    def test_paused_is_editable_but_preserved_when_omitted(self):
        out = _build_schedule_meals(RAW, [
            # meal 1 sends paused -> applied; meal 2 omits it -> keeps base (True)
            {"meal_number": 1, "name": "Breakfast", "hour": 8, "minute": 0,
             "days": ["Mon"], "portions": 1, "paused": True},
            {"meal_number": 2, "name": "Lunch", "hour": 13, "minute": 0,
             "days": ["Mon"], "portions": 1},
        ])
        self.assertTrue(out[0]["paused"])   # set by the editor
        self.assertTrue(out[1]["paused"])   # preserved from the raw meal

    def test_duplicate_meal_number_does_not_clone_identity(self):
        # A second item claiming meal_number 1 must not reuse meal 1's id; it
        # becomes a fresh meal with a minted number instead.
        out = _build_schedule_meals(RAW, [
            {"meal_number": 1, "name": "Breakfast", "hour": 8, "minute": 0,
             "days": ["Mon"], "portions": 1},
            {"meal_number": 1, "name": "Imposter", "hour": 9, "minute": 0,
             "days": ["Tue"], "portions": 1},
        ])
        self.assertEqual(out[0]["id"], "id1")
        self.assertNotEqual(out[1]["id"], "id1")
        self.assertEqual([m["mealNumber"] for m in out], [1, 3])  # 3 = max+1

    def test_remove_meal_drops_it(self):
        out = _build_schedule_meals(RAW, [
            {"meal_number": 2, "name": "Lunch", "hour": 13, "minute": 0,
             "days": ["Mon"], "portions": 1},
        ])
        self.assertEqual([m["mealNumber"] for m in out], [2])

    def test_days_normalized_to_weekday_order(self):
        out = _build_schedule_meals(RAW, [
            {"meal_number": 1, "name": "B", "hour": 8, "minute": 0,
             "days": ["Sun", "Mon", "Fri"], "portions": 1},
        ])
        self.assertEqual(out[0]["days"], ["Mon", "Fri", "Sun"])

    def test_rejects_bad_input(self):
        ok = {"meal_number": 1, "name": "B", "hour": 8, "minute": 0,
              "days": ["Mon"], "portions": 1}
        for bad in ({"hour": 24}, {"minute": 60}, {"portions": 0},
                    {"portions": 99}, {"days": []}, {"name": "  "}):
            with self.assertRaises(ControlError):
                _build_schedule_meals(RAW, [{**ok, **bad}])
        with self.assertRaises(ControlError):
            _build_schedule_meals(RAW, [])  # a schedule can't be emptied here


if __name__ == "__main__":
    unittest.main()
