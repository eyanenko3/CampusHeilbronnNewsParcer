"""
Parser for the official TUM Campus Heilbronn News & Stories feed.

    Source:  https://chn.tum.de/stories/news   (TYPO3 tx_news + Solr search)

How the website is structured (verified November 2026)
------------------------------------------------------
1. LISTING pages (``/stories/news``, ``/stories/news?tx_solrnews[page]=2``, ...)
   contain one ``<article class="c-news-solr-list__item">`` per story with:
     * ``data-document-id="…/tx_news_domain_model_news/1022"`` -> stable id
     * ``.c-news-solr-list__item-title a``  -> title + detail URL
     * ``.c-news-solr-list__item-teaser``   -> teaser paragraph
     * ``.c-news-solr-list__item-image img``-> thumbnail image
     * ``time[datetime="2026-11-05"]``      -> publication date
     * ``li.c-news-solr-list__item-tag``    -> format & topic tags
       (e.g. "Magazine Article", "News", "AI Competence", "Research")

2. DETAIL pages (``/stories/...``) provide the full article:
     * JSON-LD ``{"@type": "NewsArticle", "headline", "datePublished", "dateModified", "image"}``
     * ``h1.news-detail-title``                       -> full headline
     * ``li.news-detail-tags__item``                  -> category & topic tags
     * ``.news-detail-image img[src]`` / ``og:image`` -> high-resolution image
     * ``.news-detail-description .rte-content``      -> lead summary
     * ``.news-detail-bodytext .rte-content``         -> full multi-paragraph article text
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
from ..utils.dates import combine, parse_iso_date
from ..utils.html import BeautifulSoup, Tag, absolute_url, block_text, iter_json_ld, meta_content, text_of

NEWS_LISTING_URL = "https://chn.tum.de/stories/news"
#: News archive has 25+ pages of multi-year history; scrape the latest page (10 articles) per run by default.
DEFAULT_MAX_PAGES = 1

_TX_NEWS_ID_RE = re.compile(r"tx_news_domain_model_news/(\d+)")


@dataclass(slots=True)
class NewsListingItem:
    external_id: str
    url: str
    title: str
    teaser: str | None = None
    image_url: str | None = None
    published_date: date | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NewsDetailInfo:
    title: str | None = None
    published_at: datetime | None = None
    published_date: date | None = None
    summary: str | None = None
    body: str | None = None
    image_url: str | None = None
    tags: list[str] = field(default_factory=list)
    language: str | None = None


@register
class TumChnNewsParser(BaseParser):
    key = "tum_chn_news"
    name = "TUM Campus Heilbronn – News & Stories"
    base_url = NEWS_LISTING_URL
    interval = timedelta(hours=6)
    max_pages: int = DEFAULT_MAX_PAGES

    def scrape(self) -> Iterator[Event]:
        seen: set[str] = set()
        for item in self._iter_listing():
            if item.external_id in seen:
                continue
            seen.add(item.external_id)

            article: Event | None = None
            with self.item_guard(item.url):
                detail = self._parse_detail(self.http.get_soup(item.url), item.url)
                article = self._merge(item, detail)
            if article is not None:
                yield article

    # ------------------------------------------------------------------ #
    # Listing pages                                                      #
    # ------------------------------------------------------------------ #
    def _iter_listing(self) -> Iterator[NewsListingItem]:
        url: str | None = self.base_url
        visited: set[str] = set()

        while url and len(visited) < self.max_pages:
            visited.add(url)
            if len(visited) == 1:
                soup = self.http.get_soup(url)
            else:
                try:
                    soup = self.http.get_soup(url)
                except Exception as exc:  # noqa: BLE001
                    self.mark_incomplete(f"news listing page {url} failed: {exc}")
                    return

            for card in soup.select("article.c-news-solr-list__item"):
                item: NewsListingItem | None = None
                with self.item_guard(card.get("data-document-id") or url):
                    item = self._parse_listing_item(card, url)
                if item is not None:
                    yield item

            url = self._next_page_url(soup, url)
            if url in visited:
                break

    @staticmethod
    def _next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
        link = soup.select_one("li.c-search__pagination-next a[href]")
        if link is None:
            return None
        absolute = absolute_url(current_url, str(link["href"]))
        return urldefrag(absolute)[0] if absolute else None

    def _parse_listing_item(self, article: Tag, page_url: str) -> NewsListingItem:
        link = article.select_one(".c-news-solr-list__item-title a[href]")
        if link is None:
            raise ValueError("news card without title link")
        url = absolute_url(page_url, str(link["href"]))
        title = text_of(link)
        if not url or not title:
            raise ValueError("news card with empty title or URL")

        doc_id = str(article.get("data-document-id") or "")
        match = _TX_NEWS_ID_RE.search(doc_id)
        external_id = f"tx-news-{match[1]}" if match else urlparse(url).path.rstrip("/")

        image = article.select_one(".c-news-solr-list__item-image img[src]")
        time_tag = article.select_one("time[datetime]")

        return NewsListingItem(
            external_id=external_id,
            url=url,
            title=title,
            teaser=text_of(article.select_one(".c-news-solr-list__item-teaser")),
            image_url=absolute_url(page_url, str(image["src"])) if image else None,
            published_date=parse_iso_date(str(time_tag["datetime"])) if time_tag else None,
            tags=[
                tag
                for li in article.select(
                    "li.c-news-solr-list__item-tag:not(.c-news-solr-list__item-tag--more)"
                )
                if (tag := text_of(li))
            ],
        )

    # ------------------------------------------------------------------ #
    # Detail page                                                        #
    # ------------------------------------------------------------------ #
    def _parse_detail(self, soup: BeautifulSoup, page_url: str) -> NewsDetailInfo:
        info = NewsDetailInfo()

        for block in iter_json_ld(soup):
            types = block.get("@type")
            if types == "NewsArticle" or (isinstance(types, list) and "NewsArticle" in types):
                info.title = block.get("headline") or None
                pub_raw = block.get("datePublished")
                if isinstance(pub_raw, str):
                    info.published_date = parse_iso_date(pub_raw)
                    try:
                        dt = datetime.fromisoformat(pub_raw)
                        info.published_at = dt if dt.tzinfo else dt.replace(tzinfo=self.timezone)
                    except ValueError:
                        pass
                images = block.get("image")
                if isinstance(images, list) and images:
                    info.image_url = absolute_url(page_url, str(images[0]))
                elif isinstance(images, str):
                    info.image_url = absolute_url(page_url, images)
                break

        title_tag = soup.select_one("h1.news-detail-title")
        info.title = text_of(title_tag) or info.title

        lead_node = soup.select_one(".news-detail-description .rte-content")
        info.summary = block_text(lead_node) or meta_content(soup, "og:description")

        body_nodes = [
            *soup.select(".news-detail-bodytext .rte-content"),
            *soup.select(".news-detail .c-textmedia__bodytext"),
        ]
        body_parts = [part for node in body_nodes if (part := block_text(node))]
        info.body = "\n\n".join(body_parts) or None

        if not info.image_url:
            img = soup.select_one(".news-detail-image img[src]")
            info.image_url = (
                absolute_url(page_url, str(img["src"]))
                if img
                else absolute_url(page_url, meta_content(soup, "og:image"))
            )

        info.tags = [t for li in soup.select("li.news-detail-tags__item") if (t := text_of(li))]

        html_tag = soup.find("html")
        lang = html_tag.get("lang") if isinstance(html_tag, Tag) else None
        info.language = str(lang).split("-")[0].lower() if lang else None
        return info

    # ------------------------------------------------------------------ #
    # Merge listing + detail -> Event                                    #
    # ------------------------------------------------------------------ #
    def _merge(self, item: NewsListingItem, detail: NewsDetailInfo) -> Event:
        if detail.published_at is not None:
            starts_at = detail.published_at
            all_day = False
        else:
            pub_date = detail.published_date or item.published_date
            if pub_date is None:
                raise ValueError("news article has no publication date")
            starts_at = combine(pub_date, time.min, self.timezone)
            all_day = True

        summary = detail.summary or item.teaser
        description = detail.body or summary
        if summary and detail.body and not detail.body.startswith(summary[:40]):
            description = f"{summary}\n\n{detail.body}"

        tags = detail.tags or item.tags
        if "News" not in tags and "Magazine Article" not in tags:
            tags = ["News", *tags]

        return Event(
            external_id=item.external_id,
            title=detail.title or item.title,
            url=item.url,
            starts_at=starts_at,
            all_day=all_day,
            summary=summary,
            description=description,
            image_url=detail.image_url or item.image_url,
            organizer="TUM Campus Heilbronn",
            language=detail.language,
            tags=tags,
            extra={"kind": "news"},
        )
