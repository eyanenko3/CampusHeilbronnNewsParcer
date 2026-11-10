"""
Deterministic date/time parsing helpers shared by parsers.

Websites write dates for humans: "November 6, 2026 – December 10, 2026",
"6. November 2026", "11:30 am – 2:00 pm", "18:00 - 20:00 Uhr". These helpers
convert such strings into ``date`` / ``time`` objects with plain regular
expressions - no AI, no network, fully unit-testable.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

__all__ = ["MONTHS", "parse_dates", "parse_time_range", "combine", "parse_iso_date"]

#: Month names/abbreviations in English and German -> month number.
MONTHS: dict[str, int] = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    # German (only the ones that differ from English)
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6,
    "juli": 7, "oktober": 10, "dezember": 12, "mär": 3, "okt": 10, "dez": 12,
}

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))  # longest first

# "November 6, 2026"  /  "Nov 6 2026"
_MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
# "6. November 2026"  /  "6 Nov 2026"
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
# "06.11.2026"  /  "6.11.26"
_NUMERIC_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b")
# "2026-11-06" or "2026-11-06T00:00:00"
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?=T|\b)")

# "11:30 am", "2 pm", "14:00", "14.00 Uhr", "11:30 a.m."
_TIME = re.compile(
    r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?=[\s\-–—,;)]|uhr|h\b|$)",
    re.IGNORECASE,
)


def parse_iso_date(value: str | None) -> date | None:
    """'2026-11-06' or '2026-11-06T00:00:00' -> date(2026, 11, 6)."""
    if not value:
        return None
    match = _ISO_DATE.search(value)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def parse_dates(text: str | None) -> list[date]:
    """Return all calendar dates found in ``text`` in order of appearance.

    >>> parse_dates("November 6, 2026 – December 10, 2026")
    [datetime.date(2026, 11, 6), datetime.date(2026, 12, 10)]
    """
    if not text:
        return []
    found: list[tuple[int, date]] = []  # (position in text, date)

    def add(position: int, year: int, month: int, day: int) -> None:
        if year < 100:
            year += 2000
        try:
            found.append((position, date(year, month, day)))
        except ValueError:
            pass  # e.g. "31. February" - ignore impossible dates

    for m in _MONTH_DAY_YEAR.finditer(text):
        add(m.start(), int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    for m in _DAY_MONTH_YEAR.finditer(text):
        add(m.start(), int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    for m in _NUMERIC_DMY.finditer(text):
        add(m.start(), int(m[3]), int(m[2]), int(m[1]))
    for m in _ISO_DATE.finditer(text):
        add(m.start(), int(m[1]), int(m[2]), int(m[3]))

    found.sort(key=lambda item: item[0])
    result: list[date] = []
    for _, value in found:
        if value not in result:
            result.append(value)
    return result


def _to_time(hour: int, minute: int, meridiem: str | None) -> time | None:
    if meridiem:
        meridiem = meridiem.lower().replace(".", "").replace(" ", "")
        if not 1 <= hour <= 12:
            return None
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def parse_time_range(text: str | None) -> tuple[time | None, time | None]:
    """Extract (start, end) times of day from a human string.

    >>> parse_time_range("11:30 am – 2:00 pm")
    (datetime.time(11, 30), datetime.time(14, 0))
    >>> parse_time_range("18:00 Uhr")
    (datetime.time(18, 0), None)

    A bare number without minutes or am/pm (e.g. a day "6") is NOT treated as
    a time, to avoid false positives. If only the end carries am/pm
    ("10 – 11:30 am"), the start inherits it.
    """
    if not text:
        return None, None
    tokens: list[tuple[int, int, str | None]] = []
    for m in _TIME.finditer(text):
        hour, minute, meridiem = int(m[1]), m[2], m[3]
        if minute is None and meridiem is None:
            continue  # "6" alone is ambiguous
        tokens.append((hour, int(minute or 0), meridiem))
        if len(tokens) == 2:
            break
    if not tokens:
        return None, None

    if len(tokens) == 2 and tokens[0][2] is None and tokens[1][2] is not None:
        tokens[0] = (tokens[0][0], tokens[0][1], tokens[1][2])

    start = _to_time(*tokens[0])
    end = _to_time(*tokens[1]) if len(tokens) == 2 else None
    return start, end


def combine(day: date, at: time, tz: ZoneInfo) -> datetime:
    """Local wall-clock date + time -> timezone-aware datetime.

    ``zoneinfo`` resolves daylight-saving transitions correctly
    (e.g. CET +01:00 in winter, CEST +02:00 in summer).
    """
    return datetime.combine(day, at, tzinfo=tz)
