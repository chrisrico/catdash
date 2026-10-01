"""Configuration loaded from environment variables (and a local .env file).

In Docker, values come from the compose `env_file`; locally, python-dotenv loads
the project-root .env. Whisker credentials come from WHISKER_EMAIL/WHISKER_PASSWORD.
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


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    collect_interval_hours: float
    refresh_cooldown_minutes: int
    activity_limit: int
    weight_limit: int
    insight_days: int
    db_path: str
    port: int
    controls_enabled: bool
    # Stuck-robot watchdog (needs controls): auto-reset a Litter-Robot that has
    # sat "in use" too long, then notify if that didn't clear it. See watchdog.py.
    watchdog_enabled: bool
    watchdog_poll_minutes: int
    watchdog_reset_after_minutes: int
    watchdog_notify_after_minutes: int
    # Where a tapped notification lands (and the contact the push services see).
    dashboard_url: str

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        username=os.environ.get("WHISKER_EMAIL") or "",
        password=os.environ.get("WHISKER_PASSWORD") or "",
        collect_interval_hours=float(os.environ.get("COLLECT_INTERVAL_HOURS") or 6),
        refresh_cooldown_minutes=_int("REFRESH_COOLDOWN_MINUTES", 10),
        activity_limit=_int("ACTIVITY_LIMIT", 50000),
        weight_limit=_int("WEIGHT_LIMIT", 5000),
        insight_days=_int("INSIGHT_DAYS", 365),
        db_path=os.environ.get("DB_PATH") or "data/catdash.db",
        port=_int("PORT", 8080),
        controls_enabled=_bool("CONTROLS_ENABLED", False),
        watchdog_enabled=_bool("STUCK_WATCHDOG", True),
        watchdog_poll_minutes=max(1, _int("STUCK_WATCHDOG_POLL_MINUTES", 5)),
        watchdog_reset_after_minutes=max(1, _int("STUCK_WATCHDOG_RESET_AFTER_MINUTES", 30)),
        watchdog_notify_after_minutes=max(1, _int("STUCK_WATCHDOG_NOTIFY_AFTER_MINUTES", 30)),
        dashboard_url=(os.environ.get("DASHBOARD_URL") or "").strip(),
    )
