"""
Small BeautifulSoup helpers that keep parser code short and readable.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

try:
    from bs4 import BeautifulSoup, Tag
except ModuleNotFoundError:  # pragma: no cover - stdlib fallback when bs4 is absent
    from ._compat_soup import BeautifulSoup, Tag  # type: ignore[no-redef]

__all__ = ["BeautifulSoup", "Tag", "text_of", "absolute_url", "block_text", "iter_json_ld", "meta_content"]

log = logging.getLogger(__name__)

_MULTI_SLASH = re.compile(r"/{2,}")


def text_of(node: Tag | None, *, exclude_selector: str | None = None) -> str | None:
    """Visible text of ``node`` with whitespace collapsed (``None`` if empty).

    ``exclude_selector`` removes matching children first - handy for screen
    reader-only labels such as ``<span class="u-visually-hidden">Time:</span>``.
    """
    if node is None:
        return None
    if exclude_selector:
        node = BeautifulSoup(str(node), "html.parser")  # copy, never mutate the caller's tree
        for hidden in node.select(exclude_selector):
            hidden.decompose()
    text = " ".join(node.get_text(" ").split())
    return text or None


def block_text(node: Tag | None) -> str | None:
    """Text of a rich-text block, keeping one paragraph per line-pair.

    ``<p>A</p><p>B<br>C</p>`` -> ``"A\\n\\nB\\nC"``
    """
    if node is None:
        return None
    node = BeautifulSoup(str(node), "html.parser")
    for br in node.find_all("br"):
        br.replace_with("\n")
    block_tags = ["p", "li", "h2", "h3", "h4", "h5", "h6"]
    # Outermost blocks only, so <li><p>x</p></li> is not emitted twice.
    blocks = [b for b in node.find_all(block_tags) if b.find_parent(block_tags) is None]
    if not blocks:
        parts = [node.get_text()]
    else:
        parts = [block.get_text() for block in blocks]
    cleaned = ["\n".join(" ".join(line.split()) for line in part.splitlines()).strip() for part in parts]
    text = "\n\n".join(part for part in cleaned if part)
    return text or None


def absolute_url(base: str, href: str | None) -> str | None:
    """Resolve ``href`` against ``base`` and tidy accidental ``//`` in the path
    (e.g. ``https://chn.tum.de//fileadmin/x.jpg``)."""
    if not href or not href.strip() or href.strip().startswith(("javascript:", "mailto:", "#")):
        return None
    parsed = urlparse(urljoin(base, href.strip()))
    if parsed.scheme not in ("http", "https"):
        return None
    return urlunparse(parsed._replace(path=_MULTI_SLASH.sub("/", parsed.path)))


def meta_content(soup: BeautifulSoup, key: str) -> str | None:
    """``<meta property|name="key" content="...">`` -> content."""
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    content = tag.get("content") if isinstance(tag, Tag) else None
    return content.strip() if isinstance(content, str) and content.strip() else None


def iter_json_ld(soup: BeautifulSoup) -> Iterator[dict[str, Any]]:
    """Yield every JSON-LD object on the page, flattening ``@graph`` and lists.

    Structured data (schema.org) is the most stable thing to scrape: it is
    meant for machines and rarely changes when the visual design changes.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            log.debug("Ignoring invalid JSON-LD block")
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                yield item
