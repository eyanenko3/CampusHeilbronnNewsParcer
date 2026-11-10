"""
Parser registry: the plug-in mechanism.

Adding a parser = adding ONE file to ``chn_parsers/sources/`` that defines a
``BaseParser`` subclass decorated with ``@register``. :func:`discover` imports
every module in that package, which executes the decorators and fills the
registry. Nothing else (CLI, daemon, Docker, database) needs to change.
"""

from __future__ import annotations

import importlib
import pkgutil
import re

from .base import BaseParser

__all__ = ["register", "discover", "get_parser", "all_parsers", "UnknownParserError"]

_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_REGISTRY: dict[str, type[BaseParser]] = {}
_discovered = False


class UnknownParserError(KeyError):
    pass


def register(cls: type[BaseParser]) -> type[BaseParser]:
    """Class decorator that makes a parser available to the CLI / daemon."""
    for attribute in ("key", "name", "base_url"):
        if not getattr(cls, attribute, None):
            raise TypeError(f"{cls.__name__} must define the class attribute '{attribute}'")
    if not _KEY_RE.match(cls.key):
        raise TypeError(f"{cls.__name__}.key={cls.key!r} must match {_KEY_RE.pattern}")
    existing = _REGISTRY.get(cls.key)
    if existing is not None and existing is not cls:
        raise TypeError(f"Duplicate parser key {cls.key!r}: {existing.__qualname__} and {cls.__qualname__}")
    _REGISTRY[cls.key] = cls
    return cls


def discover() -> None:
    """Import every module in ``chn_parsers.sources`` (once).

    Modules whose name starts with an underscore (e.g. ``_template.py``) are
    skipped, so templates and private helpers are never registered.
    """
    global _discovered
    if _discovered:
        return
    from . import sources  # local import: the package itself must exist

    for module in pkgutil.iter_modules(sources.__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{sources.__name__}.{module.name}")
    _discovered = True


def all_parsers() -> dict[str, type[BaseParser]]:
    discover()
    return dict(sorted(_REGISTRY.items()))


def get_parser(key: str) -> type[BaseParser]:
    parsers = all_parsers()
    try:
        return parsers[key]
    except KeyError:
        known = ", ".join(parsers) or "<none>"
        raise UnknownParserError(f"Unknown parser {key!r}. Known parsers: {known}") from None

