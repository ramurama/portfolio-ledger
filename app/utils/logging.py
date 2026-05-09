"""Application-wide logging configuration.

We keep this very simple on purpose: a single `configure_logging()`
entrypoint that the CLI calls once at startup, plus a `get_logger()`
helper that the rest of the codebase uses to obtain named loggers.

Using the standard library logger (rather than something like Rich
directly) means our log statements behave correctly when the tool is
run inside Docker, redirected to files, or wired into a CI pipeline.
"""

from __future__ import annotations

import logging
from typing import Final

_DEFAULT_FORMAT: Final[str] = (
    "%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s"
)


def configure_logging(verbose: bool = False) -> None:
    """Initialise the root logger.

    Idempotent: calling it twice (e.g. from tests and from the CLI) does
    not duplicate handlers, which would otherwise produce double output.
    """

    root = logging.getLogger()
    if root.handlers:
        # Already configured. Just adjust the level so `--verbose`
        # toggles work even on the second call.
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a logger scoped to the given dotted module name."""

    return logging.getLogger(name)
