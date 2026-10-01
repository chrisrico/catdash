"""Stuck-robot watchdog: a two-stage check for a Litter-Robot that stays
"in use" and never cycles.

The failure this catches: the unit reports Cat Detected (the Whisker app's "In
Use"), the cat leaves, and the cycle-delay countdown never completes — on the
LR4 this has been the weight scale reading phantom weight. The robot then sits
unusable until someone intervenes. The two stages:

  1. In use for RESET_AFTER minutes  → send the robot a reset (the API's
     `shortResetPress`, one short press of the physical Reset button, which
     cancels the countdown and returns the unit to Ready).
  2. Still/again in use NOTIFY_AFTER minutes after that reset → push a
     "check the robot" notification to every subscribed browser (push.py).

An episode is the stretch from the first "in use" observation until the robot
has been out of that state for a settle period (two polls), so a reset that
drops the unit to Ready for a minute before the scale re-triggers detection
does not start a fresh 30-minute clock and reset again forever — it reaches
stage 2 instead. The reset threshold is never below the unit's own wait time
plus a margin: resetting during a legitimate countdown would cancel a cycle
that was about to run.

State lives in memory only (a restart starts over — the polling loop will
re-detect a still-stuck unit within one threshold); what the watchdog did is
written to db.watchdog_events for the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from . import db
from .config import get_settings

logger = logging.getLogger("watchdog")

# LitterBoxStatus names (pylitterbot) that mean "a cat is (thought to be) in
# the globe and the unit is waiting to cycle".
IN_USE_STATUSES = frozenset({"CAT_DETECTED", "CAT_SENSOR_INTERRUPTED", "CAT_SENSOR_TIMING"})
# Margin over the unit's wait time below which we never reset (minutes).
WAIT_TIME_MARGIN_MINUTES = 5

NOTIFY_TITLE = "Check the Litter-Robot"


@dataclass
class RobotWatch:
    """What we know about one Litter-Robot's current episode."""

    robot_id: str
    name: str
    status: str | None = None
    in_use_since: float | None = None  # epoch seconds; None = no episode
    reset_at: float | None = None
    reset_ok: bool | None = None
    notified_at: float | None = None
    clear_since: float | None = None  # first poll out of "in use" during an episode

    def snapshot(self, now: float) -> dict[str, Any]:
        """JSON for /api/watchdog."""
        return {
            "id": self.robot_id,
            "name": self.name,
            "status": self.status,
            "in_use_since": _iso(self.in_use_since),
            "in_use_minutes": (
                round((now - self.in_use_since) / 60) if self.in_use_since else None
            ),
            "reset_at": _iso(self.reset_at),
            "reset_ok": self.reset_ok,
            "notified_at": _iso(self.notified_at),
        }

    def end_episode(self) -> None:
        self.in_use_since = self.reset_at = self.notified_at = self.clear_since = None
        self.reset_ok = None


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _minutes(seconds: float) -> int:
    return round(seconds / 60)


