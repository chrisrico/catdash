"""The stuck-robot watchdog's two stages, driven by a fake clock and a fake
control layer: reset after the threshold, notify if still stuck after the
reset, and an episode that ends only once the unit has settled."""

from __future__ import annotations

import asyncio

import pytest

from catdash.watchdog import Watchdog

RESET_AFTER = 30
NOTIFY_AFTER = 30
POLL = 5


class FakeControl:
    def __init__(self, status: str = "READY", **extra):
        self.status = status
        self.extra = {"online": True, "power_on": True, "is_sleeping": False, "wait_time_minutes": 15}
        self.extra.update(extra)
        self.commands: list[tuple[str, str]] = []
        self.reset_ok = True
        self.fail_poll = False

    async def litter_robot_states(self):
        if self.fail_poll:
            raise RuntimeError("whisker down")
        return [{"id": "r1", "name": "Poodini", "status": self.status, **self.extra}]

    async def run_command(self, robot_id, action, value):
        self.commands.append((robot_id, action))
        return {"ok": self.reset_ok, "robot": {}}


class Harness:
    def __init__(self, control: FakeControl):
        self.now = 1_000_000.0
        self.pushes: list[dict] = []
        self.events: list[dict] = []
        self.control = control

        async def send_push(**kw):
            self.pushes.append(kw)
            return {"sent": 1, "failed": 0, "gone": 0}

        self.wd = Watchdog(
            control,
            reset_after_minutes=RESET_AFTER,
            notify_after_minutes=NOTIFY_AFTER,
            poll_minutes=POLL,
            send_push=send_push,
            record=lambda **kw: self.events.append(kw),
            clock=lambda: self.now,
        )

    def tick(self, minutes_later: float = POLL):
        """Advance the clock and poll once (every call is one scheduled run)."""
        self.now += minutes_later * 60
        asyncio.run(self.wd.tick())

    def watch(self):
        return self.wd.state()["robots"]["r1"]


@pytest.fixture
def h():
    return Harness(FakeControl())


def test_a_normal_visit_never_triggers_anything(h):
    h.control.status = "CAT_DETECTED"
    for _ in range(4):  # 20 minutes in use: a long but ordinary visit + countdown
        h.tick()
    h.control.status = "CLEAN_CYCLE"
    h.tick()
    h.control.status = "READY"
    for _ in range(3):
        h.tick()
    assert h.control.commands == []
    assert h.pushes == [] and h.events == []
    assert h.watch()["in_use_since"] is None


def test_stage_one_resets_after_the_threshold(h):
    h.control.status = "CAT_DETECTED"
    h.tick()  # first seen in use: the clock starts here
    for _ in range(5):  # 25 min in use
        h.tick()
    assert h.control.commands == []
    h.tick()  # 30 min
    assert h.control.commands == [("r1", "reset")]
    assert h.events[-1]["event"] == "reset" and "accepted" in h.events[-1]["detail"]
    assert h.watch()["reset_at"] is not None and h.watch()["reset_ok"] is True
    # Only one reset per episode.
    h.tick()
    assert len(h.control.commands) == 1


def test_stage_two_notifies_when_still_stuck_after_the_reset(h):
    h.control.status = "CAT_DETECTED"
    for _ in range(7):  # reset fires on the 7th poll (30 min after first seen)
        h.tick()
    assert len(h.control.commands) == 1 and h.pushes == []
    for _ in range(5):  # 25 min after the reset
        h.tick()
    assert h.pushes == []
    h.tick()  # 30 min after the reset
    assert len(h.pushes) == 1
    push = h.pushes[0]
    assert push["title"] == "Check the Litter-Robot"
    assert "Poodini" in push["body"] and "Cat Detected" in push["body"] and "60 minutes" in push["body"]
    assert "didn't clear it" in push["body"]
    assert h.events[-1]["event"] == "notified"
    # Notified once; no more resets or pushes while it stays stuck.
    for _ in range(12):
        h.tick()
    assert len(h.pushes) == 1 and len(h.control.commands) == 1


def test_a_brief_clear_after_the_reset_does_not_restart_the_clock(h):
    """The scale-drift case: the reset drops the unit to Ready for one poll,
    then detection re-triggers. That is the same episode — stage 2 follows,
    rather than a fresh 30-minute wait and another reset."""
    h.control.status = "CAT_DETECTED"
    for _ in range(7):
        h.tick()
    assert len(h.control.commands) == 1
    h.control.status = "READY"
    h.tick()  # one poll clear (less than the settle period)
    h.control.status = "CAT_DETECTED"
    for _ in range(5):
        h.tick()
    assert len(h.control.commands) == 1  # no second reset
    assert len(h.pushes) == 1  # stage 2 happened on schedule


def test_a_settled_clear_ends_the_episode(h):
    h.control.status = "CAT_DETECTED"
    for _ in range(7):
        h.tick()
    h.control.status = "READY"
    h.tick()  # first poll out of use: the settle clock starts
    h.tick()
    assert h.watch()["reset_at"] is not None  # 5 min clear: not settled yet
    h.tick()  # 10 min clear (two poll intervals) = settled
    assert h.events[-1]["event"] == "cleared" and "after the reset" in h.events[-1]["detail"]
    assert h.watch()["in_use_since"] is None and h.watch()["reset_at"] is None
    assert h.pushes == []
    # A later episode gets its own reset.
    h.control.status = "CAT_DETECTED"
    for _ in range(7):
        h.tick()
    assert len(h.control.commands) == 2


def test_a_failed_reset_still_leads_to_the_notification(h):
    h.control.status = "CAT_DETECTED"
    h.control.reset_ok = False
    for _ in range(7):
        h.tick()
    assert h.watch()["reset_ok"] is False
    assert "refused" in h.events[-1]["detail"]
    for _ in range(6):
        h.tick()
    assert len(h.pushes) == 1 and "failed" in h.pushes[0]["body"]


def test_the_reset_threshold_never_undercuts_the_wait_time(h):
    """A 30-minute wait time plus margin beats the configured 30 minutes:
    resetting during a legitimate countdown would cancel a cycle."""
    h.control.extra["wait_time_minutes"] = 30
    h.control.status = "CAT_DETECTED"
    for _ in range(7):  # 30 min
        h.tick()
    assert h.control.commands == []
    h.tick()  # 35 min
    assert h.control.commands == [("r1", "reset")]


@pytest.mark.parametrize("field,value", [("online", False), ("power_on", False), ("is_sleeping", True)])
def test_offline_off_or_sleeping_units_are_not_in_use(h, field, value):
    h.control.extra[field] = value
    h.control.status = "CAT_DETECTED"
    for _ in range(10):
        h.tick()
    assert h.control.commands == [] and h.watch()["in_use_since"] is None


def test_other_in_use_statuses_count(h):
    h.control.status = "CAT_SENSOR_INTERRUPTED"
    for _ in range(7):
        h.tick()
    assert h.control.commands == [("r1", "reset")]


def test_a_failed_poll_is_reported_and_does_not_advance_anything(h):
    h.control.status = "CAT_DETECTED"
    h.tick()
    h.control.fail_poll = True
    for _ in range(10):
        h.tick()
    assert h.wd.state()["last_error"].startswith("RuntimeError")
    assert h.control.commands == []
    h.control.fail_poll = False
    h.tick()
    assert h.wd.state()["last_error"] is None
    # The episode clock kept running through the outage, so the reset is due.
    assert h.control.commands == [("r1", "reset")]
