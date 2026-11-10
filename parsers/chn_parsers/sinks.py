"""
Sinks: where scraped events go.

The runner talks to a sink through two tiny interfaces (``Sink`` /
``RunHandle``), so parsers stay database-agnostic and a dry run is just a
different sink:

  * :class:`PostgresSink` - production. Calls the SQL functions
    ``ingest.start_run`` / ``ingest.upsert_event`` / ``ingest.finish_run``.
    All persistence rules (dedup, change detection, tags, soft delete) live
    in SQL, not here.
  * :class:`JsonSink` - ``--dry-run``. Collects events and prints them as JSON.
    No database needed; perfect while developing a new parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Protocol, TextIO

from .base import BaseParser
from .models import Event

__all__ = ["Sink", "RunHandle", "PostgresSink", "JsonSink"]


class RunHandle(Protocol):
    def write(self, event: Event) -> str:
        """Persist one event, return 'inserted' | 'updated' | 'unchanged' | 'collected'."""

    def finish(self, status: str, item_errors: int, error_message: str | None, complete: bool) -> dict[str, Any]:
        """Close the run, return a summary."""


class Sink(Protocol):
    def open_run(self, parser_cls: type[BaseParser]) -> RunHandle: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------- #
# PostgreSQL                                                             #
# ---------------------------------------------------------------------- #
class _PostgresRun:
    def __init__(self, conn: Any, run_id: int) -> None:
        self._conn = conn
        self.run_id = run_id

    def write(self, event: Event) -> str:
        from psycopg.types.json import Jsonb

        row = self._conn.execute(
            "SELECT ingest.upsert_event(%s, %s)", (self.run_id, Jsonb(event.to_payload()))
        ).fetchone()
        return row[0]

    def finish(self, status: str, item_errors: int, error_message: str | None, complete: bool) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT ingest.finish_run(%s, %s, %s, %s, %s)",
            (self.run_id, status, item_errors, error_message, complete),
        ).fetchone()
        return row[0]  # jsonb -> dict


class PostgresSink:
    """Writes through the ``ingest`` SQL API.

    ``autocommit=True``: every event is committed on its own, so a crash in
    the middle of a run keeps everything stored so far, and one rejected event
    (constraint violation) does not roll back the others.
    """

    def __init__(self, database_url: str) -> None:
        import psycopg  # imported lazily so --dry-run works without psycopg/libpq

        self._conn = psycopg.connect(database_url, autocommit=True, application_name="chn-parsers")

    def open_run(self, parser_cls: type[BaseParser]) -> _PostgresRun:
        row = self._conn.execute(
            "SELECT ingest.start_run(%s, %s, %s)", (parser_cls.key, parser_cls.name, parser_cls.base_url)
        ).fetchone()
        return _PostgresRun(self._conn, row[0])

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------- #
# JSON (dry run)                                                         #
# ---------------------------------------------------------------------- #
class _JsonRun:
    def __init__(self, sink: JsonSink, parser_cls: type[BaseParser]) -> None:
        self._sink = sink
        self._parser_cls = parser_cls
        self._events: list[dict[str, Any]] = []

    def write(self, event: Event) -> str:
        self._events.append(event.to_payload())
        return "collected"

    def finish(self, status: str, item_errors: int, error_message: str | None, complete: bool) -> dict[str, Any]:
        summary = {
            "source": self._parser_cls.key,
            "status": status,
            "complete": complete,
            "events_seen": len(self._events),
            "item_errors": item_errors,
            "error_message": error_message,
        }
        self._sink.results.append({**summary, "events": self._events})
        return summary


class JsonSink:
    """Collects all runs and dumps them as one JSON document on ``close()``."""

    def __init__(self, output: Path | None = None, stream: TextIO | None = None) -> None:
        self._output = output
        self._stream = stream or sys.stdout
        self.results: list[dict[str, Any]] = []

    def open_run(self, parser_cls: type[BaseParser]) -> _JsonRun:
        return _JsonRun(self, parser_cls)

    def close(self) -> None:
        document = json.dumps(self.results, indent=2, ensure_ascii=False)
        if self._output:
            self._output.write_text(document + "\n", encoding="utf-8")
        else:
            print(document, file=self._stream)

