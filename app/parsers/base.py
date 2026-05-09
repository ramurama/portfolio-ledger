"""Abstract base class for all broker parsers.

A parser's only job is to turn a raw broker file into a list of
`Transaction` objects. It must NOT perform FIFO calculations, file IO
beyond reading the source file, or reporting. Keeping this contract
narrow is what lets us add a new broker by writing a single class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from app.models import Transaction


class BrokerParser(ABC):
    """Contract every concrete broker parser must satisfy."""

    #: Short identifier used in logs and the parser registry, e.g. "scalable_capital".
    broker_id: str = ""

    #: Human-readable name surfaced in error messages.
    broker_name: str = ""

    @classmethod
    @abstractmethod
    def can_parse(cls, path: Path) -> bool:
        """Quick check: does this file look like a `cls` export?

        Implementations should be cheap (read a few lines, sniff the
        header) so the registry can ask every parser without paying for
        a full file read.
        """

    @abstractmethod
    def parse(self, path: Path, account_name: str) -> Iterable[Transaction]:
        """Yield `Transaction` objects for every supported row in `path`.

        The parser is responsible for filtering out unsupported rows
        (Cash Transfers, Fees, Interest, etc). It should not raise on
        individual malformed rows - log and skip instead, so a single
        bad line does not abort an entire historical export.
        """
