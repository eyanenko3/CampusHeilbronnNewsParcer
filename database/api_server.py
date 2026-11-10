"""
Thin, stateless JSON HTTP gateway over the PostgreSQL ``api`` schema.

All filtering, full-text search, tag aggregation, and pagination live in SQL
(``database/migrations/003_api_read_layer.sql``). This server simply maps HTTP
query parameters 1:1 onto those read-only SQL functions/views so that:

  * React Native (iOS / Android / Web) can query events over HTTPS, and
  * Firebase Hosting can rewrite ``/api/**`` to this container on Cloud Run.

Endpoints
---------
  GET /healthz
  GET /api/v1/events?from=...&to=...&tags=campus,current-students&sources=tum_chn_events&q=AI&limit=50&offset=0
  GET /api/v1/events/<id>
  GET /api/v1/tags
  GET /api/v1/sources
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("chn_api")

_EVENT_ID_RE = re.compile(r"^/api/v1/events/(\d+)/?$")
_EVENT_COLUMNS = (
    "id, source_key, source_name, title, summary, description, url, "
    "registration_url, image_url, starts_at, ends_at, all_day, location, "
    "organizer, language, tags, tag_slugs, first_seen_at, updated_at"
)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def _csv_param(params: dict[str, list[str]], name: str) -> list[str] | None:
    raw_values = params.get(name, [])
    items = [part.strip() for raw in raw_values for part in raw.split(",") if part.strip()]
    return items or None


def _str_param(params: dict[str, list[str]], name: str) -> str | None:
    vals = params.get(name)
    if not vals or not vals[0].strip():
        return None
    return vals[0].strip()


def _int_param(params: dict[str, list[str]], name: str, default: int) -> int:
    raw = _str_param(params, name)
    if raw is None:
        return default
    return int(raw)


class ApiHandler(BaseHTTPRequestHandler):
    database_url: str = ""
    cors_origin: str = "*"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            if path == "/healthz":
                self._handle_healthz()
            elif path == "/api/v1/events":
                self._handle_list_events(params)
            elif match := _EVENT_ID_RE.match(path):
                self._handle_get_event(int(match.group(1)))
            elif path == "/api/v1/tags":
                self._handle_list_tags()
            elif path == "/api/v1/sources":
                self._handle_list_sources()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown endpoint: {path}"})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error for GET %s", self.path)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    # ------------------------------------------------------------------ #
    # Route handlers                                                     #
    # ------------------------------------------------------------------ #
    def _connect(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(
            self.database_url,
            autocommit=True,
            row_factory=dict_row,
            application_name="chn-api",
        )

    def _handle_healthz(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()
        self._send_json(HTTPStatus.OK, {"status": "ok"})

    def _handle_list_events(self, params: dict[str, list[str]]) -> None:
        p_from = _str_param(params, "from")
        p_to = _str_param(params, "to")
        p_tags = _csv_param(params, "tags")
        p_sources = _csv_param(params, "sources")
        p_query = _str_param(params, "q")
        p_limit = max(1, min(_int_param(params, "limit", 50), 200))
        p_offset = max(0, _int_param(params, "offset", 0))

        # When 'from' is omitted, api.search_events defaults to now().
        # When 'from=all' is passed, we pass NULL to include past events too.
        use_default_from = p_from is None
        from_arg = None if p_from == "all" else p_from

        with self._connect() as conn:
            if use_default_from:
                items = conn.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM api.search_events("
                    "p_to => %s, p_tags => %s, p_sources => %s, p_query => %s, p_limit => %s, p_offset => %s)",
                    (p_to, p_tags, p_sources, p_query, p_limit, p_offset),
                ).fetchall()
                total_row = conn.execute(
                    "SELECT api.count_events(p_to => %s, p_tags => %s, p_sources => %s, p_query => %s) AS total",
                    (p_to, p_tags, p_sources, p_query),
                ).fetchone()
            else:
                items = conn.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM api.search_events(%s, %s, %s, %s, %s, %s, %s)",
                    (from_arg, p_to, p_tags, p_sources, p_query, p_limit, p_offset),
                ).fetchall()
                total_row = conn.execute(
                    "SELECT api.count_events(%s, %s, %s, %s, %s) AS total",
                    (from_arg, p_to, p_tags, p_sources, p_query),
                ).fetchone()

        self._send_json(
            HTTPStatus.OK,
            {
                "total": int(total_row["total"]) if total_row else 0,
                "limit": p_limit,
                "offset": p_offset,
                "items": items,
            },
        )

    def _handle_get_event(self, event_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM api.events WHERE id = %s",
                (event_id,),
            ).fetchone()
        if row is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Event {event_id} not found"})
            return
        self._send_json(HTTPStatus.OK, row)

    def _handle_list_tags(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT slug, label, upcoming_count FROM api.tags ORDER BY upcoming_count DESC, label ASC"
            ).fetchall()
        self._send_json(HTTPStatus.OK, {"items": rows})

    def _handle_list_sources(self) -> None:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM api.sources ORDER BY name ASC").fetchall()
        self._send_json(HTTPStatus.OK, {"items": rows})

    # ------------------------------------------------------------------ #
    # Response helpers                                                   #
    # ------------------------------------------------------------------ #
    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    logging.basicConfig(
        level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL environment variable is required.")

    ApiHandler.database_url = database_url
    ApiHandler.cors_origin = os.getenv("CORS_ALLOW_ORIGIN", "*")

    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ApiHandler)
    log.info("CHN Read API listening on http://0.0.0.0:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

