"""Live status + remote control of the robots via a shared pylitterbot session.

Unlike collector.py — which connects and disconnects on every scheduled run —
this module keeps ONE lazily-connected `Account`, reused across requests and
serialized by an `asyncio.Lock`, so a command doesn't trigger a fresh Whisker
login each time (the same login-spam concern the refresh cooldown guards). The
session refreshes its own auth token on each request; if that ever fails hard
(refresh token expired, socket dropped) we drop the account and reconnect once.

This whole surface is gated by CONTROLS_ENABLED at the endpoint layer — by the
time anything here runs, the flag has already been checked.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from pylitterbot import Account
from pylitterbot.enums import BrightnessLevel, NightLightMode
from pylitterbot.event import EVENT_UPDATE
from pylitterbot.exceptions import InvalidCommandException

from .config import get_settings

logger = logging.getLogger("control")

# When a live websocket update implies new activity-history rows — a Litter-Robot
# finished a clean cycle, a Feeder dispensed a meal — pull them in soon instead of
# waiting for the next scheduled collection. That's what fills in "today" while a
# dashboard is open. A single cycle emits a burst of updates, so the collection is
# debounced to the trailing edge and floored so a busy box can't collect on every
# visit.
_STREAM_COLLECT_DEBOUNCE = 45.0  # seconds of quiet after the last signal
_STREAM_COLLECT_MIN_GAP = 120.0  # minimum seconds between stream-triggered runs

# Distinguishes "no signal recorded yet" from a real (possibly falsy) signal.
_UNSET = object()


class RobotNotFound(Exception):
    """No robot on the account matches the requested id (maps to HTTP 404)."""


class ControlError(Exception):
    """A command was malformed or rejected by the robot (maps to HTTP 400)."""


class ControlUnavailable(Exception):
    """Controls can't run right now — no credentials, or login failed (HTTP 502)."""


# Exceptions that are about the *request*, not the connection: re-raise them
# straight to the caller instead of reconnecting and retrying.
_NO_RETRY = (RobotNotFound, ControlError)


def _is_litter_robot(robot: Any) -> bool:
    """A Litter-Robot exposes activity history; a Feeder-Robot does not."""
    return hasattr(robot, "get_activity_history")


def _is_feeder(robot: Any) -> bool:
    return hasattr(robot, "food_level")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# Mon-first ordering for displaying a meal's repeat days consistently.
_WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _serialize_schedule(robot: Any) -> dict | None:
    """The Feeder-Robot's active feeding schedule.

    Surfaces each meal's `meal_number` so the client can target it for the
    skip/pause writes exposed by `WhiskerControl`.
    """
    sched = robot.active_schedule or {}
    raw_meals = sched.get("meals") or []
    insert = robot.meal_insert_size or 0
    meals = []
    for meal in raw_meals:
        if not isinstance(meal, dict):
            continue
        days = [d for d in _WEEKDAY_ORDER if d in (meal.get("days") or [])]
        portions = meal.get("portions")
        skip = meal.get("skip")
        # The "no skip" sentinel is a year-0000 date; treat anything else as a
        # real skip date and let the client decide if it's still upcoming.
        skip_date = (
            skip[:10] if isinstance(skip, str) and not skip.startswith("0000") else None
        )
        meals.append(
            {
                "meal_number": meal.get("mealNumber"),
                "name": meal.get("name"),
                "hour": meal.get("hour"),
                "minute": meal.get("minute"),
                "days": days,
                "every_day": len(days) == 7,
                "portions": portions,
                "cups": round(portions * insert, 4) if portions and insert else None,
                "paused": bool(meal.get("paused")),
                "skip": skip_date,
            }
        )
    meals.sort(key=lambda m: ((m["hour"] or 0), (m["minute"] or 0)))
    return {"name": sched.get("name"), "meals": meals}


