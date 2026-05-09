"""Walk the input directory and collect all transactions.

The input directory is laid out as one folder per account holder:

    input/
        ramu/
            2026-04-01_10-00-00_ScalableCapital-Broker-Transactions.csv
            2026-05-09_11-05-03_ScalableCapital-Broker-Transactions.csv  <-- newest
        rakshana/
            ...

Latest-file selection
---------------------
Scalable Capital exports are *cumulative* - every export contains the
full history up to and including the export date. Processing several
exports therefore double-counts transactions and corrupts tax-lot output.

To avoid that, the ingestion service keeps **only the newest export
per account folder**. "Newest" is read from the `YYYY-MM-DD` prefix in
the filename (optionally followed by `_HH-MM-SS` for same-day exports),
NOT from filesystem mtime - file copies / git checkouts can scramble
mtimes but the timestamp embedded in the export filename is stable.

Files whose names do not start with a parseable timestamp are still
*considered* (so manually-renamed exports continue to work) but they
sort last; the latest-by-name file always wins when one is present.

Hidden files (`.DS_Store`, etc.) are silently skipped. Files for which
no parser is registered are logged and recorded under `files_skipped`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from app.config import INPUT_DIR
from app.models import Transaction
from app.parsers import detect_parser
from app.services.transfer_pairs import collapse_switch_pairs
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Filename timestamp extraction
# ---------------------------------------------------------------------------
# Matches "YYYY-MM-DD" optionally followed by "_HH-MM-SS". Capturing the
# whole prefix as a single string lets us sort lexically - the format is
# already monotonic so we don't need to parse to a real datetime.
_FILENAME_TIMESTAMP_RE: re.Pattern[str] = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}(?:_\d{2}-\d{2}-\d{2})?)"
)


def extract_source_date(path: Path) -> Optional[datetime]:
    """Read the export date out of `path`'s filename.

    Recognises both prefixes the broker uses:

        "2026-05-09_..."           -> datetime(2026, 5, 9)
        "2026-05-09_11-05-03_..."  -> datetime(2026, 5, 9, 11, 5, 3)

    Returns `None` when the filename does not start with a parseable
    timestamp - in that case the report just omits the source-date
    band rather than fabricating a value.
    """

    match = _FILENAME_TIMESTAMP_RE.match(path.name)
    if not match:
        return None

    stamp = match.group("stamp")
    fmt = "%Y-%m-%d_%H-%M-%S" if "_" in stamp else "%Y-%m-%d"
    try:
        return datetime.strptime(stamp, fmt)
    except ValueError:
        # Defensive: regex shape matched but the values are not a
        # valid date (e.g. month 13). Skip silently.
        return None


def _filename_sort_key(path: Path) -> tuple[int, str, str]:
    """Build a sort key that orders files by embedded timestamp.

    Returns a 3-tuple `(rank, stamp, name)`:

        * `rank`  - 0 for files with a parseable date prefix, 1 otherwise.
                    This guarantees timestamped files always rank ahead
                    of un-timestamped ones, no matter their alphabetical
                    relationship.
        * `stamp` - the matched "YYYY-MM-DD[_HH-MM-SS]" string (empty
                    when missing). Lexical comparison on this string is
                    equivalent to chronological comparison.
        * `name`  - the full filename, used only as a tie-breaker so the
                    ordering is deterministic for files sharing a stamp.
    """

    match = _FILENAME_TIMESTAMP_RE.match(path.name)
    if match:
        return (0, match.group("stamp"), path.name)
    return (1, "", path.name)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class IngestionResult:
    """Summary of an ingestion pass.

    `transactions` is sorted chronologically and is the only field the
    tax-lot engine cares about. The other fields exist so the CLI can
    print a useful summary and so tests can assert on file coverage.
    """

    transactions: list[Transaction]
    accounts: list[str] = field(default_factory=list)
    files_processed: list[Path] = field(default_factory=list)
    files_skipped: list[Path] = field(default_factory=list)
    # account_name -> datetime extracted from the chosen export's
    # filename. Populated only for files whose name follows the
    # documented `YYYY-MM-DD[_HH-MM-SS]` convention; missing entries
    # are intentional rather than an error.
    source_dates: dict[str, datetime] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------
def ingest_input_directory(
    input_dir: Optional[Path] = None,
    account_filter: Optional[str] = None,
) -> IngestionResult:
    """Top-level entrypoint used by the CLI.

    Parameters
    ----------
    input_dir:
        Override for the configured `INPUT_DIR` - useful in tests.
    account_filter:
        Optional account name. When provided, only that sub-folder is
        ingested. Matching is case-sensitive to mirror filesystem
        semantics on Linux containers.
    """

    base = input_dir or INPUT_DIR
    if not base.exists():
        raise FileNotFoundError(f"Input directory does not exist: {base}")

    result = IngestionResult(transactions=[])

    for account_dir in _iter_account_dirs(base):
        if account_filter and account_dir.name != account_filter:
            logger.debug(
                "Skipping account %s (filter=%s)", account_dir.name, account_filter
            )
            continue

        result.accounts.append(account_dir.name)
        _ingest_account(account_dir, result)

    if account_filter and account_filter not in result.accounts:
        # Surface a clear error rather than silently producing an
        # empty report for a typo'd account name.
        raise ValueError(
            f"Account folder {account_filter!r} not found in {base}"
        )

    # Chronological order is a precondition of the tax-lot engine. We
    # sort here once so the engine can stay simple.
    result.transactions.sort(key=lambda tx: tx.date)

    # Drop paired Security transfer legs that represent a broker-internal
    # sub-account "switch". They preserve the original tax lots at the
    # broker, so the tax-lot engine must never observe them - otherwise
    # the cheap historical lots would be popped and replaced with a
    # single expensive lot at the inbound day's price, corrupting every
    # subsequent realized-gain calculation. See `transfer_pairs` for
    # the detection rule.
    before = len(result.transactions)
    result.transactions = collapse_switch_pairs(result.transactions)
    collapsed = before - len(result.transactions)

    logger.info(
        "Ingested %d transactions from %d account(s); processed %d file(s), "
        "skipped %d file(s); collapsed %d switch-pair leg(s).",
        len(result.transactions),
        len(result.accounts),
        len(result.files_processed),
        len(result.files_skipped),
        collapsed,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iter_account_dirs(base: Path) -> Iterable[Path]:
    """Yield direct sub-directories of `base`, ignoring hidden ones."""

    for entry in sorted(base.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            yield entry


def select_latest_export(account_dir: Path) -> Optional[Path]:
    """Return the newest non-hidden file in `account_dir`, or None.

    The decision is based on the `YYYY-MM-DD[_HH-MM-SS]` prefix in the
    filename (see `_filename_sort_key` for the exact ranking rules).
    Empty folders return None - the caller logs and moves on.
    """

    candidates: list[Path] = [
        p for p in account_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    ]
    if not candidates:
        return None

    # `max` with our sort key picks the latest stamp. We invert the
    # rank component (timestamped first) by sorting ascending and
    # taking the *last* element instead - that way un-timestamped
    # files only win when they are the only option present.
    candidates.sort(key=_filename_sort_key)

    # If the folder contains both timestamped and un-timestamped files,
    # the timestamped ones (rank 0) sort first; we want the latest
    # timestamped file, which is the last rank-0 entry.
    timestamped = [p for p in candidates if _filename_sort_key(p)[0] == 0]
    if timestamped:
        return timestamped[-1]

    # Fallback: no file has a date prefix at all - take the last entry
    # by name so behaviour is deterministic.
    return candidates[-1]


def _ingest_account(account_dir: Path, result: IngestionResult) -> None:
    """Parse the latest supported export in `account_dir` into `result`.

    Older / superseded exports in the same folder are recorded under
    `files_skipped` so the operator can verify which file actually drove
    the report.
    """

    account_name = account_dir.name
    latest = select_latest_export(account_dir)

    # Record every other file as "skipped" up-front so the summary is
    # accurate even if the chosen file fails to parse below.
    for path in sorted(account_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path != latest:
            logger.info(
                "Skipping superseded export for %s: %s",
                account_name, path.name,
            )
            result.files_skipped.append(path)

    if latest is None:
        logger.warning("No exports found for account %s - skipping.", account_name)
        return

    parser = detect_parser(latest)
    if parser is None:
        logger.warning("No parser available for %s - skipping.", latest)
        result.files_skipped.append(latest)
        return

    try:
        transactions = list(parser.parse(latest, account_name))
    except Exception:
        # Re-raise after logging so the operator sees which file
        # actually failed - silent corruption of historical data
        # would be far worse than a noisy crash.
        logger.exception("Failed to parse %s", latest)
        raise

    result.transactions.extend(transactions)
    result.files_processed.append(latest)

    # Capture the export date so reports can show "data as of ..." -
    # crucial for tax/audit traceability when reports are filed away.
    source_date = extract_source_date(latest)
    if source_date is not None:
        result.source_dates[account_name] = source_date

    logger.info(
        "  - %s -> %d transaction(s) [latest export selected]",
        latest.name, len(transactions),
    )
