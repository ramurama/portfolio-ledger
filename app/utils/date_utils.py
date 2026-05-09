"""Date and time parsing helpers.

Scalable Capital exports the trade date and trade time as two separate
columns (`date` and `time`). For accurate tax-lot ordering we want a
single sortable `datetime` value, so this module provides one canonical
parser that combines both columns.

We intentionally use `dateutil.parser` for its lenient handling - even
if the broker tweaks the export format (e.g. drops seconds) we still
get a sensible result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from dateutil import parser as dateutil_parser


def parse_broker_datetime(date_str: str, time_str: Optional[str]) -> datetime:
    """Combine a `YYYY-MM-DD` date with an optional `HH:MM:SS` time.

    Some rows (e.g. legacy migrations) come with no time component. We
    default to midnight in that case so the row still slots into the
    chronological stream without distorting same-day ordering.
    """

    date_part = date_str.strip()
    time_part = (time_str or "").strip()

    if time_part:
        combined = f"{date_part} {time_part}"
    else:
        combined = date_part

    # `dateutil` returns a naive datetime which is what we want - all
    # transactions in a Scalable Capital export share the same booking
    # timezone (CET/CEST) and we never compare across timezones.
    return dateutil_parser.parse(combined)