def _fed_meal_numbers_today(robot: Any) -> list[int]:
    """Meal numbers that have actually dispensed today, from the feeder's real
    feeding history (`feeding_meal` is filtered server-side to dispensed events in
    the last 24h). Used to cross off meals that have been fed, rather than guessing
    from the clock — a meal that failed to fire simply won't appear here.
    """
    try:
        tz: ZoneInfo | None = ZoneInfo(robot.timezone)
    except Exception:  # noqa: BLE001 - missing/invalid tz; fall back to naive local
        tz = None
    today = datetime.now(tz).date()
    fed: set[int] = set()
    for rec in robot.to_dict().get("feeding_meal") or []:
        if not isinstance(rec, dict):
            continue
        number, ts = rec.get("meal_number"), rec.get("timestamp")
        if number is None or not ts:
            continue
        try:
            when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is not None and tz is not None:
            when = when.astimezone(tz)
        if when.date() == today:
            fed.add(int(number))
    return sorted(fed)


def _serialize_litter_robot(robot: Any, *, firmware_update_available: bool) -> dict:
    """A JSON-safe live snapshot of a Litter-Robot 4 for the dashboard."""
    night_light_mode = robot.night_light_mode
    panel_brightness = robot.panel_brightness
    return {
        "id": robot.id,
        "kind": "litter_robot",
        "model": robot.model,
        "name": robot.name,
        "serial": robot.serial,
        "online": robot.is_online,
        "power_on": robot.is_on,
        "status": robot.status.name,
        "status_text": robot.status_text,
        "litter_level": round(robot.litter_level),
        "waste_drawer_level": round(robot.waste_drawer_level),
        "is_drawer_full": robot.is_waste_drawer_full,
        "cycle_count": robot.cycle_count,
        "cycle_capacity": robot.cycle_capacity,
        "pet_weight_lbs": robot.pet_weight,
        "wait_time_minutes": robot.clean_cycle_wait_time_minutes,
        "valid_wait_times": robot.VALID_WAIT_TIMES,
        "night_light_mode": night_light_mode.value if night_light_mode else None,
        "night_light_brightness": robot.night_light_brightness,
        "valid_brightness_levels": [level.value for level in BrightnessLevel],
        "panel_lock_enabled": robot.panel_lock_enabled,
        "panel_brightness": panel_brightness.name if panel_brightness else None,
        "is_sleeping": robot.is_sleeping,
        "sleep_mode_enabled": robot.sleep_mode_enabled,
        "sleep_mode_start": _iso(robot.sleep_mode_start_time),
        "sleep_mode_end": _iso(robot.sleep_mode_end_time),
        "power_type": robot.power_type,
        "hopper_status": (hs.value if (hs := robot.hopper_status) else None),
        "is_hopper_removed": robot.is_hopper_removed,
        "scoops_saved": robot.scoops_saved_count,
        "firmware": robot.firmware,
        "firmware_update_available": firmware_update_available,
        "faults": {
            "globe_motor": robot.globe_motor_fault_status.value,
            "usb": (uf.value if (uf := robot.usb_fault_status) else None),
            "wifi": (wf.value if (wf := robot.wifi_mode_status) else None),
        },
    }


def _serialize_feeder(robot: Any) -> dict:
    """A JSON-safe live snapshot of a Feeder-Robot for the dashboard."""
    last = robot.last_feeding
    return {
        "id": robot.id,
        "kind": "feeder",
        "model": robot.model,
        "name": robot.name,
        "serial": robot.serial,
        "online": robot.is_online,
        "power_on": robot.is_on,
        "food_level": robot.food_level,
        "gravity_mode_enabled": robot.gravity_mode_enabled,
        "night_light_enabled": robot.night_light_mode_enabled,
        "panel_lock_enabled": robot.panel_lock_enabled,
        "meal_insert_size": robot.meal_insert_size,
        "valid_meal_insert_sizes": sorted(robot.VALID_MEAL_INSERT_SIZES),
        "power_type": robot.power_type,
        "next_feeding": _iso(robot.next_feeding),
        "schedule": _serialize_schedule(robot),
        "fed_meal_numbers": _fed_meal_numbers_today(robot),
        "last_feeding": (
            {
                "timestamp": _iso(last.get("timestamp")),
                "amount_cups": last.get("amount"),
                "name": last.get("name"),
            }
            if last
            else None
        ),
    }


