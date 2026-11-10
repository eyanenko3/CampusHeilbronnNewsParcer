"""
A small, *polite* HTTP client shared by all parsers.

Why not call ``httpx.get`` directly in each parser?
  * Politeness: a minimum delay between requests to the same host, a
    descriptive User-Agent and robots.txt compliance are enforced centrally.
    University websites are not ours to hammer.
  * Robustness: retries with exponential backoff for flaky networks,
    HTTP 429 (rate limited) and 5xx errors.
  * Testability: parsers depend on the tiny :class:`Fetcher` protocol, so tests
    inject a fake that serves saved HTML files - no network needed.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - stdlib fallback when httpx is absent
    httpx = None  # type: ignore[assignment]

from .config import Settings
from .utils.html import BeautifulSoup

__all__ = ["Fetcher", "HttpClient", "FetchError", "RobotsDisallowed"]

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A page could not be downloaded (after all retries)."""


class RobotsDisallowed(FetchError):
    """robots.txt forbids fetching this URL for our User-Agent."""


class Fetcher(Protocol):
    """What a parser needs from the network layer. Implemented by HttpClient
    in production and by a fixture-backed fake in tests."""

    def get_text(self, url: str) -> str: ...

    def get_soup(self, url: str) -> BeautifulSoup: ...


@dataclass(slots=True)
class _SimpleResponse:
    status_code: int
    text: str
    headers: dict[str, str]


class HttpClient:
    """Synchronous HTTP client with rate limiting, retries and robots.txt."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,de;q=0.8",
        }
        self._client = (
            httpx.Client(
                headers=self._headers,
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            )
            if httpx is not None
            else None
        )
        self._last_request_at: dict[str, float] = {}  # host -> monotonic timestamp
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def get_text(self, url: str) -> str:
        """Download ``url`` and return the decoded body."""
        if self._settings.respect_robots_txt and not self._allowed_by_robots(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        return self._request_with_retries(url).text

    def get_soup(self, url: str) -> BeautifulSoup:
        """Download ``url`` and parse it with BeautifulSoup (stdlib html.parser,
        so there is no native dependency to compile)."""
        return BeautifulSoup(self.get_text(url), "html.parser")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #
    def _throttle(self, host: str) -> None:
        """Sleep so that two requests to ``host`` are at least
        ``request_delay_seconds`` apart."""
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self._settings.request_delay_seconds - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _raw_get(self, url: str) -> _SimpleResponse:
        if self._client is not None:
            resp = self._client.get(url)
            return _SimpleResponse(resp.status_code, resp.text, dict(resp.headers))
        req = urllib.request.Request(url, headers=self._headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._settings.request_timeout_seconds) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                body = resp.read().decode(charset, errors="replace")
                return _SimpleResponse(resp.status, body, dict(resp.headers))
        except urllib.error.HTTPError as exc:
            return _SimpleResponse(exc.code, "", dict(exc.headers) if exc.headers else {})

    def _request_with_retries(self, url: str) -> _SimpleResponse:
        host = urlparse(url).netloc
        attempts = self._settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._throttle(host)
            try:
                response = self._raw_get(url)
                if response.status_code in _RETRYABLE_STATUS:
                    last_error = FetchError(f"HTTP {response.status_code}")
                    retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after") or ""
                    backoff = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                elif 200 <= response.status_code < 300:
                    log.debug("GET %s -> %s", url, response.status_code)
                    return response
                else:
                    raise FetchError(f"GET {url} failed with HTTP {response.status_code}")
            except FetchError:
                raise
            except Exception as exc:  # noqa: BLE001 - network/transport errors (httpx or urllib)
                last_error = exc
                backoff = 2 ** attempt

            if attempt < attempts:
                log.warning("GET %s failed (%s); retry %d/%d in %.0fs",
                            url, last_error, attempt, attempts - 1, backoff)
                time.sleep(backoff)

        raise FetchError(f"GET {url} failed after {attempts} attempts: {last_error}") from last_error

    def _allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        robots = self._robots[origin]
        # No (readable) robots.txt means no restrictions, per the convention.
        return robots is None or robots.can_fetch(self._settings.user_agent, url)

    def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            response = self._request_with_retries(robots_url)
        except FetchError as exc:
            log.info("No usable robots.txt at %s (%s); assuming allowed", robots_url, exc)
            return None
        parser = urllib.robotparser.RobotFileParser(robots_url)
        parser.parse(response.text.splitlines())
        return parser
