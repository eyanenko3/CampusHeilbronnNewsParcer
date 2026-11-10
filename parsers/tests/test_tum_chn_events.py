"""
End-to-end unit tests for the TUM Campus Heilbronn events parser.

Runs completely offline using saved HTML fixtures in ``tests/fixtures/``.
Compatible with both ``pytest`` and ``python3 -m unittest``.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chn_parsers.http import FetchError
from chn_parsers.runner import run_parser
from chn_parsers.sinks import JsonSink
from chn_parsers.sources.tum_chn_events import TumChnEventsParser
from chn_parsers.utils.html import BeautifulSoup

FIXTURES = Path(__file__).parent / "fixtures"
BERLIN = ZoneInfo("Europe/Berlin")


class FakeFetcher:
    """In-memory ``Fetcher`` mapped from URL -> fixture file name."""

    def __init__(self, routes: dict[str, str]) -> None:
        self._routes = routes
        self.requested_urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested_urls.append(url)
        fixture_name = self._routes.get(url)
        if fixture_name is None:
            raise FetchError(f"Unmapped URL in FakeFetcher: {url}")
        return (FIXTURES / fixture_name).read_text(encoding="utf-8")

    def get_soup(self, url: str) -> BeautifulSoup:
        return BeautifulSoup(self.get_text(url), "html.parser")


class TumChnEventsParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routes = {
            "https://chn.tum.de/events": "chn_listing_p1.html",
            "https://chn.tum.de/events?tx_solrevents%5Bpage%5D=2": "chn_listing_p2.html",
            "https://chn.tum.de/event/event/exhibition-thomas-mann": "chn_detail_exhibition.html",
            "https://chn.tum.de/event/event/buerger-uni-11112026": "chn_detail_buerger_uni.html",
        }

    def test_scrapes_paginated_listing_and_enriches_from_detail_pages(self) -> None:
        fetcher = FakeFetcher(self.routes)
        parser = TumChnEventsParser(fetcher)

        events = list(parser.scrape())

        self.assertTrue(parser.stats.complete)
        self.assertEqual(parser.stats.item_errors, 0)
        self.assertEqual(len(events), 2)

        # --- Event 1: multi-day exhibition with JSON-LD, time range, og:image, powermail form ---
        ex = events[0]
        self.assertEqual(ex.external_id, "calendarize-event-885")
        self.assertEqual(
            ex.title,
            "Exhibition Thomas Mann – In the Burning Landscape of Democracy and AI TO EYE",
        )
        self.assertEqual(ex.url, "https://chn.tum.de/event/event/exhibition-thomas-mann")
        self.assertEqual(ex.starts_at, datetime(2026, 11, 6, 11, 30, tzinfo=BERLIN))
        self.assertEqual(ex.ends_at, datetime(2026, 12, 10, 14, 0, tzinfo=BERLIN))
        self.assertFalse(ex.all_day)
        self.assertEqual(
            ex.location,
            "Bildungscampus Heilbronn, hip-Turm, Edisonstraße 15, Heilbronn",
        )
        self.assertEqual(
            ex.organizer,
            "A joint exhibition by TUM and ETH Zurich at the Bildungscampus Heilbronn",
        )
        # Double slash in og:image is normalised cleanly:
        self.assertEqual(
            ex.image_url,
            "https://chn.tum.de/fileadmin/_processed_/7/5/csm_tum-campus-heilbronn-thomas-mann_9a49d98f2a.jpg",
        )
        self.assertEqual(ex.registration_url, "https://chn.tum.de/event/event/exhibition-thomas-mann")
        self.assertIn("Technical University of Munich and ETH Zurich", ex.description or "")
        self.assertEqual(ex.tags, ["General Public"])
        self.assertEqual(ex.language, "en")
        self.assertTrue(ex.extra.get("registration_form_on_page"))
        self.assertIn("format%5D=ics", ex.extra.get("ics_url", ""))

        # --- Event 2: single-day all-day lecture with explicit registration link ---
        bu = events[1]
        self.assertEqual(bu.external_id, "calendarize-event-517")
        self.assertEqual(bu.title, "Bürger-Uni")
        self.assertTrue(bu.all_day)
        self.assertEqual(bu.starts_at, datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN))
        self.assertEqual(bu.ends_at, datetime(2026, 11, 11, 23, 59, 59, tzinfo=BERLIN))
        self.assertEqual(bu.registration_url, "https://events.example.org/register/517")
        self.assertEqual(bu.tags, ["General Public", "Current Students"])
        self.assertEqual(bu.location, "AULA Bildungscampus, Bildungscampus 6, 74076 Heilbronn")

    def test_marks_run_incomplete_if_second_listing_page_fails(self) -> None:
        del self.routes["https://chn.tum.de/events?tx_solrevents%5Bpage%5D=2"]
        fetcher = FakeFetcher(self.routes)
        sink = JsonSink()

        result = run_parser(TumChnEventsParser, fetcher, sink)

        self.assertEqual(result.status, "partial")
        self.assertFalse(result.complete)
        self.assertEqual(result.events_written, 1)

    def test_isolates_single_broken_detail_page(self) -> None:
        del self.routes["https://chn.tum.de/event/event/exhibition-thomas-mann"]
        fetcher = FakeFetcher(self.routes)
        sink = JsonSink()

        result = run_parser(TumChnEventsParser, fetcher, sink)

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.events_written, 1)
        self.assertEqual(result.item_errors, 1)


if __name__ == "__main__":
    unittest.main()

