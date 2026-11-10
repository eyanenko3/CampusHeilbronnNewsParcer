"""
Long-running scheduler ("daemon") for Docker / any always-on host.

Each parser declares its own ``interval``. The daemon runs every parser once
at start-up, then again whenever its interval has elapsed. It is deliberately
simple - a loop and a dictionary of due times - so there is nothing to
configure and nothing that can drift out of sync.

For serverless hosting (Cloud Run Jobs + Cloud Scheduler, cron, GitHub
Actions) use ``python -m chn_parsers run`` instead: one pass, then exit.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from types import FrameType

from .base import BaseParser
from .config import Settings
from .http import HttpClient
from .runner import run_parser
from .sinks import PostgresSink

__all__ = ["Daemon"]

log = logging.getLogger(__name__)

#: When a run cannot even start (database down), try again after this delay
#: instead of waiting for the parser's full interval.
RETRY_AFTER_INFRA_ERROR_SECONDS = 300


class Daemon:
    def __init__(self, settings: Settings, parsers: dict[str, type[BaseParser]]) -> None:
        if not settings.database_url:
            raise SystemExit("DATABASE_URL is required for the daemon (use 'run --dry-run' to test without a DB).")
        if not parsers:
            raise SystemExit("No parsers selected - check SCRAPER_ENABLED_PARSERS.")
        self._settings = settings
        self._parsers = parsers
        self._next_due = {key: 0.0 for key in parsers}  # 0 = run immediately
        self._stop = threading.Event()

    def stop(self, signum: int | None = None, _frame: FrameType | None = None) -> None:
        log.info("Received %s - stopping after the current run", signal.Signals(signum).name if signum else "stop")
        self._stop.set()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)  # docker stop
        signal.signal(signal.SIGINT, self.stop)   # Ctrl+C
        log.info("Daemon started with parsers: %s", ", ".join(self._parsers))

        while not self._stop.is_set():
            for key, parser_cls in self._parsers.items():
                if self._stop.is_set():
                    break
                if time.monotonic() >= self._next_due[key]:
                    self._next_due[key] = time.monotonic() + self._run_one(parser_cls)

            sleep_for = max(0.0, min(self._next_due.values()) - time.monotonic())
            self._stop.wait(min(sleep_for, self._settings.daemon_tick_seconds))

        log.info("Daemon stopped")

    def _run_one(self, parser_cls: type[BaseParser]) -> float:
        """Run a parser; return the number of seconds until it is due again."""
        try:
            with HttpClient(self._settings) as http:
                sink = PostgresSink(self._settings.database_url)  # fresh connection per run
                try:
                    run_parser(parser_cls, http, sink)
                finally:
                    sink.close()
        except Exception:  # noqa: BLE001 - keep the daemon alive no matter what
            log.exception("[%s] could not run; retrying in %ss", parser_cls.key, RETRY_AFTER_INFRA_ERROR_SECONDS)
            return min(RETRY_AFTER_INFRA_ERROR_SECONDS, parser_cls.interval.total_seconds())
        return parser_cls.interval.total_seconds()

