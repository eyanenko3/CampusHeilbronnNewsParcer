---
description: Architecture, conventions, and commands for the TUM Campus Heilbronn News & Events Aggregator.
---

# TUM Campus Heilbronn News & Events Aggregator — Project Rules

## 1. Two-Folder Architecture
- **[`parsers/`](../../parsers)**: Deterministic, zero-AI Python web scrapers (`chn_parsers`).
  - Every parser inherits from `BaseParser` (`parsers/chn_parsers/base.py`), is decorated with `@register` (`parsers/chn_parsers/registry.py`), lives in `parsers/chn_parsers/sources/`, and yields validated `Event` instances (`parsers/chn_parsers/models.py`).
  - Existing parsers:
    - `tum_chn_events` (`parsers/chn_parsers/sources/tum_chn_events.py`) — scrapes `https://chn.tum.de/events` + detail pages.
    - `tum_chn_news` (`parsers/chn_parsers/sources/tum_chn_news.py`) — scrapes `https://chn.tum.de/stories/news` + detail pages.
- **[`database/`](../../database)**: PostgreSQL relational schema (`core`), ingestion stored procedures (`ingest`), read/filter views & functions (`api`), thin HTTP gateway (`database/api_server.py`), React Native client (`database/client/`), and mobile-first Firebase web app (`database/public/index.html` + `database/public/live_feed.json`).

## 2. Key Commands
- Run unit tests (offline):
  `PYTHONPATH=parsers python3 -m unittest discover -v -s parsers/tests`
- List registered parsers:
  `PYTHONPATH=parsers python3 -m chn_parsers list`
- Show collected news in terminal / re-scrape:
  `python3 show_news.py` or `python3 show_news.py --scrape`
- Serve the mobile-first sliding web app locally:
  `python3 -m http.server 8080 --directory database/public`
- Deploy static web app to Firebase Hosting:
  `firebase deploy --only hosting`