class Watchdog:
    """One instance per process; `tick()` is the scheduled job.

    `control` is the WhiskerControl singleton (or a stand-in with the same
    `litter_robot_states()` / `run_command()` coroutines). `send_push` and
    `record` default to the real push + DB and are injectable for tests."""

    def __init__(
        self,
        control: Any,
        *,
        reset_after_minutes: int,
        notify_after_minutes: int,
        poll_minutes: int,
        send_push: Callable[..., Awaitable[Any]] | None = None,
        record: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._control = control
        self.reset_after = reset_after_minutes * 60
        self.notify_after = notify_after_minutes * 60
        # Out of "in use" for two polls = the episode is over.
        self.settle = 2 * poll_minutes * 60
        self._send_push = send_push or _send_push
        self._record = record or _record
        self._clock = clock
        self._watches: dict[str, RobotWatch] = {}
        self._lock = asyncio.Lock()
        self.last_tick: float | None = None
        self.last_error: str | None = None

    # --- Public -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "reset_after_minutes": self.reset_after // 60,
            "notify_after_minutes": self.notify_after // 60,
            "last_tick": _iso(self.last_tick),
            "last_error": self.last_error,
            "robots": {rid: w.snapshot(now) for rid, w in self._watches.items()},
        }

    async def tick(self) -> None:
        """Poll every Litter-Robot once and advance each one's episode."""
        if self._lock.locked():
            return  # a slow poll is still running; don't stack another
        async with self._lock:
            try:
                states = await self._control.litter_robot_states()
            except Exception as exc:  # noqa: BLE001 - never crash the scheduler
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("watchdog poll failed: %s", self.last_error)
                return
            self.last_error = None
            self.last_tick = self._clock()
            for st in states:
                try:
                    await self._observe(st)
                except Exception:  # noqa: BLE001
                    logger.exception("watchdog step failed for %s", st.get("name"))

    # --- The state machine --------------------------------------------------

    def _in_use(self, st: dict[str, Any]) -> bool:
        if not st.get("online") or not st.get("power_on"):
            return False
        # Sleep mode defers cycles on purpose; a cat-detect that lingers through
        # the sleep window is the unit working as designed.
        if st.get("is_sleeping"):
            return False
        return st.get("status") in IN_USE_STATUSES

    def _reset_threshold(self, st: dict[str, Any]) -> float:
        wait = st.get("wait_time_minutes") or 0
        return max(self.reset_after, (wait + WAIT_TIME_MARGIN_MINUTES) * 60)

    async def _observe(self, st: dict[str, Any]) -> None:
        now = self._clock()
        watch = self._watches.setdefault(
            st["id"], RobotWatch(robot_id=st["id"], name=st.get("name") or st["id"])
        )
        watch.name = st.get("name") or watch.name
        watch.status = st.get("status")

        if not self._in_use(st):
            if watch.in_use_since is None:
                return
            if watch.clear_since is None:
                watch.clear_since = now
                return
            if now - watch.clear_since >= self.settle:
                self._finish(watch, now)
            return

        # In use.
        watch.clear_since = None
        if watch.in_use_since is None:
            watch.in_use_since = now
            logger.info("%s is in use (%s); watching", watch.name, watch.status)
            return

        elapsed = now - watch.in_use_since
        if watch.reset_at is None:
            if elapsed >= self._reset_threshold(st):
                await self._reset(watch, elapsed)
            return

        if watch.notified_at is None and now - watch.reset_at >= self.notify_after:
            await self._notify(watch, elapsed)

    async def _reset(self, watch: RobotWatch, elapsed: float) -> None:
        mins = _minutes(elapsed)
        logger.warning("%s has been in use for %d min; sending reset", watch.name, mins)
        try:
            result = await self._control.run_command(watch.robot_id, "reset", {})
            ok = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
            detail = f"in use for {mins} min; reset {'accepted' if ok else 'refused'}"
        except Exception as exc:  # noqa: BLE001 - a failed reset still moves to stage 2
            ok = False
            detail = f"in use for {mins} min; reset failed: {type(exc).__name__}: {exc}"[:200]
            logger.warning("reset for %s failed: %s", watch.name, exc)
        # The clock for stage 2 starts now even when the command failed: the
        # point of stage 2 is a human looking at the unit.
        watch.reset_at = self._clock()
        watch.reset_ok = ok
        self._record(robot_id=watch.robot_id, robot_name=watch.name, event="reset", detail=detail)

    async def _notify(self, watch: RobotWatch, elapsed: float) -> None:
        mins = _minutes(elapsed)
        reset_mins = _minutes(self._clock() - (watch.reset_at or self._clock()))
        if watch.reset_ok:
            how = f"a reset {reset_mins} min ago didn't clear it"
        else:
            how = f"the reset {reset_mins} min ago failed"
        body = (
            f"{watch.name} has shown {_status_label(watch.status)} for {mins} minutes "
            f"and {how}. It may need a power cycle or a scale re-zero."
        )
        logger.warning("%s still stuck after reset; notifying: %s", watch.name, body)
        watch.notified_at = self._clock()
        self._record(robot_id=watch.robot_id, robot_name=watch.name, event="notified", detail=body)
        try:
            counts = await self._send_push(
                title=NOTIFY_TITLE, body=body, tag=f"stuck-{watch.robot_id[:12]}"
            )
            logger.info("stuck notification result: %s", counts)
        except Exception:  # noqa: BLE001
            logger.exception("stuck notification failed")

    def _finish(self, watch: RobotWatch, now: float) -> None:
        if watch.in_use_since is None:
            return
        total = _minutes((watch.clear_since or now) - watch.in_use_since)
        if watch.reset_at is not None:
            what = "after the reset" if watch.notified_at is None else "after the notification"
            detail = f"cleared {what}; in use for {total} min"
            logger.info("%s cleared %s (%d min)", watch.name, what, total)
            self._record(
                robot_id=watch.robot_id, robot_name=watch.name, event="cleared", detail=detail
            )
        else:
            logger.info("%s cycled normally after %d min", watch.name, total)
        watch.end_episode()


def _status_label(status: str | None) -> str:
    return {
        "CAT_DETECTED": "Cat Detected",
        "CAT_SENSOR_INTERRUPTED": "Cat Sensor Interrupted",
        "CAT_SENSOR_TIMING": "Cat Sensor Timing",
    }.get(status or "", status or "in use")


async def _send_push(**kwargs: Any) -> dict:
    """The real push, off the event loop (pywebpush is blocking)."""
    from . import push

    return await asyncio.to_thread(push.send_to_all, **kwargs)


def _record(**kwargs: Any) -> None:
    try:
        db.insert_watchdog_event(**kwargs)
    except Exception:  # noqa: BLE001 - the DB is a record, not the mechanism
        logger.exception("could not record watchdog event")


def build_from_settings(control: Any) -> Watchdog:
    s = get_settings()
    return Watchdog(
        control,
        reset_after_minutes=s.watchdog_reset_after_minutes,
        notify_after_minutes=s.watchdog_notify_after_minutes,
        poll_minutes=s.watchdog_poll_minutes,
    )
