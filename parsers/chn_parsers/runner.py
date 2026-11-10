"""
The runner glues a parser to a sink and decides the outcome of a run.

    parser.scrape()  --Event-->  run.write()  ...  run.finish(status, ...)

Status rules (mirrored by ingest.finish_run in SQL):

    failed     the parser raised (e.g. listing page unreachable) or too many
               consecutive database errors occurred
    partial    finished, but some items failed or the listing was incomplete
    succeeded  finished, every item stored, full listing seen
               -> only now may vanished events be soft-deleted
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .base import BaseParser
from .http import Fetcher
from .sinks import Sink

__all__ = ["RunResult", "run_parser"]

log = logging.getLogger(__name__)

#: Abort a run when the database rejects this many events in a row
#: (it is most likely down; no point in logging hundreds of identical errors).
MAX_CONSECUTIVE_WRITE_ERRORS = 5


class _TooManyWriteErrors(RuntimeError):
    pass


@dataclass(slots=True)
class RunResult:
    parser_key: str
    status: str
    events_written: int
    item_errors: int
    complete: bool
    duration_seconds: float
    error_message: str | None = None
    outcomes: Counter[str] = field(default_factory=Counter)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != "failed"

    def describe(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.outcomes.items())) or "no events"
        text = (f"[{self.parser_key}] {self.status.upper()} in {self.duration_seconds:.1f}s - "
                f"{self.events_written} events ({counts}), {self.item_errors} item errors")
        if self.summary.get("deactivated"):
            text += f", {self.summary['deactivated']} removed at source"
        if self.error_message:
            text += f" - {self.error_message}"
        return text


def run_parser(parser_cls: type[BaseParser], http: Fetcher, sink: Sink) -> RunResult:
    """Execute one parser end-to-end. Never raises for parser/item problems;
    only a failure to *open* the run (e.g. database unreachable) propagates."""
    started = time.monotonic()
    parser = parser_cls(http)
    run = sink.open_run(parser_cls)  # may raise -> caller decides (retry later)
    log.info("[%s] run started", parser_cls.key)

    outcomes: Counter[str] = Counter()
    written = write_errors = consecutive_write_errors = 0
    status = "succeeded"
    error_message: str | None = None

    try:
        for event in parser.scrape():
            try:
                outcomes[run.write(event)] += 1
                written += 1
                consecutive_write_errors = 0
            except Exception:  # noqa: BLE001 - one bad event must not stop the run
                write_errors += 1
                consecutive_write_errors += 1
                log.exception("[%s] could not store event %s", parser_cls.key, event.external_id)
                if consecutive_write_errors >= MAX_CONSECUTIVE_WRITE_ERRORS:
                    raise _TooManyWriteErrors(
                        f"{consecutive_write_errors} consecutive storage errors - aborting run"
                    ) from None
    except Exception as exc:  # noqa: BLE001 - convert into a 'failed' run record
        status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        log.exception("[%s] run failed", parser_cls.key)

    item_errors = parser.stats.item_errors + write_errors
    if status != "failed" and (item_errors or not parser.stats.complete):
        status = "partial"
        reasons = list(parser.stats.incomplete_reasons)
        if item_errors:
            reasons.append(f"{item_errors} item(s) failed")
        error_message = "; ".join(reasons)

    summary: dict[str, Any] = {}
    try:
        summary = run.finish(status, item_errors, error_message, parser.stats.complete)
    except Exception:  # noqa: BLE001
        log.exception("[%s] could not close the run record", parser_cls.key)

    result = RunResult(
        parser_key=parser_cls.key,
        status=status,
        events_written=written,
        item_errors=item_errors,
        complete=parser.stats.complete,
        duration_seconds=time.monotonic() - started,
        error_message=error_message,
        outcomes=outcomes,
        summary=summary or {},
    )
    (log.info if result.ok else log.error)("%s", result.describe())
    return result

