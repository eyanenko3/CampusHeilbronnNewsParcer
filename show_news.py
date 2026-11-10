#!/usr/bin/env python3
"""
Convenience CLI script to display and run the TUM Campus Heilbronn news scraper.

Usage:
    python3 show_news.py            # Display already collected news articles
    python3 show_news.py --scrape   # Re-run live scraper to fetch freshest news
    python3 show_news.py --all      # Show both News and Events
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "parsers"))

FEED_FILE = REPO_ROOT / "database" / "public" / "live_feed.json"


def display_items(items, kind="NEWS ARTICLES"):
    print(f"\n{'='*70}")
    print(f"  TUM CAMPUS HEILBRONN — {kind} ({len(items)} items)")
    print(f"{'='*70}\n")
    for idx, item in enumerate(items, 1):
        print(f"[{idx}] {item.get('title')}")
        print(f"    📅 Date/Time: {item.get('starts_at')}")
        if item.get("location"):
            print(f"    📍 Location:  {item.get('location')}")
        if item.get("tags"):
            print(f"    🏷️  Tags:      {', '.join(item.get('tags', []))}")
        print(f"    🔗 URL:       {item.get('url')}")
        if item.get("image_url"):
            print(f"    🖼️  Image:     {item.get('image_url')}")
        summary = item.get("summary") or item.get("description") or ""
        clean_sum = " ".join(summary.split())[:180]
        if clean_sum:
            print(f"    📝 Summary:   {clean_sum}...")
        print("-" * 70)


def run_live_scrape(parser_key="tum_chn_news"):
    from chn_parsers.http import HttpClient
    from chn_parsers.registry import get_parser
    from chn_parsers.runner import run_parser
    from chn_parsers.sinks import JsonSink
    from chn_parsers.config import load_settings

    settings = load_settings()
    parser_cls = get_parser(parser_key)
    sink = JsonSink()

    print(f"[scraper] Fetching live data from {parser_cls.base_url} ...")
    with HttpClient(settings) as http:
        result = run_parser(parser_cls, http, sink)

    print(f"[scraper] Finished: {result.describe()}")
    if sink.results and "events" in sink.results[0]:
        return sink.results[0]["events"]
    return []


def main():
    parser = argparse.ArgumentParser(description="TUM Campus Heilbronn News Viewer")
    parser.add_argument("--scrape", action="store_true", help="Run live scraper over network")
    parser.add_argument("--all", action="store_true", help="Display events as well as news")
    args = parser.parse_args()

    if args.scrape:
        news_items = run_live_scrape("tum_chn_news")
        display_items(news_items, "LIVE SCRAPED NEWS")
        return

    if not FEED_FILE.exists():
        print(f"Feed file not found at {FEED_FILE}. Run with --scrape to fetch.")
        sys.exit(1)

    data = json.loads(FEED_FILE.read_text(encoding="utf-8"))
    news_items = []
    event_items = []

    for run in data:
        source = run.get("source")
        events = run.get("events", [])
        if source == "tum_chn_news":
            news_items.extend(events)
        else:
            event_items.extend(events)

    display_items(news_items, "COLLECTED NEWS ARTICLES")

    if args.all:
        display_items(event_items, "CAMPUS EVENTS")


if __name__ == "__main__":
    main()

