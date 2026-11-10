# `parsers/` — Deterministic Web Scrapers (`chn_parsers`)

Zero-AI, deterministic Python parser framework and daemon for aggregating publicly available events relevant to **TUM Campus Heilbronn** students.

---

## 1. Design Principles

1. **Zero AI Cost**: Parsing relies strictly on structured metadata (`schema.org` JSON-LD, OpenGraph tags, HTML5 `<time datetime="...">`), SVG icon identifiers, CSS selectors, and deterministic regular expressions (`chn_parsers/utils/dates.py`).
2. **Plug-in Registry (`@register`)**: Adding a new website requires **one file** in `chn_parsers/sources/`. No changes to the CLI, daemon, Docker configuration, or PostgreSQL schema are needed.
3. **Strict Data Contract (`Event`)**: Every parser yields `Event` instances (`chn_parsers/models.py`). Validation and normalisation happen immediately on construction:
   - Required: `external_id`, `title`, `url` (link to original page), `starts_at` (timezone-aware `datetime`).
   - Optional: `ends_at`, `all_day`, `summary`, `description`, `image_url`, `registration_url`, `location`, `organizer`, `language`, `tags`, `extra`.
4. **Database Agnostic**: Parsers never import `psycopg` or write SQL. The runner streams `Event` objects into a `Sink`:
   - `PostgresSink` calls the PostgreSQL stored functions `ingest.start_run`, `ingest.upsert_event`, and `ingest.finish_run`.
   - `JsonSink` (`--dry-run`) prints the normalized JSON payload to `stdout` or a file without needing a database.
5. **Polite & Fault-Isolated**:
   - `HttpClient` enforces per-host rate limiting (`SCRAPER_REQUEST_DELAY_SECONDS`, default `1.5s`), `robots.txt` compliance, and exponential backoff on HTTP `429`/`5xx`.
   - `with self.item_guard(ref):` isolates single-event failures so one malformed card or detail page never aborts the rest of the scrape run.
   - `self.mark_incomplete(reason)` prevents the database from soft-deleting unseen events when pagination stops early.

---

## 2. Folder Structure

```text
parsers/
├── Dockerfile                        # Container image (default CMD: daemon)
├── pyproject.toml                    # Package metadata & dependencies
├── README.md                         # This guide
├── chn_parsers/
│   ├── __init__.py
│   ├── __main__.py                   # `python -m chn_parsers` entry point
│   ├── models.py                     # `Event` dataclass + validation + `.to_payload()`
│   ├── base.py                       # `BaseParser` ABC, `item_guard`, `mark_incomplete`
│   ├── registry.py                   # `@register` decorator + auto-discovery of `sources/`
│   ├── http.py                       # Rate-limited HTTP client + `robots.txt` + retries
│   ├── sinks.py                      # `PostgresSink` (calls `ingest.*`) & `JsonSink`
│   ├── runner.py                     # Connects `BaseParser` -> `Sink`, computes `RunResult`
│   ├── daemon.py                     # Long-running interval scheduler (`SIGTERM`-safe)
│   ├── cli.py                        # `list`, `run`, `daemon` subcommands
│   ├── sources/
│   │   ├── _template.py              # Copy-paste starter template for new parsers
│   │   └── tum_chn_events.py         # Production parser for https://chn.tum.de/events
│   └── utils/
│       ├── dates.py                  # Deterministic EN/DE date & time-range parser
│       ├── html.py                   # BeautifulSoup helpers, JSON-LD & OpenGraph extractors
│       └── _compat_soup.py           # Stdlib HTMLParser fallback when `bs4` isn't installed
└── tests/
    ├── fixtures/                     # Saved HTML from chn.tum.de (listing + detail pages)
    ├── test_models_and_dates.py      # Unit tests for Event validation, dates, and registry
    └── test_tum_chn_events.py        # End-to-end offline tests for TumChnEventsParser
```

---

## 3. How the `https://chn.tum.de/events` Parser Works (`sources/tum_chn_events.py`)

