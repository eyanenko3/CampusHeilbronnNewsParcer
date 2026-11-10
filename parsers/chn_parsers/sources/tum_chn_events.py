"""
Parser for the official TUM Campus Heilbronn event calendar.

    Source:  https://chn.tum.de/events   (TYPO3 + "calendarize" + Solr search)

How the website is structured (verified November 2026)
------------------------------------------------------
1. LISTING pages (``/events``, ``/events?tx_solrevents[page]=2``, ...)
   contain one ``<article class="c-events-solr-list__item">`` per event with:
     * ``data-document-id="…/tx_calendarize_domain_model_event/885"``  -> stable id
     * ``.c-events-solr-list__item-title a``      -> title + detail URL
     * ``.c-events-solr-list__item-teaser``       -> one-line teaser
     * ``.c-events-solr-list__item-image img``    -> thumbnail
     * ``time[datetime="2026-11-06"]``            -> start DATE (no time!)
     * ``li.c-events-solr-list__item-tag``        -> audiences ("Current Students")
     * ``svg[title]`` next to "Read more"          -> venue
   Pagination: ``li.c-search__pagination-next a`` (absent on the last page).

2. DETAIL pages (``/event/event/<slug>``) add what students actually need:
     * JSON-LD ``{"@type": "Event", "startDate", "endDate", "location"}``
     * ``ul.events-detail-meta > li`` items, identified by their SVG icon:
         #icon-calendar -> "November 6, 2026 – December 10, 2026"
         #icon-clock    -> "11:30 am – 2:00 pm"            (the TIME)
         #icon-poi      -> venue
         #icon-building -> organiser ("Who?")
     * ``.events-detail-description .rte-content``  -> full description
     * ``<meta property="og:image">``                -> large image
     * ``a.events-detail-ics``                        -> iCalendar export
     * ``main form.powermail_form``                   -> on-page registration form

Strategy: walk all listing pages, then open every event's detail page and
merge both, preferring the richer detail data and falling back to the listing.
Icons (not the visible labels) identify the meta fields, so the parser works
for the English and German versions of the site alike.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import urldefrag, urlparse

from ..base import BaseParser
from ..models import Event
from ..registry import register
from ..utils.dates import combine, parse_dates, parse_iso_date, parse_time_range
from ..utils.html import BeautifulSoup, Tag, absolute_url, block_text, iter_json_ld, meta_content, text_of

LISTING_URL = "https://chn.tum.de/events"
#: Safety net against pagination loops / unexpected site changes.
MAX_PAGES = 25

_CALENDARIZE_ID_RE = re.compile(r"tx_calendarize_domain_model_event/(\d+)")
_REGISTRATION_HINT_RE = re.compile(r"regist|anmeld|sign[\s-]?up|ticket", re.IGNORECASE)

# Detail-page meta fields, keyed by SVG icon id (language independent) ...
_ICON_FIELDS = {
    "icon-calendar": "when",
    "icon-clock": "time",
    "icon-poi": "venue",
    "icon-building": "who",
}
# ... with the screen-reader labels as a fallback (EN + DE).
_LABEL_FIELDS = {
    "when": "when", "wann": "when", "date": "when", "datum": "when",
    "time": "time", "uhrzeit": "time", "zeit": "time",
    "venue": "venue", "ort": "venue", "where": "venue", "wo": "venue",
    "who": "who", "wer": "who",
}


@dataclass(slots=True)
class ListingItem:
    """What one card on the listing page tells us."""

    external_id: str
    url: str
    title: str
    teaser: str | None = None
    image_url: str | None = None
    start_date: date | None = None
    tags: list[str] = field(default_factory=list)
    location: str | None = None


@dataclass(slots=True)
class DetailInfo:
    """What the event's own page tells us (everything optional)."""

    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = None
    organizer: str | None = None
    description: str | None = None
    og_description: str | None = None
    image_url: str | None = None
    tags: list[str] = field(default_factory=list)
    registration_url: str | None = None
    registration_form_on_page: bool = False
    ics_url: str | None = None
    language: str | None = None


