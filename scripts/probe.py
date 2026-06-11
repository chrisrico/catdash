"""One-shot probe to verify the pylitterbot API shape against a real account.

Run with:  uv run python scripts/probe.py

Reads credentials from .env (keys: username, password). Prints pets, per-pet
weight history, robot activity history, and insights so we can confirm exact
field shapes before wiring up the collector. Does NOT write anything.
"""

import asyncio
import os
from collections import Counter

from dotenv import load_dotenv
from pylitterbot import Account


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def main() -> None:
    load_dotenv()
    username = os.environ.get("WHISKER_EMAIL")
    password = os.environ.get("WHISKER_PASSWORD")
    if not username or not password:
        raise SystemExit("Missing username/password in .env")

    account = Account()
    try:
        print(f"Connecting as {username} ...")
        await account.connect(
            username=username,
            password=password,
            load_robots=True,
            load_pets=True,
        )
        print("Connected.")

        # -------- Pets + per-pet weight history --------
        section(f"PETS ({len(account.pets)})")
        for pet in account.pets:
            print(
                f"- id={pet.id!r} name={pet.name!r} "
                f"weight={getattr(pet, 'weight', None)} "
                f"last_weight_reading={getattr(pet, 'last_weight_reading', None)} "
                f"estimated_weight={getattr(pet, 'estimated_weight', None)}"
            )
            try:
                history = await pet.fetch_weight_history(limit=200)
                print(f"  weight_history: {len(history)} measurements")
                for m in history[:5]:
                    print(
                        f"    timestamp={getattr(m, 'timestamp', None)!r} "
                        f"weight={getattr(m, 'weight', None)!r} "
                        f"attrs={list(vars(m).keys()) if hasattr(m, '__dict__') else m}"
                    )
                if history:
                    ts = [m.timestamp for m in history]
                    print(f"  date range: {min(ts)}  ->  {max(ts)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  !! weight history failed: {exc!r}")

        # -------- Robots: activity history + insights --------
        section(f"ROBOTS ({len(account.robots)})")
        for robot in account.robots:
            print(f"- {type(robot).__name__} name={robot.name!r} serial={robot.serial!r}")

            if hasattr(robot, "get_activity_history"):
                try:
                    acts = await robot.get_activity_history(limit=200)
                    print(f"  activity_history: {len(acts)} entries")
                    actions = Counter(str(a.action) for a in acts)
                    print("  distinct actions:")
                    for action, count in actions.most_common():
                        print(f"    {count:4d}  {action}")
                    print("  sample (first 5):")
                    for a in acts[:5]:
                        print(f"    {a.timestamp!r}  action={a.action!r}  str={str(a)!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! activity history failed: {exc!r}")

            if hasattr(robot, "get_insight"):
                try:
                    insight = await robot.get_insight(days=30)
                    print(
                        f"  insight: total_cycles={insight.total_cycles} "
                        f"average_cycles={insight.average_cycles} "
                        f"days={len(insight.cycle_history)}"
                    )
                    for day, cycles in insight.cycle_history[:5]:
                        print(f"    {day}  cycles={cycles}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! insight failed: {exc!r}")
    finally:
        await account.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    asyncio.run(main())
