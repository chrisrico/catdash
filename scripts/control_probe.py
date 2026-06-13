"""Probe live robot state + (optionally) the control command path.

Run with:  uv run python scripts/control_probe.py
           uv run python scripts/control_probe.py --noop    # safe round-trip
           uv run python scripts/control_probe.py --snack   # dispenses a snack!

Reads credentials from .env (WHISKER_EMAIL / WHISKER_PASSWORD). Connects with
pylitterbot (the same client the app uses — no app emulator needed), dumps the
exact live state of every robot via the app's own serializers, and lists which
control actions are available. With --noop it issues a genuinely-safe command
(sets the wait time to its CURRENT value) to prove the command path end-to-end
without changing anything. Read-only by default.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from pprint import pprint

# Running `python scripts/control_probe.py` puts scripts/ on sys.path, not the
# repo root, so make the catdash package importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from pylitterbot import Account

from catdash.control import (
    _is_feeder,
    _is_litter_robot,
    _serialize_feeder,
    _serialize_litter_robot,
)


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--noop",
        action="store_true",
        help="set each litter robot's wait time to its current value (safe no-op)",
    )
    parser.add_argument(
        "--snack",
        action="store_true",
        help="dispense one snack from each feeder (NOT a no-op — feeds your cat!)",
    )
    args = parser.parse_args()

    load_dotenv()
    username = os.environ.get("WHISKER_EMAIL")
    password = os.environ.get("WHISKER_PASSWORD")
    if not username or not password:
        raise SystemExit("Missing WHISKER_EMAIL / WHISKER_PASSWORD in .env")

    account = Account()
    try:
        print(f"Connecting as {username} ...")
        await account.connect(username=username, password=password, load_robots=True)
        print(f"Connected. {len(account.robots)} robot(s) on the account.")

        for robot in account.robots:
            section(f"{type(robot).__name__}  name={robot.name!r}  id={robot.id!r}")
            if _is_litter_robot(robot):
                try:
                    fw = await robot.has_firmware_update()
                except Exception as exc:  # noqa: BLE001
                    print(f"  (firmware check failed: {exc!r})")
                    fw = False
                pprint(_serialize_litter_robot(robot, firmware_update_available=fw))
            elif _is_feeder(robot):
                pprint(_serialize_feeder(robot))
            else:
                print(f"  (unrecognized robot type: {type(robot).__name__})")

        if args.noop:
            section("SAFE COMMAND ROUND-TRIP (--noop)")
            for robot in account.robots:
                if _is_litter_robot(robot):
                    current = robot.clean_cycle_wait_time_minutes
                    print(f"- {robot.name}: set_wait_time({current})  [unchanged]")
                    ok = await robot.set_wait_time(current)
                    print(f"    -> ok={ok}")

        if args.snack:
            section("GIVE SNACK (--snack)")
            for robot in account.robots:
                if _is_feeder(robot):
                    print(f"- {robot.name}: give_snack()")
                    ok = await robot.give_snack()
                    print(f"    -> ok={ok}")
    finally:
        await account.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    asyncio.run(main())
