"""
Zero-dependency stdlib fallback for ``BeautifulSoup`` / ``Tag``.

In Docker and production virtualenvs ``beautifulsoup4`` is installed via
``pyproject.toml`` and used automatically. This module only activates when
``bs4`` is absent (for example on a bare developer machine or CI runner without
``pip``), so ``python -m chn_parsers`` and the unit-test suite work anywhere
Python 3.11+ is present.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Iterator
from html.parser import HTMLParser

__all__ = ["BeautifulSoup", "Tag"]

_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_SIMPLE_SELECTOR_RE = re.compile(
    r"^(?P<tag>[a-zA-Z0-9_-]+|\*)?"
    r"(?P<classes>(?:\.[a-zA-Z0-9_-]+)*)"
    r"(?P<attrs>(?:\[[a-zA-Z0-9_:-]+(?:=['\"]?[^'\")\]]*['\"]?)?\])*)"
    r"(?::not\((?P<not_sel>[^)]+)\))?$"
)
_ATTR_RE = re.compile(r"\[(?P<name>[a-zA-Z0-9_:-]+)(?:=['\"]?(?P<val>[^'\")\]]*)['\"]?)?\]")


class Tag:
    """Minimal subset of ``bs4.element.Tag`` used by ``chn_parsers``."""

    def __init__(self, name: str, attrs: dict[str, str] | None = None, parent: Tag | None = None) -> None:
        self.name = name.lower()
        self.attrs: dict[str, str] = dict(attrs or {})
        self.parent: Tag | None = parent
        self.children: list[Tag | str] = []

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.attrs.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self.attrs[key]

    def __contains__(self, key: object) -> bool:
        return key in self.attrs

    @property
    def string(self) -> str | None:
        texts = [c for c in self.children if isinstance(c, str)]
        if len(texts) == len(self.children):
            return "".join(texts)
        return None

    def get_text(self, separator: str = "") -> str:
        parts: list[str] = []
        self._collect_text(parts)
        return separator.join(parts)

    def _collect_text(self, out: list[str]) -> None:
        for child in self.children:
            if isinstance(child, str):
                out.append(child)
            else:
                child._collect_text(out)

    def decompose(self) -> None:
        if self.parent is not None:
            self.parent.children = [c for c in self.parent.children if c is not self]
            self.parent = None
        self.children.clear()

    def replace_with(self, replacement: str | Tag) -> None:
        if self.parent is None:
            return
        idx = self.parent.children.index(self)
        self.parent.children[idx] = replacement
        if isinstance(replacement, Tag):
            replacement.parent = self.parent
        self.parent = None

    def find_parent(self, names: str | Iterable[str]) -> Tag | None:
        target = {names.lower()} if isinstance(names, str) else {n.lower() for n in names}
        curr = self.parent
        while curr is not None:
            if curr.name in target:
                return curr
            curr = curr.parent
        return None

    def iter_descendants(self) -> Iterator[Tag]:
        for child in list(self.children):
            if isinstance(child, Tag):
                yield child
                yield from child.iter_descendants()

    def find_all(
        self,
        name: str | Iterable[str] | None = None,
        attrs: dict[str, str] | None = None,
    ) -> list[Tag]:
        names = None if name is None else ({name.lower()} if isinstance(name, str) else {n.lower() for n in name})
        results: list[Tag] = []
        for node in self.iter_descendants():
            if names is not None and node.name not in names:
                continue
            if attrs and any(node.attrs.get(k) != v for k, v in attrs.items()):
                continue
            results.append(node)
        return results

    def find(
        self,
        name: str | Iterable[str] | None = None,
        attrs: dict[str, str] | None = None,
    ) -> Tag | None:
        matches = self.find_all(name, attrs)
        return matches[0] if matches else None

    def select(self, selector: str) -> list[Tag]:
        tokens = selector.replace(">", " > ").split()
        current: list[Tag] = [self]
        direct_child = False

        for token in tokens:
            if token == ">":
                direct_child = True
                continue
            next_nodes: list[Tag] = []
            seen_ids: set[int] = set()
            for base in current:
                candidates = (
                    (c for c in base.children if isinstance(c, Tag))
                    if direct_child
                    else base.iter_descendants()
                )
                for cand in candidates:
                    if id(cand) not in seen_ids and _matches_simple(cand, token):
                        seen_ids.add(id(cand))
                        next_nodes.append(cand)
            current = next_nodes
            direct_child = False
        return current

    def select_one(self, selector: str) -> Tag | None:
        matches = self.select(selector)
        return matches[0] if matches else None

    def __str__(self) -> str:
        return self._to_html()

    def _to_html(self) -> str:
        if self.name == "[document]":
            return "".join(c if isinstance(c, str) else c._to_html() for c in self.children)
        attrs_str = "".join(f' {k}="{v}"' for k, v in self.attrs.items())
        if self.name in _VOID_ELEMENTS:
            return f"<{self.name}{attrs_str}/>"
        inner = "".join(c if isinstance(c, str) else c._to_html() for c in self.children)
        return f"<{self.name}{attrs_str}>{inner}</{self.name}>"


def _matches_simple(node: Tag, token: str) -> bool:
    m = _SIMPLE_SELECTOR_RE.match(token)
    if not m:
        return False
    tag = m.group("tag")
    if tag and tag != "*" and node.name != tag.lower():
        return False
    classes = [c for c in (m.group("classes") or "").split(".") if c]
    if classes:
        node_classes = set(node.attrs.get("class", "").split())
        if not all(c in node_classes for c in classes):
            return False
    for attr_match in _ATTR_RE.finditer(m.group("attrs") or ""):
        attr_name = attr_match.group("name")
        attr_val = attr_match.group("val")
        if attr_name not in node.attrs:
            return False
        if attr_val is not None and node.attrs.get(attr_name) != attr_val:
            return False
    not_sel = m.group("not_sel")
    if not_sel and _matches_simple(node, not_sel):
        return False
    return True


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Tag("[document]")
        self._stack: list[Tag] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = Tag(tag, attr_dict, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag.lower() not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = Tag(tag, attr_dict, parent=self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].name == tag_l:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


class BeautifulSoup(Tag):
    """Drop-in stdlib replacement for ``bs4.BeautifulSoup(markup, "html.parser")``."""

    def __init__(self, markup: str | Tag, features: str = "html.parser") -> None:  # noqa: ARG002
        super().__init__("[document]")
        if isinstance(markup, Tag):
            cloned = copy.deepcopy(markup)
            self.children = [cloned]
            cloned.parent = self
        else:
            builder = _TreeBuilder()
            builder.feed(markup)
            builder.close()
            self.children = builder.root.children
            for child in self.children:
                if isinstance(child, Tag):
                    child.parent = self

