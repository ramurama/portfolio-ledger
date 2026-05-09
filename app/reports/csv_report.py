"""CSV renderer.

Polars is overkill for plain string tables but the project specifies it
as a required dependency, so we use Polars' DataFrame to write the CSV.
This also lets us swap in richer transformations later (e.g. partitioned
exports) without touching the call sites.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from app.utils.logging import get_logger

logger = get_logger(__name__)


def write_csv(
    output_path: Path,
    headers: list[str],
    body: list[list[str]],
) -> Path:
    """Write a header + body table to `output_path`.

    The output is plain comma-separated values with a UTF-8 BOM so it
    opens cleanly in Excel on Windows / macOS.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Polars expects column-oriented data. We pre-build a dict of
    # `column -> values` so the resulting DataFrame matches `headers`
    # in both order and content.
    columns: dict[str, list[str]] = {h: [] for h in headers}
    for row in body:
        for header, value in zip(headers, row):
            columns[header].append(value)

    df = pl.DataFrame(columns)
    df.write_csv(output_path, include_bom=True)

    logger.info("Wrote CSV report -> %s (%d rows)", output_path, len(body))
    return output_path
