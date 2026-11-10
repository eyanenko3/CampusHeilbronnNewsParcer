"""
TEMPLATE for a new parser - copy me!

    cp chn_parsers/sources/_template.py chn_parsers/sources/my_source.py

Files starting with "_" are ignored by the registry, so this template is never
executed. After copying:

  1. Rename the class, set ``key`` / ``name`` / ``base_url``.
  2. Uncomment ``@register``.
  3. Implement ``scrape()``.
  4. Save one real page under tests/fixtures/ and write a test (see
     tests/test_tum_chn_events.py for the pattern).
  5. Try it without a database:
         python -m chn_parsers run --parser my_source --dry-run

Rules of thumb
--------------
* Prefer structured data when it exists: JSON-LD (``iter_json_ld``), iCal
  feeds, RSS, a public JSON API. Fall back to CSS selectors.
* ``external_id`` must be stable between runs. Use the source's own id; the
  URL path is a good second choice. NEVER use list positions or today's date.
* Times must be timezone-aware. Use ``combine(day, time, self.timezone)``.
* If only a date is known, set ``all_day=True``.
* Wrap each item in ``self.item_guard(...)``; call ``self.mark_incomplete()``
  if you could not read the whole listing.
* Be polite: always go through ``self.http`` (rate limit + robots.txt).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time, timedelta

from ..base import BaseParser
from ..models import Event
from ..registry import register  # noqa: F401  (used once you uncomment @register)
from ..utils.dates import combine, parse_dates, parse_time_range
from ..utils.html import absolute_url, text_of


# @register
class ExampleParser(BaseParser):
    key = "example_source"  # lowercase, digits, underscores; never change later
    name = "Example Organisation – Events"
    base_url = "https://example.org/events"
    interval = timedelta(hours=12)

    def scrape(self) -> Iterator[Event]:
        soup = self.http.get_soup(self.base_url)

        for card in soup.select(".event-card"):
            event: Event | None = None
            with self.item_guard(card.get("data-id") or "?"):
                link = card.select_one("a[href]")
                url = absolute_url(self.base_url, link["href"] if link else None)
                day = parse_dates(text_of(card.select_one(".date")))[0]
                start, end = parse_time_range(text_of(card.select_one(".time")))

                event = Event(
                    external_id=str(card["data-id"]),
                    title=text_of(card.select_one("h3")) or "",
                    url=url or self.base_url,
                    starts_at=combine(day, start or time.min, self.timezone),
                    ends_at=combine(day, end, self.timezone) if end else None,
                    all_day=start is None,
                    description=text_of(card.select_one(".description")),
                    image_url=absolute_url(self.base_url, (card.select_one("img") or {}).get("src")),
                    tags=[t for li in card.select(".tag") if (t := text_of(li))],
                )
            if event is not None:
                yield event