The TUM Campus Heilbronn website runs on TYPO3 with the `calendarize` extension and Apache Solr search. The listing page alone only shows a teaser and a start date (without the time of day or full description), so `TumChnEventsParser` performs a **two-stage scrape**:

1. **Stage 1 — Paginated Listing (`/events`, `/events?tx_solrevents[page]=2`, ...)**:
   - Extracts each `<article class="c-events-solr-list__item">`.
   - Reads the stable TYPO3 calendarize ID from `data-document-id=".../tx_calendarize_domain_model_event/885"` $\rightarrow$ `external_id = "calendarize-event-885"`.
   - Extracts the card title, detail URL, teaser, thumbnail `<img>`, `<time datetime="YYYY-MM-DD">`, audience tags (`.c-events-solr-list__item-tag`), and venue (`svg[title]`).
   - Follows `li.c-search__pagination-next a[href]` until the last page.
2. **Stage 2 — Event Detail Page (`/event/event/<slug>`)**:
   - Opens each event's link to extract the full description and exact date/time range:
     - **JSON-LD (`<script type="application/ld+json">`)**: `schema.org/Event` `name`, `startDate`, `endDate`, and `location.name`.
     - **Detail Meta Bar (`ul.events-detail-meta > li`)**: Identified by SVG sprite icon (`#icon-calendar`, `#icon-clock`, `#icon-poi`, `#icon-building`) so it works regardless of whether the page is in English or German. Parses date ranges (`November 6, 2026 – December 10, 2026`) and time ranges (`11:30 am – 2:00 pm`).
     - **Full Description**: Combines `.events-detail-description .rte-content` and `.events-detail-content .c-textmedia__bodytext` into clean multi-paragraph text.
     - **High-Res Image**: Reads `<meta property="og:image">` (falling back to `.events-detail-media img[src]`).
     - **Registration & iCal**: Detects external registration links in the body or an on-page TYPO3 Powermail registration form (`main form.powermail_form`), plus the `.ics` calendar export link (`a.events-detail-ics`).

---

## 4. CLI Usage

```bash
# 1. List all registered parsers and their intervals
PYTHONPATH=parsers python3 -m chn_parsers list

# 2. Dry-run a parser (prints normalised JSON to stdout; no database required)
PYTHONPATH=parsers python3 -m chn_parsers run --parser tum_chn_events --dry-run

# 3. Run all parsers once and write to PostgreSQL (ideal for cron / Cloud Run Jobs)
DATABASE_URL=postgresql://chn_scraper:chn_scraper_dev_pw@localhost:5432/chn_events \
  PYTHONPATH=parsers python3 -m chn_parsers run

# 4. Run the continuous background daemon (default in Docker)
DATABASE_URL=postgresql://chn_scraper:chn_scraper_dev_pw@localhost:5432/chn_events \
  PYTHONPATH=parsers python3 -m chn_parsers daemon
```

---

## 5. How to Add a New Parser

1. Copy the template:
   ```bash
   cp parsers/chn_parsers/sources/_template.py parsers/chn_parsers/sources/heilbronn_city.py
   ```
2. Edit `heilbronn_city.py`:
   - Uncomment `@register`.
   - Set `key` (lowercase letters, digits, underscores, e.g. `"heilbronn_city"`), `name`, `base_url`, and `interval`.
   - Implement `scrape(self) -> Iterator[Event]`.
3. Verify discovery and dry-run output:
   ```bash
   PYTHONPATH=parsers python3 -m chn_parsers list
   PYTHONPATH=parsers python3 -m chn_parsers run -p heilbronn_city --dry-run
   ```
4. Add an offline HTML fixture under `parsers/tests/fixtures/` and a test in `parsers/tests/`:
   ```bash
   PYTHONPATH=parsers python3 -m unittest discover -v -s parsers/tests
   ```
   When the parser first runs against PostgreSQL, `ingest.start_run()` automatically registers the new source in `core.sources`.

