"""READ-ONLY observer: watch robot state while you tap "Sync time" in the app.

This sends NOTHING to your robots. It connects with your Whisker credentials,
opens the same live WebSocket the app uses, snapshots each robot's full state,
and then prints every field that changes — live — plus a periodic refresh in
case a change isn't pushed over the socket.

Use it to discover what the app's time-sync actually does to the device:

  1. uv run python scripts/probe_time_observe.py
  2. Wait for "Baseline captured — now watching."
  3. On your phone, open the Whisker app and tap Sync (or just open the robot).
  4. Watch this terminal for changed fields. Time-related keys are flagged ⏰.
  5. Ctrl-C when done and paste the output back.

Fields flagged ⏰ (unitTime / timezone / sleep / schedule / updated_at ...) are
the ones that matter for the daylight-saving sync.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from pylitterbot import Account
from pylitterbot.event import EVENT_UPDATE

# Flush every line immediately so output is visible live even when piped/redirected.
sys.stdout.reconfigure(line_buffering=True)

# Same credential resolution as catdash/config.py.
load_dotenv()
USERNAME = os.environ.get("username") or os.environ.get("WHISKER_EMAIL") or ""
PASSWORD = os.environ.get("password") or os.environ.get("WHISKER_PASSWORD") or ""

POLL_SECONDS = 4
TIMEY = re.compile(r"time|tz|zone|offset|clock|dst|sleep|sync|schedule|wake|updated", re.I)

# serial -> flattened snapshot of the last seen state
_last: dict[str, dict[str, object]] = {}


def _flatten(obj: object, prefix: str = "") -> dict[str, object]:
    """Flatten nested dicts/lists to dotted paths for easy diffing."""
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def report_if_changed(robot: object, source: str) -> None:
    """Diff the robot's current state against the last snapshot; print changes."""
    serial = getattr(robot, "serial", robot.name)
    current = _flatten(dict(robot._data))  # noqa: SLF001
    previous = _last.get(serial)
    if previous is None:
        _last[serial] = current
        return

    keys = sorted(set(previous) | set(current))
    changes = []
    for k in keys:
        before = previous.get(k, "<absent>")
        after = current.get(k, "<absent>")
        if before != after:
            flag = "⏰" if TIMEY.search(k) else "  "
            changes.append(f"  {flag} {k}: {before!r} -> {after!r}")

    if changes:
        print(f"\n[{_stamp()}] {robot.name} changed ({source}):")
        print("\n".join(changes))
    _last[serial] = current


async def main() -> None:
    if not (USERNAME and PASSWORD):
        raise SystemExit("No credentials: set username/password (or WHISKER_*) in .env")

    account = Account()
    try:
        print("Connecting (read-only; nothing is sent to the robots)…")
        await account.connect(
            username=USERNAME, password=PASSWORD,
            load_robots=True, subscribe_for_updates=True,
        )

        for robot in account.robots:
            serial = getattr(robot, "serial", robot.name)
            _last[serial] = _flatten(dict(robot._data))  # noqa: SLF001
            # Live push notifications from the same socket the app uses.
            robot.on(EVENT_UPDATE, lambda r=robot: report_if_changed(r, "live"))

        print(f"\nBaseline captured for {len(account.robots)} robot(s). Time-ish fields now:")
        for robot in account.robots:
            timey = {k: v for k, v in _flatten(dict(robot._data)).items() if TIMEY.search(k)}
            print(f"  {robot.name}: {timey}")

        print(
            "\n=== Now open the Whisker app on your phone and tap Sync / open the robot. ===\n"
            "Watching for changes (⏰ = time-related). Press Ctrl-C to stop.\n"
        )

        while True:
            await asyncio.sleep(POLL_SECONDS)
            for robot in account.robots:
                try:
                    await robot.refresh()  # forces a re-read in case nothing was pushed
                    report_if_changed(robot, "poll")
                except Exception as exc:  # noqa: BLE001
                    print(f"[{_stamp()}] refresh failed for {robot.name}: {exc!r}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
    finally:
        await account.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
