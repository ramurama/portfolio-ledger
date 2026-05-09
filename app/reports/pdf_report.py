"""PDF renderer using ReportLab.

The PDFs include:
    * a bold title at the top of the first page
    * a generated-at timestamp directly underneath
    * the data table with repeating headers across pages
    * a totals strip at the bottom (when applicable)
    * page numbers in the footer of every page

We use ReportLab's `BaseDocTemplate` + `PageTemplate` so the page-number
footer is drawn for every page automatically. The table itself is built
with `LongTable`, which paginates large datasets and re-prints the
header on each new page.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageTemplate,
    Paragraph,
    Spacer,
    TableStyle,
)

from app.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
_BASE_STYLES = getSampleStyleSheet()

_TITLE_STYLE = ParagraphStyle(
    name="ReportTitle",
    parent=_BASE_STYLES["Title"],
    fontName="Helvetica-Bold",
    fontSize=18,
    leading=22,
    spaceAfter=4,
    textColor=colors.HexColor("#1F4E78"),
)

_TIMESTAMP_STYLE = ParagraphStyle(
    name="ReportTimestamp",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    textColor=colors.grey,
    spaceAfter=10,
)

_TOTALS_STYLE = ParagraphStyle(
    name="ReportTotals",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    spaceBefore=10,
)

# Look-and-feel for the data table.
_TABLE_STYLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, 0), 9),
    ("FONTSIZE", (0, 1), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ("TOPPADDING", (0, 0), (-1, 0), 6),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFBFBF")),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
])


def write_pdf(
    output_path: Path,
    title: str,
    headers: list[str],
    body: list[list[str]],
    totals: Optional[dict[str, str]] = None,
) -> Path:
    """Render a single-table report to `output_path`.

    Parameters
    ----------
    output_path:
        Destination file. Parent directory is created if missing.
    title:
        Top-of-page title (e.g. "FIFO Realized Gains").
    headers / body:
        Pre-formatted table data from `app.reports._schema`.
    totals:
        Optional `label -> value` mapping rendered as a single line
        beneath the table (e.g. ``{"Total Realized G/L": "1,234.56"}``).
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = _build_doc(output_path)
    story = _build_story(title, headers, body, totals)
    doc.build(story)

    logger.info("Wrote PDF report -> %s (%d rows)", output_path, len(body))
    return output_path


# ---------------------------------------------------------------------------
# Document construction helpers
# ---------------------------------------------------------------------------
def _build_doc(output_path: Path) -> BaseDocTemplate:
    """Create a landscape A4 document with a paginated footer."""

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title="Portfolio Ledger Report",
        author="Portfolio Ledger",
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="main",
    )
    doc.addPageTemplates(
        [PageTemplate(id="report", frames=frame, onPage=_draw_page_number)]
    )
    return doc


def _build_story(
    title: str,
    headers: list[str],
    body: list[list[str]],
    totals: Optional[dict[str, str]],
) -> list:
    """Assemble the flowable story rendered into the PDF."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    story: list = [
        Paragraph(title, _TITLE_STYLE),
        Paragraph(f"Generated: {timestamp}", _TIMESTAMP_STYLE),
    ]

    if body:
        # Header is row 0 of the table data; LongTable will repeat it
        # on every new page thanks to `repeatRows=1`.
        table_data = [headers] + body
        table = LongTable(table_data, repeatRows=1)
        table.setStyle(_TABLE_STYLE)
        story.append(table)
    else:
        # Empty datasets must not produce a blank PDF - tell the reader
        # explicitly that there was nothing to render.
        story.append(Paragraph(
            "<i>No data available for this report.</i>",
            _BASE_STYLES["Normal"],
        ))

    if totals:
        story.append(Spacer(1, 4 * mm))
        for label, value in totals.items():
            story.append(Paragraph(f"{label}: {value}", _TOTALS_STYLE))

    return story


def _draw_page_number(canvas, doc) -> None:
    """Footer callback: render `Page X` centred at the bottom."""

    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    page_text = f"Page {doc.page}"
    canvas.drawCentredString(
        doc.pagesize[0] / 2,
        10 * mm,
        page_text,
    )
    canvas.restoreState()
