"""
Unit tests for the TUM Campus Heilbronn News parser (`tum_chn_news`).
"""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from chn_parsers.sources.tum_chn_news import TumChnNewsParser

from test_tum_chn_events import FakeFetcher

BERLIN = ZoneInfo("Europe/Berlin")


class TumChnNewsParserTests(unittest.TestCase):
    def test_scrapes_news_listing_and_detail_article(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://chn.tum.de/stories/news": "chn_news_listing.html",
                "https://chn.tum.de/stories/mindshift-online/mindshift-article/edge-ai-in-cars": "chn_news_detail.html",
            }
        )
        parser = TumChnNewsParser(fetcher)

        items = list(parser.scrape())

        self.assertEqual(len(items), 1)
        article = items[0]
        self.assertEqual(article.external_id, "tx-news-1022")
        self.assertEqual(article.title, "Edge AI in Cars")
        self.assertEqual(article.starts_at, datetime(2026, 11, 5, 12, 50, 51, tzinfo=BERLIN))
        self.assertFalse(article.all_day)
        self.assertEqual(
            article.image_url,
            "https://chn.tum.de/fileadmin/chn.tum.de/Stories/Mindshift_Online/Profs_und_Mitarbeitende/tum-campus-heilbronn-magazinbeitrag-Sreelakshmi-Rameshan.png",
        )
        self.assertEqual(article.tags, ["Magazine Article", "AI Competence", "Research"])
        self.assertIn("Sensors have joined cylinders", article.description or "")
        self.assertEqual(article.extra.get("kind"), "news")


if __name__ == "__main__":
    unittest.main()
