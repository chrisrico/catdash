"""Probe the Feeder-Robot to discover the feeding-history data shape.

Run: uv run python scripts/probe_feeder.py
"""

import asyncio
import json
import os

from dotenv import load_dotenv
from pylitterbot import Account


async def main() -> None:
    load_dotenv()
    account = Account()
    try:
        await account.connect(
            username=os.environ["WHISKER_EMAIL"],
            password=os.environ["WHISKER_PASSWORD"],
            load_robots=True,
        )
        for robot in account.robots:
            if type(robot).__name__ != "FeederRobot":
                continue
            print(f"FeederRobot name={robot.name!r} serial={robot.serial!r}")
            print(f"  food_level={robot.food_level}")
            print(f"  is_online={robot.is_online}")
            print(f"  last_feeding={robot.last_feeding}")
            print(f"  last_meal={getattr(robot, 'last_meal', None)}")
            print(f"  last_snack={getattr(robot, 'last_snack', None)}")
            print(f"  next_feeding={getattr(robot, 'next_feeding', None)}")

            data = robot._data  # noqa: SLF001
            print(f"  _data top-level keys: {sorted(data.keys())}")
            for key in ("feeding_meal", "feeding_snack"):
                arr = data.get(key)
                if isinstance(arr, list):
                    print(f"  {key}: {len(arr)} entries")
                    for entry in arr[:3]:
                        print(f"    {json.dumps(entry, default=str)}")
    finally:
        await account.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
