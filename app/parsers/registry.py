"""Parser auto-detection registry.

The registry knows about every concrete parser and offers a single
`detect_parser(path)` helper. The CLI does not have to care which
broker exported a particular file - new brokers can be added in one
place by registering them here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.parsers.base import BrokerParser
from app.parsers.scalable_capital import ScalableCapitalParser

# Mutable so tests / future plugins can extend it via `register_parser`.
_PARSERS: list[type[BrokerParser]] = [
    ScalableCapitalParser,
]


def register_parser(parser_cls: type[BrokerParser]) -> None:
    """Add a new parser implementation to the registry.

    Parsers are queried in registration order, so register more
    specific parsers before more generic ones if there is overlap.
    """

    if parser_cls not in _PARSERS:
        _PARSERS.append(parser_cls)


def detect_parser(path: Path) -> Optional[BrokerParser]:
    """Return an instantiated parser that can read `path`, or None.

    None signals "we have no parser for this file" - callers typically
    log a warning and skip the file.
    """

    for parser_cls in _PARSERS:
        if parser_cls.can_parse(path):
            return parser_cls()
    return None
