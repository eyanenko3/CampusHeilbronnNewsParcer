"""
chn_parsers - deterministic web scrapers for TUM Campus Heilbronn events.

Layout:
    models.py    Event: the validated data contract every parser returns
    base.py      BaseParser: the class every parser inherits from
    registry.py  @register + auto-discovery of chn_parsers/sources/*.py
    sources/     ONE MODULE PER WEBSITE  <- this is where new parsers go
    http.py      polite HTTP client (rate limit, retries, robots.txt)
    utils/       date/time + HTML helpers
    sinks.py     where events go: PostgreSQL (ingest.* SQL API) or JSON
    runner.py    runs one parser into one sink, decides run status
    daemon.py    long-running scheduler
    cli.py       command line entry point (python -m chn_parsers ...)
"""

__version__ = "0.1.0"