@register
class TumChnEventsParser(BaseParser):
    key = "tum_chn_events"
    name = "TUM Campus Heilbronn – Events"
    base_url = LISTING_URL
    interval = timedelta(hours=6)

    # ================================================================== #
    # Entry point                                                        #
    # ================================================================== #
    def scrape(self) -> Iterator[Event]:
        seen: set[str] = set()
        for item in self._iter_listing():
            if item.external_id in seen:  # same event listed twice (e.g. page shift)
                continue
            seen.add(item.external_id)

            event: Event | None = None
            with self.item_guard(item.url):
                detail = self._parse_detail(self.http.get_soup(item.url), item.url)
                event = self._merge(item, detail)
            if event is not None:
                yield event

    # ================================================================== #
    # Listing pages                                                      #
    # ================================================================== #
    def _iter_listing(self) -> Iterator[ListingItem]:
        """Yield cards from all listing pages, following "next page" links."""
        url: str | None = self.base_url
        visited: set[str] = set()

        while url and len(visited) < MAX_PAGES:
            visited.add(url)
            if len(visited) == 1:
                soup = self.http.get_soup(url)  # first page failing = whole run fails
            else:
                try:
                    soup = self.http.get_soup(url)
                except Exception as exc:  # noqa: BLE001
                    self.mark_incomplete(f"listing page {url} failed: {exc}")
                    return

            articles = soup.select("article.c-events-solr-list__item")
            if not articles and len(visited) == 1:
                self.log.warning("No event cards found on %s - empty calendar or changed layout?", url)

            for article in articles:
                item: ListingItem | None = None
                with self.item_guard(article.get("data-document-id") or url):
                    item = self._parse_listing_item(article, url)
                if item is not None:
                    yield item

            url = self._next_page_url(soup, url)
            if url in visited:
                break

        if url and url not in visited:
            self.mark_incomplete(f"stopped after MAX_PAGES={MAX_PAGES}")

    @staticmethod
    def _next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
        link = soup.select_one("li.c-search__pagination-next a[href]")
        if link is None:
            return None
        absolute = absolute_url(current_url, str(link["href"]))
        return urldefrag(absolute)[0] if absolute else None

    def _parse_listing_item(self, article: Tag, page_url: str) -> ListingItem:
        link = article.select_one(".c-events-solr-list__item-title a[href]")
        if link is None:
            raise ValueError("event card without title link")
        url = absolute_url(page_url, str(link["href"]))
        title = text_of(link)
        if not url or not title:
            raise ValueError("event card with empty title or URL")

        document_id = str(article.get("data-document-id") or "")
        match = _CALENDARIZE_ID_RE.search(document_id)
        external_id = f"calendarize-event-{match[1]}" if match else urlparse(url).path.rstrip("/")

        image = article.select_one(".c-events-solr-list__item-image img[src]")
        time_tag = article.select_one("time[datetime]")
        venue_icon = article.select_one(".c-events-solr-list__item-more svg[title]")

        return ListingItem(
            external_id=external_id,
            url=url,
            title=title,
            teaser=text_of(article.select_one(".c-events-solr-list__item-teaser")),
            image_url=absolute_url(page_url, str(image["src"])) if image else None,
            start_date=parse_iso_date(str(time_tag["datetime"])) if time_tag else None,
            tags=[
                tag
                for li in article.select(
                    "li.c-events-solr-list__item-tag:not(.c-events-solr-list__item-tag--more)"
                )
                if (tag := text_of(li))
            ],
            location=str(venue_icon["title"]).strip() if venue_icon else None,
        )

    # ================================================================== #
    # Detail page                                                        #
    # ================================================================== #
    def _parse_detail(self, soup: BeautifulSoup, page_url: str) -> DetailInfo:
        info = DetailInfo()

        # --- 1) schema.org JSON-LD: the most machine-friendly source --------
        for block in iter_json_ld(soup):
            types = block.get("@type")
            if types == "Event" or (isinstance(types, list) and "Event" in types):
                info.title = block.get("name") or None
                start_raw, end_raw = block.get("startDate"), block.get("endDate")
                info.start_date = parse_iso_date(start_raw)
                info.end_date = parse_iso_date(end_raw)
                # The site currently sends T00:00:00, i.e. no real time. Should it
                # ever send one, use it (it is overridden by the visible time below).
                info.start_time = _non_midnight_time(start_raw)
                info.end_time = _non_midnight_time(end_raw)
                location = block.get("location")
                if isinstance(location, dict):
                    info.location = location.get("name") or None
                break

        # --- 2) visible meta list (date range, time, venue, organiser) -------
        meta = self._read_meta_items(soup)
        if meta.get("when"):
            dates = parse_dates(meta["when"])
            if dates:
                info.start_date = info.start_date or dates[0]
                if len(dates) > 1:
                    info.end_date = info.end_date or dates[-1]
        if meta.get("time"):
            start_time, end_time = parse_time_range(meta["time"])
            if start_time:
                info.start_time, info.end_time = start_time, end_time
        info.location = meta.get("venue") or info.location
        info.organizer = meta.get("who")

        # --- 3) texts, images, tags -----------------------------------------
        title_tag = soup.select_one("h1.events-detail-title")
        info.title = text_of(title_tag) or info.title
        desc_Nodes = [
            *soup.select(".events-detail-description .rte-content"),
            *soup.select(".events-detail-content .c-textmedia__bodytext"),
        ]
        desc_parts = [part for node in desc_Nodes if (part := block_text(node))]
        info.description = "\n\n".join(desc_parts) or None
        info.og_description = meta_content(soup, "og:description")
        info.image_url = absolute_url(page_url, meta_content(soup, "og:image"))
        if not info.image_url:
            image = soup.select_one(".events-detail-media img[src]")
            info.image_url = absolute_url(page_url, str(image["src"])) if image else None
        info.tags = [t for li in soup.select("li.events-detail-tags__item") if (t := text_of(li))]

        # --- 4) registration + calendar export ----------------------------------
        for link in soup.select(".events-detail a[href]"):
            href = absolute_url(page_url, str(link["href"]))
            if href and (_REGISTRATION_HINT_RE.search(text_of(link) or "") or _REGISTRATION_HINT_RE.search(href)):
                info.registration_url = href
                break
        info.registration_form_on_page = soup.select_one("main form.powermail_form") is not None
        ics = soup.select_one("a.events-detail-ics[href]")
        info.ics_url = absolute_url(page_url, str(ics["href"])) if ics else None

        html_tag = soup.find("html")
        lang = html_tag.get("lang") if isinstance(html_tag, Tag) else None
        info.language = str(lang).split("-")[0].lower() if lang else None
        return info

    @staticmethod
    def _read_meta_items(soup: BeautifulSoup) -> dict[str, str]:
        """Map ``ul.events-detail-meta`` entries to when/time/venue/who."""
        result: dict[str, str] = {}
        for li in soup.select("ul.events-detail-meta > li"):
            field_name = None
            use = li.find("use")
            if isinstance(use, Tag):
                href = str(use.get("xlink:href") or use.get("href") or "")
                field_name = _ICON_FIELDS.get(href.rsplit("#", 1)[-1])
            if field_name is None:  # fallback: visually hidden label, e.g. "When?:"
                label = text_of(li.select_one(".u-visually-hidden")) or ""
                field_name = _LABEL_FIELDS.get(re.sub(r"[^a-zäöü]", "", label.lower()))
            value = text_of(li, exclude_selector=".u-visually-hidden")
            if field_name and value and field_name not in result:
                result[field_name] = value
        return result

    # ================================================================== #
    # Merge listing + detail -> Event                                    #
    # ================================================================== #
    def _merge(self, item: ListingItem, detail: DetailInfo) -> Event:
        start_date = detail.start_date or item.start_date
        if start_date is None:
            raise ValueError("no start date on listing or detail page")
        end_date = detail.end_date if detail.end_date and detail.end_date > start_date else None
        tz = self.timezone

        starts_at, ends_at, all_day = _build_datetimes(
            start_date, end_date, detail.start_time, detail.end_time, tz
        )

        registration_url = detail.registration_url
        if registration_url is None and detail.registration_form_on_page:
            registration_url = item.url  # the sign-up form lives on the event page

        extra: dict[str, object] = {}
        if detail.ics_url:
            extra["ics_url"] = detail.ics_url
        if detail.registration_form_on_page:
            extra["registration_form_on_page"] = True

        return Event(
            external_id=item.external_id,
            title=detail.title or item.title,
            url=item.url,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            summary=item.teaser or detail.og_description,
            description=detail.description or detail.og_description or item.teaser,
            image_url=detail.image_url or item.image_url,
            registration_url=registration_url,
            location=detail.location or item.location,
            organizer=detail.organizer,
            language=detail.language,
            tags=detail.tags or item.tags,
            extra=extra,
        )


