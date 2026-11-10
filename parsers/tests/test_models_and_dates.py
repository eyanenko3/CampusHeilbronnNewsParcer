"""
Unit tests for Event validation, date/time parsing, and parser discovery.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from chn_parsers.models import Event, EventValidationError
from chn_parsers.registry import all_parsers, get_parser
from chn_parsers.utils.dates import combine, parse_dates, parse_iso_date, parse_time_range

BERLIN = ZoneInfo("Europe/Berlin")


class DatesAndModelsTests(unittest.TestCase):
    def test_parse_dates_handles_english_german_and_numeric_formats(self) -> None:
        self.assertEqual(
            parse_dates("November 6, 2026 – December 10, 2026"),
            [date(2026, 11, 6), date(2026, 12, 10)],
        )
        self.assertEqual(parse_dates("6. Oktober 2026"), [date(2026, 10, 6)])
        self.assertEqual(
            parse_dates("(06.11.2026 – 02.12.2026)"),
            [date(2026, 11, 6), date(2026, 12, 2)],
        )
        self.assertEqual(parse_iso_date("2026-11-06T00:00:00"), date(2026, 11, 6))

    def test_parse_time_range_handles_12h_and_24h_clocks(self) -> None:
        self.assertEqual(parse_time_range("11:30 am – 2:00 pm"), (time(11, 30), time(14, 0)))
        self.assertEqual(parse_time_range("18:00 - 20:30 Uhr"), (time(18, 0), time(20, 30)))
        self.assertEqual(parse_time_range("10:00 – 11:30 am"), (time(10, 0), time(11, 30)))
        self.assertEqual(parse_time_range("no time here"), (None, None))

    def test_event_validates_and_normalises_fields(self) -> None:
        ev = Event(
            external_id="  evt-1 ",
            title="  Hackathon   Heilbronn\n",
            url="https://chn.tum.de/event/hackathon",
            starts_at=combine(date(2026, 11, 6), time(9, 0), BERLIN),
            tags=["Campus", "campus", " Current Students "],
        )
        self.assertEqual(ev.external_id, "evt-1")
        self.assertEqual(ev.title, "Hackathon Heilbronn")
        self.assertEqual(ev.tags, ["Campus", "Current Students"])
        payload = ev.to_payload()
        self.assertEqual(payload["starts_at"], "2026-11-06T09:00:00+01:00")

    def test_event_rejects_naive_datetime_and_bad_urls(self) -> None:
        with self.assertRaises(EventValidationError):
            Event(
                external_id="1",
                title="Bad",
                url="https://chn.tum.de/e",
                starts_at=datetime(2026, 11, 6, 9, 0),  # naive!
            )
        with self.assertRaises(EventValidationError):
            Event(
                external_id="1",
                title="Bad",
                url="/relative/path",
                starts_at=combine(date(2026, 11, 6), time(9, 0), BERLIN),
            )

    def test_registry_discovers_tum_chn_events_and_skips_template(self) -> None:
        parsers = all_parsers()
        self.assertIn("tum_chn_events", parsers)
        self.assertNotIn("example_source", parsers)
        self.assertIs(get_parser("tum_chn_events"), parsers["tum_chn_events"])


if __name__ == "__main__":
    unittest.main()

