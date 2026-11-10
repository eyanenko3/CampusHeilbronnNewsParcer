"""
The base class every parser inherits from.

A parser has exactly one job: turn a website into a stream of
:class:`~chn_parsers.models.Event` objects. It does NOT know about databases,
scheduling or HTTP retries - those are handled by the runner, the sinks and
the shared :class:`~chn_parsers.http.HttpClient`.

Minimal example (see ``sources/_template.py`` for a fully commented one)::

    @register
    class MyClubParser(BaseParser):
        key = "my_club"
        name = "My Club Events"
        base_url = "https://example.org/events"

        def scrape(self) -> Iterator[Event]:
            soup = self.http.get_soup(self.base_url)
            for card in soup.select(".event"):
                with self.item_guard(card.get("id", "?")):
                    yield Event(...)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

from .http import Fetcher
from .models import Event

__all__ = ["BaseParser", "ParserStats"]

#: Campus Heilbronn is in Germany; sources usually publish local wall-clock times.
BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(slots=True)
class ParserStats:
    """Book-keeping filled in while ``scrape()`` runs; read by the runner."""

    item_errors: int = 0
    #: ``False`` as soon as the parser knows it did NOT see the full listing
    #: (e.g. page 2 of 3 failed). Incomplete runs never soft-delete events.
    complete: bool = True
    incomplete_reasons: list[str] = field(default_factory=list)


class BaseParser(ABC):
    """Abstract parser. Subclasses set the class attributes and implement
    :meth:`scrape`."""

    # ------------------------------------------------------------------ #
    # Required class attributes                                          #
    # ------------------------------------------------------------------ #
    #: Unique, stable identifier: lowercase letters, digits and underscores.
    #: Stored in ``core.sources.key`` - never rename it once in production.
    key: ClassVar[str]
    #: Human readable name shown in the app ("TUM Campus Heilbronn").
    name: ClassVar[str]
    #: Entry URL of the source; also shown as the source's homepage.
    base_url: ClassVar[str]

    # ------------------------------------------------------------------ #
    # Optional class attributes                                          #
    # ------------------------------------------------------------------ #
    #: How often the daemon runs this parser.
    interval: ClassVar[timedelta] = timedelta(hours=6)
    #: Default timezone for naive dates found on the page.
    timezone: ClassVar[ZoneInfo] = BERLIN

    def __init__(self, http: Fetcher) -> None:
        self.http = http
        self.stats = ParserStats()
        self.log = logging.getLogger(f"chn_parsers.sources.{self.key}")

    # ------------------------------------------------------------------ #
    # The one method to implement                                        #
    # ------------------------------------------------------------------ #
    @abstractmethod
    def scrape(self) -> Iterator[Event]:
        """Yield every event currently published by the source.

        Guidelines:
          * Be a generator (``yield``) so events are stored as they come.
          * Wrap the processing of ONE item in ``with self.item_guard(...)``
            so a single malformed entry does not abort the whole run.
          * Call :meth:`mark_incomplete` if you could not read the full
            listing (a pagination page failed, a safety limit was hit, ...).
          * Let exceptions escape for problems that make the whole run
            meaningless (e.g. the listing page itself is unreachable).
        """

    # ------------------------------------------------------------------ #
    # Helpers for subclasses                                             #
    # ------------------------------------------------------------------ #
    @contextmanager
    def item_guard(self, item_ref: str) -> Iterator[None]:
        """Isolate failures of a single item.

        Any exception inside the block is logged with ``item_ref`` and counted
        in ``stats.item_errors``; the parser then continues with the next item.
        """
        try:
            yield
        except Exception:  # noqa: BLE001 - intentionally broad: isolate any item failure
            self.stats.item_errors += 1
            self.log.exception("Skipping item %s: failed to parse", item_ref)

    def mark_incomplete(self, reason: str) -> None:
        """Declare that this run did not see the full listing."""
        self.stats.complete = False
        self.stats.incomplete_reasons.append(reason)
        self.log.warning("Run marked incomplete: %s", reason)

    @classmethod
    def describe(cls) -> str:
        return f"{cls.key:<24} {cls.name}  (every {cls.interval}, {cls.base_url})"