async def _run_litter_command(robot: Any, action: str, value: dict) -> bool:
    """Dispatch a control action to a Litter-Robot. Returns the robot's success
    flag. Raises ControlError for a malformed/invalid request."""
    try:
        if action == "clean":
            return await robot.start_cleaning()
        if action == "power":
            return await robot.set_power_status(bool(value["on"]))
        if action == "wait_time":
            return await robot.set_wait_time(int(value["minutes"]))
        if action == "night_light":
            ok = await robot.set_night_light_mode(NightLightMode(value["mode"]))
            if ok and value.get("brightness") is not None:
                ok = await robot.set_night_light_brightness(int(value["brightness"]))
            return ok
        if action == "panel_lock":
            return await robot.set_panel_lockout(bool(value["locked"]))
        if action == "panel_brightness":
            return await robot.set_panel_brightness(BrightnessLevel(int(value["level"])))
        if action == "name":
            return await robot.set_name(str(value["name"]))
        if action == "reset":
            return await robot.reset()
        if action == "hopper":
            return await robot.toggle_hopper(bool(value["removed"]))
        if action == "firmware_update":
            return await robot.update_firmware()
    except (KeyError, ValueError, InvalidCommandException) as exc:
        raise ControlError(str(exc) or type(exc).__name__) from exc
    raise ControlError(f"Unknown litter-robot action: {action!r}")


async def _run_feeder_command(robot: Any, action: str, value: dict) -> bool:
    """Dispatch a control action to a Feeder-Robot."""
    try:
        if action == "snack":
            return await robot.give_snack()
        if action == "gravity_mode":
            return await robot.set_gravity_mode(bool(value["on"]))
        if action == "meal_insert_size":
            return await robot.set_meal_insert_size(float(value["cups"]))
        if action == "night_light":
            return await robot.set_night_light(bool(value["on"]))
        if action == "panel_lock":
            return await robot.set_panel_lockout(bool(value["locked"]))
        if action == "name":
            return await robot.set_name(str(value["name"]))
        if action == "skip_meal":
            return await robot.skip_meal(
                int(value["meal_number"]), skip=bool(value.get("skip", True))
            )
        if action == "pause_meal":
            return await robot.pause_meal(
                int(value["meal_number"]), paused=bool(value.get("paused", True))
            )
    except (KeyError, ValueError, InvalidCommandException) as exc:
        raise ControlError(str(exc) or type(exc).__name__) from exc
    raise ControlError(f"Unknown feeder action: {action!r}")


# Feeder schedule writes update local state optimistically; the Hasura-backed
# read lags behind the REST write, so a refresh right after would briefly report
# stale schedule state. Trust the optimistic snapshot for these actions.
_NO_REFRESH_ACTIONS = {"skip_meal", "pause_meal", "set_schedule", "clear_schedule"}


