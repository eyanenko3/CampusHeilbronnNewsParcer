"""
The data contract between parsers and the database.

Every parser - no matter which website it reads - must produce ``Event``
objects. The class validates and normalises its fields on construction, so a
buggy parser fails loudly *here* (with a clear message pointing at the field)
instead of writing garbage into PostgreSQL.

``Event.to_payload()`` produces exactly the JSON document that the SQL function
``ingest.upsert_event(run_id, payload)`` expects (see
``database/migrations/002_ingest_functions.sql``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse

__all__ = ["Event", "EventValidationError"]

_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


class EventValidationError(ValueError):
    """Raised when a parser builds an ``Event`` with invalid data."""


def _clean_text(value: str | None, *, multiline: bool = False) -> str | None:
    """Collapse whitespace and turn empty strings into ``None``.

    With ``multiline=True`` paragraph breaks are kept (max. one blank line),
    which is what we want for descriptions.
    """
    if value is None:
        return None
    if multiline:
        lines = (_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines())
        text = _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()
    else:
        text = " ".join(value.split())
    return text or None


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@dataclass(slots=True, kw_only=True)
class Event:
    """One normalised event, independent of where it was scraped from.

    Required
    --------
    external_id : Identifier of the event *at the source*. Must be stable across
                  runs (it is how re-scrapes update instead of duplicating).
    title       : Name of the event.
    url         : Link to the original page (to read more / register).
    starts_at   : Timezone-aware start. For all-day events: local midnight.

    Optional
    --------
    ends_at          : Timezone-aware end (``None`` if unknown).
    all_day          : ``True`` if the source only provides a date, no time.
                       All-day events get ``ends_at`` = end of their last day
                       automatically, so "upcoming" filters keep them visible
                       for the whole day.
    summary          : Short teaser for list views.
    description      : Full plain-text description (paragraphs separated by a
                       blank line).
    image_url        : Absolute URL of a representative image.
    registration_url : Absolute URL where students can sign up.
    location         : Human readable venue.
    organizer        : Who organises it.
    language         : ISO 639-1 language code of the content, e.g. ``"en"``.
    tags             : Categories / audiences, e.g. ``["Current Students"]``.
    extra            : Free-form, JSON-serialisable source specific data.
    """

    external_id: str
    title: str
    url: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    summary: str | None = None
    description: str | None = None
    image_url: str | None = None
    registration_url: str | None = None
    location: str | None = None
    organizer: str | None = None
    language: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Validation / normalisation                                         #
    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        # --- text fields -------------------------------------------------
        self.external_id = _clean_text(self.external_id) or ""
        self.title = _clean_text(self.title) or ""
        if not self.external_id:
            raise EventValidationError("external_id must not be empty")
        if not self.title:
            raise EventValidationError(f"[{self.external_id}] title must not be empty")

        self.summary = _clean_text(self.summary)
        self.description = _clean_text(self.description, multiline=True)
        self.location = _clean_text(self.location)
        self.organizer = _clean_text(self.organizer)
        self.language = (_clean_text(self.language) or "").lower() or None

        # A teaser identical to the description adds nothing.
        if self.summary and self.description and self.summary == self.description:
            self.summary = None

        # --- URLs ------------------------------------------------------------
        self.url = (self.url or "").strip()
        if not _is_http_url(self.url):
            raise EventValidationError(f"[{self.external_id}] url must be an absolute http(s) URL: {self.url!r}")
        for name in ("image_url", "registration_url"):
            value = (getattr(self, name) or "").strip() or None
            if value is not None and not _is_http_url(value):
                raise EventValidationError(f"[{self.external_id}] {name} must be an absolute http(s) URL: {value!r}")
            setattr(self, name, value)

        # --- dates -------------------------------------------------------------
        for name in ("starts_at", "ends_at"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise EventValidationError(
                    f"[{self.external_id}] {name} must be a timezone-aware datetime, got {value!r}"
                )
        if self.all_day:
            # Normalise all-day events to [00:00 of first day, 23:59:59 of last day].
            self.starts_at = datetime.combine(self.starts_at.date(), time.min, self.starts_at.tzinfo)
            last_day = (self.ends_at or self.starts_at).date()
            self.ends_at = datetime.combine(last_day, time(23, 59, 59), self.starts_at.tzinfo)
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise EventValidationError(
                f"[{self.external_id}] ends_at ({self.ends_at}) is before starts_at ({self.starts_at})"
            )

        # --- collections ---------------------------------------------------------
        seen: set[str] = set()
        clean_tags: list[str] = []
        for tag in self.tags:
            cleaned = _clean_text(tag)
            if cleaned and cleaned.casefold() not in seen:
                seen.add(cleaned.casefold())
                clean_tags.append(cleaned)
        self.tags = clean_tags
        if not isinstance(self.extra, dict):
            raise EventValidationError(f"[{self.external_id}] extra must be a dict")

    # ------------------------------------------------------------------ #
    # Serialisation                                                      #
    # ------------------------------------------------------------------ #
    def to_payload(self) -> dict[str, Any]:
        """JSON-serialisable dict for ``ingest.upsert_event``.

        Datetimes become ISO-8601 strings *with* UTC offset so PostgreSQL can
        cast them to ``timestamptz`` without guessing a timezone.
        """
        return {
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "all_day": self.all_day,
            "summary": self.summary,
            "description": self.description,
            "image_url": self.image_url,
            "registration_url": self.registration_url,
            "location": self.location,
            "organizer": self.organizer,
            "language": self.language,
            "tags": list(self.tags),
            "extra": dict(self.extra),
        }

    @property
    def duration(self) -> timedelta | None:
        """Convenience for logging/tests."""
        return (self.ends_at - self.starts_at) if self.ends_at else None

