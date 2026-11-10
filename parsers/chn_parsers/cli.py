"""
Command line interface.

    python -m chn_parsers list                      # show registered parsers
    python -m chn_parsers run --dry-run             # scrape, print JSON, no DB
    python -m chn_parsers run -p tum_chn_events     # scrape one parser into the DB
    python -m chn_parsers run                       # all parsers once, then exit (cron / Cloud Run Job)
    python -m chn_parsers daemon                    # run forever on each parser's interval (Docker)

Exit code of ``run``: 0 if no run failed, 1 otherwise (useful for alerts).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .base import BaseParser
from .config import Settings, load_settings
from .daemon import Daemon
from .http import HttpClient
from .registry import UnknownParserError, all_parsers, get_parser
from .runner import run_parser
from .sinks import JsonSink, PostgresSink, Sink


def _select_parsers(keys: list[str] | None, settings: Settings) -> dict[str, type[BaseParser]]:
    """CLI ``--parser`` wins over ``SCRAPER_ENABLED_PARSERS``; default = all."""
    wanted = keys or settings.enabled_parsers
    if not wanted:
        return all_parsers()
    return {key: get_parser(key) for key in wanted}


def _cmd_list(_args: argparse.Namespace, _settings: Settings) -> int:
    parsers = all_parsers()
    if not parsers:
        print("No parsers registered.")
    for parser_cls in parsers.values():
        print(parser_cls.describe())
    return 0


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    parsers = _select_parsers(args.parser, settings)

    sink: Sink
    if args.dry_run:
        sink = JsonSink(output=args.output)
    else:
        if not settings.database_url:
            print("DATABASE_URL is not set. Use --dry-run to scrape without a database.", file=sys.stderr)
            return 2
        sink = PostgresSink(settings.database_url)

    results = []
    try:
        with HttpClient(settings) as http:
            for parser_cls in parsers.values():
                results.append(run_parser(parser_cls, http, sink))
    finally:
        sink.close()

    for result in results:
        print(result.describe(), file=sys.stderr)
    return 0 if all(result.ok for result in results) else 1


def _cmd_daemon(_args: argparse.Namespace, settings: Settings) -> int:
    Daemon(settings, _select_parsers(None, settings)).run_forever()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chn_parsers", description="Campus Heilbronn event scrapers")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registered parsers").set_defaults(func=_cmd_list)

    run = sub.add_parser("run", help="run parsers once and exit")
    run.add_argument("-p", "--parser", action="append", metavar="KEY",
                     help="parser key to run (repeatable). Default: all / SCRAPER_ENABLED_PARSERS")
    run.add_argument("--dry-run", action="store_true", help="do not touch the database; print JSON instead")
    run.add_argument("-o", "--output", type=Path, help="with --dry-run: write JSON to this file")
    run.set_defaults(func=_cmd_run)

    sub.add_parser("daemon", help="run forever, each parser on its own interval").set_defaults(func=_cmd_daemon)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout stays clean for --dry-run JSON
    )
    args = build_arg_parser().parse_args(argv)
    try:
        return args.func(args, settings)
    except UnknownParserError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