class WhiskerControl:
    """Owns the single reused Account and serializes access to it."""

    def __init__(self) -> None:
        self._account: Account | None = None
        self._lock = asyncio.Lock()
        # Live streaming: each connected SSE client gets a queue; robot
        # EVENT_UPDATE callbacks broadcast fresh snapshots into all of them.
        self._subscribers: set[asyncio.Queue] = set()
        self._streaming = False
        self._update_unsubs: list[Callable[[], None]] = []
        # has_firmware_update() is an extra network call we only make in
        # list_robots; cache it so streamed snapshots can reuse the value.
        self._fw_cache: dict[str, bool] = {}
        # Stream-triggered collection (see _maybe_trigger_collection). main injects
        # run_collection via set_collection_trigger to avoid a control<-main import
        # cycle. We remember each robot's last "new activity" signal and debounce a
        # collection when it changes.
        self._collection_trigger: Callable[[], Awaitable[Any]] | None = None
        self._collect_signals: dict[str, Any] = {}
        self._collect_task: asyncio.Task | None = None
        self._collect_due: float = 0.0
        self._collect_last_run: float = 0.0

    async def _connect(self) -> Account:
        settings = get_settings()
        if not settings.has_credentials:
            raise ControlUnavailable("Whisker credentials are not configured.")
        account = Account()
        await account.connect(
            username=settings.username,
            password=settings.password,
            load_robots=True,
        )
        self._account = account
        logger.info("control session connected (%d robots)", len(account.robots))
        return account

    async def _account_ready(self) -> Account:
        if self._account is None:
            return await self._connect()
        return self._account

    async def _reset(self) -> None:
        # Drop any pending stream-triggered collection; signals re-baseline on
        # reconnect so the first post-reconnect snapshot doesn't trigger one.
        if self._collect_task is not None and not self._collect_task.done():
            self._collect_task.cancel()
        self._collect_task = None
        self._collect_signals.clear()
        for unsub in self._update_unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._update_unsubs.clear()
        self._streaming = False
        if self._account is not None:
            try:
                await self._account.disconnect()  # also unsubscribes the websockets
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._account = None

    async def _run(self, op: Callable[[Account], Awaitable[Any]]) -> Any:
        """Run `op` against the connected account, reconnecting once on a hard
        (connection/auth) failure. Request-level errors are re-raised as-is."""
        account = await self._account_ready()
        try:
            return await op(account)
        except _NO_RETRY:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "control op failed (%s); reconnecting and retrying once",
                type(exc).__name__,
            )
            await self._reset()
            try:
                account = await self._account_ready()
                return await op(account)
            except _NO_RETRY:
                raise
            except ControlUnavailable:
                raise
            except Exception as retry_exc:  # noqa: BLE001
                raise ControlUnavailable(type(retry_exc).__name__) from retry_exc

    @staticmethod
    def _find(account: Account, robot_id: str) -> Any:
        for robot in account.robots:
            if robot.id == robot_id:
                return robot
        raise RobotNotFound(robot_id)

    async def list_robots(self) -> list[dict]:
        """A live snapshot of every Litter-Robot and Feeder on the account."""

        async def op(account: Account) -> list[dict]:
            await account.refresh_robots()
            out: list[dict] = []
            for robot in account.robots:
                if _is_litter_robot(robot):
                    try:
                        fw_available = await robot.has_firmware_update()
                    except Exception:  # noqa: BLE001 - cached, non-critical extra call
                        fw_available = False
                    self._fw_cache[robot.id] = fw_available
                    out.append(
                        _serialize_litter_robot(
                            robot, firmware_update_available=fw_available
                        )
                    )
                elif _is_feeder(robot):
                    out.append(_serialize_feeder(robot))
            return out

        async with self._lock:
            return await self._run(op)

    # --- Live streaming (Server-Sent Events) ---------------------------------

    def _snapshot_robot(self, robot: Any) -> dict | None:
        """Serialize a robot for a live push (no network — uses cached state)."""
        if _is_litter_robot(robot):
            return _serialize_litter_robot(
                robot, firmware_update_available=self._fw_cache.get(robot.id, False)
            )
        if _is_feeder(robot):
            return _serialize_feeder(robot)
        return None

    def _broadcast(self, snapshot: dict) -> None:
        """Push a snapshot to every connected SSE client."""
        item = {"robot": snapshot}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:  # pragma: no cover - queues are unbounded
                pass

    def _make_update_callback(self, robot: Any) -> Callable[[], None]:
        def _on_update() -> None:
            snapshot = self._snapshot_robot(robot)
            if snapshot is not None:
                self._broadcast(snapshot)
                self._maybe_trigger_collection(snapshot)

        return _on_update

    def set_collection_trigger(self, trigger: Callable[[], Awaitable[Any]]) -> None:
        """Register the coroutine that runs one collection. Injected by main at
        startup to avoid a control<-main import cycle; live updates that imply new
        activity history call it (debounced)."""
        self._collection_trigger = trigger

    @staticmethod
    def _collection_signal(snapshot: dict) -> Any:
        """The value that, when it changes, means the activity history has new rows
        worth collecting — None for robots/states we don't track."""
        if snapshot.get("kind") == "litter_robot":
            return snapshot.get("cycle_count")  # increments when a clean cycle completes
        if snapshot.get("kind") == "feeder":
            return (snapshot.get("last_feeding") or {}).get("timestamp")  # new meal/snack
        return None

    def _maybe_trigger_collection(self, snapshot: dict) -> None:
        """On a meaningful change for a robot, debounce a collection so today's
        activity lands without waiting for the scheduled run."""
        signal = self._collection_signal(snapshot)
        if signal is None:
            return
        prev = self._collect_signals.get(snapshot["id"], _UNSET)
        self._collect_signals[snapshot["id"]] = signal
        # Skip the first snapshot per robot (just a baseline) and no-op repeats.
        if prev is not _UNSET and signal != prev:
            self._request_collection()

    def _request_collection(self) -> None:
        """(Re)arm the trailing-edge debounce; a burst of updates collapses into
        one run once they go quiet."""
        if self._collection_trigger is None:
            return
        self._collect_due = asyncio.get_running_loop().time() + _STREAM_COLLECT_DEBOUNCE
        if self._collect_task is None or self._collect_task.done():
            self._collect_task = asyncio.create_task(self._collect_after_debounce())

    async def _collect_after_debounce(self) -> None:
        """Wait until updates go quiet AND the rate floor has passed, then run one
        collection. run_collection itself no-ops if one is already in flight."""
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            wait = max(
                self._collect_due - now,
                self._collect_last_run + _STREAM_COLLECT_MIN_GAP - now,
            )
            if wait <= 0:
                break
            await asyncio.sleep(wait)
        self._collect_last_run = loop.time()
        try:
            assert self._collection_trigger is not None
            await self._collection_trigger()
        except Exception:  # noqa: BLE001 - a trigger failure must not kill streaming
            logger.exception("stream-triggered collection failed")

    async def _ensure_streaming(self) -> None:
        """Open the per-robot WebSocket subscriptions once, lazily."""
        async with self._lock:
            if self._streaming:
                return
            self._streaming = True
            account = await self._account_ready()
            robots = [
                r for r in account.robots if _is_litter_robot(r) or _is_feeder(r)
            ]
            for robot in robots:
                self._update_unsubs.append(
                    robot.on(EVENT_UPDATE, self._make_update_callback(robot))
                )
        # Subscribe outside the lock: the WebSocket handshake can be slow and we
        # don't want to block control commands on it.
        for robot in robots:
            try:
                await robot.subscribe()
            except Exception:  # noqa: BLE001
                logger.exception("websocket subscribe failed for %s", robot.serial)

    async def stream(self):
        """Async generator of `{robot}` snapshots: the current cached state for
        each robot first (the instant fallback), then live pushes as the
        WebSocket delivers them. Heartbeats keep the connection from idling out."""
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            account = await self._account_ready()
            initial = [
                {"robot": snap}
                for robot in account.robots
                if (snap := self._snapshot_robot(robot)) is not None
            ]
            self._subscribers.add(queue)
        try:
            # Cached snapshots immediately, before the (possibly slow) WS connect.
            for item in initial:
                yield item
            await self._ensure_streaming()
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield {"heartbeat": True}
        finally:
            self._subscribers.discard(queue)

    async def run_command(self, robot_id: str, action: str, value: dict) -> dict:
        """Dispatch a command, then return the robot's post-command snapshot."""

        async def op(account: Account) -> dict:
            robot = self._find(account, robot_id)
            if _is_litter_robot(robot):
                ok = await _run_litter_command(robot, action, value)
            elif _is_feeder(robot):
                ok = await _run_feeder_command(robot, action, value)
            else:
                raise ControlError("Unsupported robot type")

            # LR4 commands don't update local state, so pull fresh data to report
            # the real post-command status (the next poll would catch it anyway).
            # Feeder schedule writes are the exception — see _NO_REFRESH_ACTIONS.
            if ok and action not in _NO_REFRESH_ACTIONS:
                try:
                    await robot.refresh()
                except Exception:  # noqa: BLE001
                    logger.exception("post-command refresh failed for %s", robot.serial)

            if _is_litter_robot(robot):
                snapshot = _serialize_litter_robot(
                    robot, firmware_update_available=False
                )
            else:
                snapshot = _serialize_feeder(robot)
            return {"ok": bool(ok), "robot": snapshot}

        async with self._lock:
            return await self._run(op)

    async def aclose(self) -> None:
        """Disconnect the shared session (called on app shutdown)."""
        async with self._lock:
            await self._reset()


# Module-level singleton reused by the endpoint layer.
control = WhiskerControl()
