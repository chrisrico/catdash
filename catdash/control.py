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

from pylitterbot import Account
from pylitterbot.enums import BrightnessLevel, NightLightMode
from pylitterbot.exceptions import InvalidCommandException

from .config import get_settings

logger = logging.getLogger("control")


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
    except (KeyError, ValueError, InvalidCommandException) as exc:
        raise ControlError(str(exc) or type(exc).__name__) from exc
    raise ControlError(f"Unknown feeder action: {action!r}")


class WhiskerControl:
    """Owns the single reused Account and serializes access to it."""

    def __init__(self) -> None:
        self._account: Account | None = None
        self._lock = asyncio.Lock()

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
        if self._account is not None:
            try:
                await self._account.disconnect()
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
            if ok:
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
