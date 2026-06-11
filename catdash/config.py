"""Configuration loaded from environment variables (and a local .env file).

In Docker, values come from the compose `env_file`; locally, python-dotenv loads
the project-root .env. Whisker credentials use the `username`/`password` keys to
match the existing .env, with WHISKER_EMAIL/WHISKER_PASSWORD as fallbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    collect_interval_hours: float
    activity_limit: int
    weight_limit: int
    insight_days: int
    db_path: str
    port: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        username=os.environ.get("WHISKER_EMAIL") or "",
        password=os.environ.get("WHISKER_PASSWORD") or "",
        collect_interval_hours=float(os.environ.get("COLLECT_INTERVAL_HOURS") or 6),
        activity_limit=_int("ACTIVITY_LIMIT", 1000),
        weight_limit=_int("WEIGHT_LIMIT", 500),
        insight_days=_int("INSIGHT_DAYS", 30),
        db_path=os.environ.get("DB_PATH") or "data/catdash.db",
        port=_int("PORT", 8080),
    )
