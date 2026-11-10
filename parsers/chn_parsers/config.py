"""
Runtime configuration, read once from environment variables.

Every setting has a safe default so ``python -m chn_parsers run --dry-run``
works on a fresh laptop without any setup. In Docker / Cloud Run the values
come from ``.env`` / service configuration (see ``.env.example``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["Settings", "load_settings"]

DEFAULT_USER_AGENT = (
    "CampusHeilbronnEventsBot/0.1 "
    "(+https://github.com/your-org/CampusHeilbronnNewsParcer; student project, contact: change-me@example.com)"
)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    #: PostgreSQL connection string. Required for real runs, unused for --dry-run.
    database_url: str | None
    #: Identifies the bot to website operators. Put a real contact address here.
    user_agent: str
    #: Minimum pause between two requests to the same host (politeness).
    request_delay_seconds: float
    #: Per-request timeout.
    request_timeout_seconds: float
    #: Retries for network errors / HTTP 429 / HTTP 5xx (exponential backoff).
    max_retries: int
    #: Respect robots.txt. Only disable for local testing against fixtures.
    respect_robots_txt: bool
    #: Restrict which parsers run (keys). Empty = all registered parsers.
    enabled_parsers: list[str] = field(default_factory=list)
    #: Daemon: how often it wakes up to check whether a parser is due.
    daemon_tick_seconds: int = 60
    #: Python logging level.
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Build :class:`Settings` from the current environment."""
    return Settings(
        database_url=os.getenv("DATABASE_URL") or None,
        user_agent=os.getenv("SCRAPER_USER_AGENT") or DEFAULT_USER_AGENT,
        request_delay_seconds=_env_float("SCRAPER_REQUEST_DELAY_SECONDS", 1.5),
        request_timeout_seconds=_env_float("SCRAPER_REQUEST_TIMEOUT_SECONDS", 20.0),
        max_retries=_env_int("SCRAPER_MAX_RETRIES", 3),
        respect_robots_txt=_env_bool("SCRAPER_RESPECT_ROBOTS_TXT", True),
        enabled_parsers=_env_list("SCRAPER_ENABLED_PARSERS"),
        daemon_tick_seconds=_env_int("SCRAPER_DAEMON_TICK_SECONDS", 60),
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    )