# ---------------------------------------------------------------------- #
# Pure helpers (easy to unit test)                                       #
# ---------------------------------------------------------------------- #
def _non_midnight_time(value: object) -> time | None:
    """'2026-11-06T18:30:00' -> time(18, 30); midnight / date-only -> None."""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return None if parsed.time() == time.min else parsed.time().replace(tzinfo=None)


def _build_datetimes(
    start_date: date,
    end_date: date | None,
    start_time: time | None,
    end_time: time | None,
    tz,
) -> tuple[datetime, datetime | None, bool]:
    """Turn the date/time pieces into (starts_at, ends_at, all_day).

    Cases:
      * no time at all            -> all-day event (Event normalises the bounds)
      * start time only           -> ends_at unknown, or end of the last day
                                     for multi-day events
      * start + end time          -> end time applies to the last day; if it is
                                     earlier than the start on the same day the
                                     event runs past midnight.
    """
    if start_time is None:
        starts_at = combine(start_date, time.min, tz)
        ends_at = combine(end_date, time.min, tz) if end_date else None
        return starts_at, ends_at, True

    starts_at = combine(start_date, start_time, tz)
    last_day = end_date or start_date
    if end_time is not None:
        ends_at = combine(last_day, end_time, tz)
        if ends_at <= starts_at:
            ends_at += timedelta(days=1)  # e.g. 22:00 – 02:00
    elif end_date is not None:
        ends_at = combine(end_date, time(23, 59, 59), tz)
    else:
        ends_at = None
    return starts_at, ends_at, False

