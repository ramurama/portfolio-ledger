"""Broker-specific CSV/Excel parsers.

Public API:

    * `BrokerParser`            - abstract base every broker must extend.
    * `ScalableCapitalParser`   - concrete parser for Scalable Capital DE.
    * `detect_parser`           - registry helper that picks the right
                                  parser for a given file path.
"""

from app.parsers.base import BrokerParser
from app.parsers.registry import detect_parser, register_parser
from app.parsers.scalable_capital import ScalableCapitalParser

__all__ = [
    "BrokerParser",
    "ScalableCapitalParser",
    "detect_parser",
    "register_parser",
]
