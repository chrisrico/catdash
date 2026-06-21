"""Stream-triggered collection (control.WhiskerControl): a meaningful robot
change over the websocket debounces exactly one collection, bursts collapse into
one run, and the first snapshot per robot is only a baseline.

Stdlib unittest + asyncio, no network (WhiskerControl() does no I/O until used).
Run: `.venv/bin/python -m unittest discover -s tests`.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from catdash import control as control_mod
from catdash.control import WhiskerControl

LR = {"id": "lr1", "kind": "litter_robot", "cycle_count": 10}


def _fast_timers():
    # Tiny debounce, no rate floor, so the test runs in milliseconds.
    return (
        mock.patch.object(control_mod, "_STREAM_COLLECT_DEBOUNCE", 0.05),
        mock.patch.object(control_mod, "_STREAM_COLLECT_MIN_GAP", 0.0),
    )


class StreamCollectTest(unittest.IsolatedAsyncioTestCase):
    def _control(self):
        calls: list[int] = []

        async def trigger():
            calls.append(1)

        c = WhiskerControl()
        c.set_collection_trigger(trigger)
        return c, calls

    async def test_baseline_then_burst_is_one_run(self):
        c, calls = self._control()
        d, g = _fast_timers()
        with d, g:
            c._maybe_trigger_collection(LR)  # first snapshot = baseline, no run
            for n in (11, 11, 12, 12):       # a burst of updates from one cycle
                c._maybe_trigger_collection({**LR, "cycle_count": n})
            await asyncio.sleep(0.2)
        self.assertEqual(calls, [1])  # exactly one collection

    async def test_no_change_no_run(self):
        c, calls = self._control()
        d, g = _fast_timers()
        with d, g:
            c._maybe_trigger_collection(LR)
            c._maybe_trigger_collection(LR)  # same cycle_count -> nothing
            await asyncio.sleep(0.2)
        self.assertEqual(calls, [])

    async def test_feeder_new_feeding_triggers(self):
        c, calls = self._control()
        d, g = _fast_timers()
        feeder = {"id": "f1", "kind": "feeder", "last_feeding": {"timestamp": "t0"}}
        with d, g:
            c._maybe_trigger_collection(feeder)  # baseline
            c._maybe_trigger_collection({**feeder, "last_feeding": {"timestamp": "t1"}})
            await asyncio.sleep(0.2)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
