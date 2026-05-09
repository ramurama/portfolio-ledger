"""Excel renderer using openpyxl.

The output is intentionally plain - one worksheet per report - but with
a few quality-of-life touches:

    * Bold header row with a coloured fill.
    * Frozen first row so headers stay visible while scrolling.
    * Column widths auto-sized to the longest cell content.
    * Right-aligned numeric columns.

Producing an `.xlsx` rather than a CSV lets recipients keep formatting
(thousand separators, alignment) when they hand the file off to an
accountant or tax advisor.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.utils.logging import get_logger

logger = get_logger(__name__)


# Styling constants kept at module level so they can be tweaked once
# and reflected across every Excel report.
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_NUMERIC_ALIGNMENT = Alignment(horizontal="right")


def write_excel(
    output_path: Path,
    sheet_name: str,
    headers: list[str],
    body: list[list[str]],
) -> Path:
    """Write a single-sheet Excel workbook to `output_path`."""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    if ws is None:
        # Defensive: openpyxl always returns a sheet, but the type stub
        # reports it as Optional. Guarding makes mypy / Pyright happy.
        ws = wb.create_sheet()
    ws.title = sheet_name[:31]  # Excel caps sheet names at 31 chars.

    _write_headers(ws, headers)
    _write_body(ws, headers, body)
    _apply_column_widths(ws, headers, body)

    # Freeze headers - the "A2" anchor means "scroll body, keep row 1".
    ws.freeze_panes = "A2"

    wb.save(output_path)
    logger.info("Wrote Excel report -> %s (%d rows)", output_path, len(body))
    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_headers(ws: Worksheet, headers: list[str]) -> None:
    """Render the styled header row."""

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _write_body(
    ws: Worksheet,
    headers: list[str],
    body: list[list[str]],
) -> None:
    """Stream the body rows beneath the header.

    Numeric-looking cells are right-aligned for readability. We classify
    a column as numeric by inspecting the *header* keywords - this is
    cheap and works for our deterministic schemas.
    """

    numeric_columns = {
        idx for idx, header in enumerate(headers, start=1)
        if any(token in header.lower() for token in (
            "price", "amount", "shares", "cost", "proceeds",
            "gain", "loss", "invested", "lots",
        ))
    }

    for row_idx, row in enumerate(body, start=2):
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            if col_idx in numeric_columns:
                cell.alignment = _NUMERIC_ALIGNMENT


def _apply_column_widths(
    ws: Worksheet,
    headers: list[str],
    body: list[list[str]],
) -> None:
    """Best-effort auto-sizing using the longest content per column.

    openpyxl has no real auto-fit so we approximate it. A small padding
    factor avoids cramped column widths.
    """

    for col_idx, header in enumerate(headers, start=1):
        max_len = len(header)
        for row in body:
            if col_idx - 1 < len(row):
                max_len = max(max_len, len(row[col_idx - 1]))
        # Cap the width so very long ISIN strings do not blow up the sheet.
        ws.column_dimensions[get_column_letter(col_idx)].width = min(
            max_len + 2, 40
        )
