"""PDF renderer using ReportLab.

The renderer supports two modes through a single entry-point:

    * Single-section reports (e.g. Current Holdings) - pass one
      `PdfSection` and the document is one continuous flow.
    * Multi-section reports (e.g. per-account FIFO) - pass several
      `PdfSection` objects and each starts on a fresh page, with its
      own subtitle and totals strip.

Every PDF includes:

    * A bold report title at the top of the first page.
    * A generated-at timestamp directly underneath the title.
    * The data table(s) with headers repeated on each new page.
    * Optional totals strip beneath each section.
    * Page numbers in the footer of every page.

We use ReportLab's `BaseDocTemplate` + `PageTemplate` so the page-number
footer is drawn for every page automatically. The data table itself is
built with `LongTable`, which paginates large datasets and re-prints
the header row on each new page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional, Sequence
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageBreak,
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

_SUBTITLE_STYLE = ParagraphStyle(
    name="ReportSubtitle",
    parent=_BASE_STYLES["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=16,
    spaceBefore=2,
    spaceAfter=6,
    textColor=colors.HexColor("#1F4E78"),
)

_TIMESTAMP_STYLE = ParagraphStyle(
    name="ReportTimestamp",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    textColor=colors.grey,
    spaceAfter=4,
)

_SOURCE_HEADER_STYLE = ParagraphStyle(
    name="ReportSourceHeader",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9,
    textColor=colors.HexColor("#1F4E78"),
    spaceBefore=2,
    spaceAfter=2,
)

_SOURCE_LINE_STYLE = ParagraphStyle(
    name="ReportSourceLine",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9,
    textColor=colors.HexColor("#333333"),
    leftIndent=10,
    spaceAfter=1,
)

_SOURCE_TRAILER_STYLE = ParagraphStyle(
    name="ReportSourceTrailer",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9,
    textColor=colors.HexColor("#333333"),
    spaceAfter=10,
)

_TOTALS_STYLE = ParagraphStyle(
    name="ReportTotals",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    spaceBefore=10,
)

# Footer totals (e.g. family-wide grand total) sit a step above the
# per-section totals so the reader's eye lands on them last. Slightly
# bigger and tinted in the brand color to differentiate them from the
# per-section strip drawn by `_TOTALS_STYLE`.
_FOOTER_TOTALS_HEADER_STYLE = ParagraphStyle(
    name="ReportFooterTotalsHeader",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    textColor=colors.HexColor("#1F4E78"),
    spaceBefore=14,
    spaceAfter=2,
)

_FOOTER_TOTALS_LINE_STYLE = ParagraphStyle(
    name="ReportFooterTotalsLine",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=12,
    spaceAfter=2,
)

_NOTE_STYLE = ParagraphStyle(
    name="ReportNote",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    textColor=colors.HexColor("#555555"),
    spaceBefore=2,
    spaceAfter=2,
)

# Cells that need to wrap (e.g. long instrument names) are rendered as
# Paragraphs rather than plain strings. The font size matches the body
# style of `_TABLE_STYLE` so the wrapped text reads consistently with
# its non-wrapping neighbours.
_CELL_PARAGRAPH_STYLE = ParagraphStyle(
    name="ReportCell",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=8,
    leading=10,
    spaceBefore=0,
    spaceAfter=0,
)

# Header cells are always rendered as Paragraphs so long titles like
# "% of Family Portfolio" or "Average Purchase Price" auto-wrap onto a
# second line instead of bleeding into the neighbouring column. The
# colour matches what the TableStyle would otherwise draw (white on
# the brand-blue background) and the font size is identical to what
# the `_TABLE_STYLE` row-0 rule used to apply, so we keep the same
# look-and-feel for short headers while gaining wrapping for long ones.
_HEADER_PARAGRAPH_STYLE = ParagraphStyle(
    name="ReportHeader",
    parent=_BASE_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9,
    leading=11,
    textColor=colors.white,
    spaceBefore=0,
    spaceAfter=0,
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


@dataclass(frozen=True)
class PdfSection:
    """One logical section of a PDF report.

    For multi-section documents (e.g. per-account FIFO) every section
    begins on a fresh page. For single-section documents we just render
    the lone section without a forced page break.

    Layout controls
    ---------------
    * `col_widths_mm` - explicit per-column widths in millimetres.
      Required when `wrap_columns` is non-empty so the wrapping
      Paragraphs know how much horizontal space they have. When None,
      ReportLab auto-sizes columns based on content.
    * `wrap_columns` - column indices whose cells should be rendered
      as multi-line Paragraphs. Use this for variable-length text
      (e.g. instrument names) that would otherwise overflow.
    """

    headers: list[str]
    body: list[list[str]]
    subtitle: str = ""                       # e.g. "Account: ramu"
    totals: dict[str, str] = field(default_factory=dict)
    col_widths_mm: Optional[list[float]] = None
    wrap_columns: tuple[int, ...] = ()


def write_pdf(
    output_path: Path,
    title: str,
    sections: Sequence[PdfSection],
    source_dates: Optional[Mapping[str, datetime]] = None,
    notes: Optional[Sequence[str]] = None,
    footer_totals: Optional[Mapping[str, str]] = None,
    footer_totals_title: str = "",
    footer_notes: Optional[Sequence[str]] = None,
) -> Path:
    """Render a multi-section report to `output_path`.

    Parameters
    ----------
    sections:
        At least one section is required. The first uses the document's
        main title; later sections start on a new page.
    source_dates:
        Optional `account_name -> datetime` mapping describing which
        broker export drove the report. Rendered as a "Source data"
        band on the first page just under the generated-at line.
    notes:
        Optional list of short disclaimer / methodology lines rendered
        beneath the source-data band as small italic text. Use this for
        guidance the reader needs *before* looking at the numbers
        (e.g. "Realized gain is reported pre-tax").
    footer_totals:
        Optional ``label -> value`` mapping rendered after the last
        section as a single highlighted strip. Useful for "grand
        totals" that summarise across every per-account section
        (e.g. family-wide invested capital). Distinct from
        `PdfSection.totals` which renders inside a single section.
    footer_totals_title:
        Optional heading printed above the footer totals strip
        (e.g. ``"Family Total"``). Ignored when `footer_totals` is
        empty.
    footer_notes:
        Optional list of short notes rendered as the very last content
        in the document, immediately after the footer totals (or after
        the last section if there are no footer totals). Use this when
        the note explains something the reader should see *next to* the
        totals - e.g. "the total invested capital includes reinvested
        profits". Distinct from `notes`, which sits at the top of the
        first page.
    """

    if not sections:
        raise ValueError("write_pdf requires at least one PdfSection")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = _build_doc(output_path)
    story = _build_story(
        title, sections, source_dates, notes,
        footer_totals, footer_totals_title, footer_notes,
    )
    doc.build(story)

    total_rows = sum(len(s.body) for s in sections)
    logger.info(
        "Wrote PDF report -> %s (%d section(s), %d row(s) total)",
        output_path, len(sections), total_rows,
    )
    return output_path


# ---------------------------------------------------------------------------
# Document construction helpers
# ---------------------------------------------------------------------------
def _build_doc(output_path: Path) -> BaseDocTemplate:
    """Create a landscape A4 document with a paginated footer."""

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        # Bottom margin is intentionally a touch deeper than the others
        # so the centred "Page X" footer (drawn at y=6mm by
        # `_draw_page_number`) sits below the frame with a small
        # breathing gap, instead of crashing into the last table row.
        bottomMargin=12 * mm,
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
    sections: Sequence[PdfSection],
    source_dates: Optional[Mapping[str, datetime]],
    notes: Optional[Sequence[str]],
    footer_totals: Optional[Mapping[str, str]],
    footer_totals_title: str,
    footer_notes: Optional[Sequence[str]],
) -> list:
    """Assemble the flowable story rendered into the PDF.

    The title + timestamp + (optional) source-data band + (optional)
    notes band is rendered once at the top of the document. Each
    section is then appended; sections after the first are preceded by
    a `PageBreak` so the per-account split is hard.
    """

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    story: list = [
        Paragraph(title, _TITLE_STYLE),
        Paragraph(f"Generated: {timestamp}", _TIMESTAMP_STYLE),
    ]
    story.extend(_build_source_band(source_dates))
    story.extend(_build_notes_band(notes))

    for idx, section in enumerate(sections):
        if idx > 0:
            # Hard page break between sections so each account's FIFO
            # report stands on its own page even if the previous one
            # was short.
            story.append(PageBreak())

        story.extend(_build_section_flowables(section))

    story.extend(_build_footer_totals(footer_totals, footer_totals_title))
    story.extend(_build_footer_notes(footer_notes))

    return story


def _build_footer_notes(footer_notes: Optional[Sequence[str]]) -> list:
    """Render notes that sit directly under the totals strip.

    A small spacer separates the notes from the bold totals lines so
    the reader's eye still locks onto the totals first; the note then
    reads as a direct annotation of those numbers.
    """

    if not footer_notes:
        return []

    flows: list = [Spacer(1, 2 * mm)]
    flows.extend(Paragraph(f"Note: {line}", _NOTE_STYLE) for line in footer_notes)
    return flows


def _build_footer_totals(
    footer_totals: Optional[Mapping[str, str]],
    footer_totals_title: str,
) -> list:
    """Render the optional grand-total strip on the last page.

    The strip is intentionally NOT preceded by a `PageBreak` - we want
    it to sit right after the last per-section totals when there is
    room, and let ReportLab spill it onto a fresh page only when the
    last section ran long. Either way, it is the final content in the
    document and therefore renders "at the end of the last page".
    """

    if not footer_totals:
        return []

    flows: list = []
    if footer_totals_title:
        flows.append(
            Paragraph(footer_totals_title, _FOOTER_TOTALS_HEADER_STYLE)
        )
    for label, value in footer_totals.items():
        flows.append(
            Paragraph(f"{label}: {value}", _FOOTER_TOTALS_LINE_STYLE)
        )
    return flows


def _build_source_band(
    source_dates: Optional[Mapping[str, datetime]],
) -> list:
    """Render the "Source data" band shown just below the timestamp.

    Layout depends on how many accounts contributed:

        * 0 entries -> band is omitted entirely.
        * 1 entry   -> single line: "Source data: <account> exported on <date>".
        * 2+ entries -> a "Source data:" header followed by one indented
                        line per account.

    Times are rendered with seconds when the filename carried a time
    component, otherwise just the date (the parsing layer puts the
    time at midnight when only a date is available, and we treat that
    as "no time recorded" here for cleaner output).
    """

    if not source_dates:
        return []

    items = sorted(source_dates.items())

    if len(items) == 1:
        account, when = items[0]
        return [
            Paragraph(
                f"Source data: <b>{account}</b> exported on "
                f"{_format_source_date(when)}",
                _SOURCE_TRAILER_STYLE,
            )
        ]

    flows: list = [Paragraph("Source data:", _SOURCE_HEADER_STYLE)]
    for account, when in items:
        flows.append(
            Paragraph(
                f"&bull; <b>{account}</b>: exported on {_format_source_date(when)}",
                _SOURCE_LINE_STYLE,
            )
        )
    # A small spacer row replicates the bottom margin the single-line
    # variant gets via spaceAfter on its trailer style.
    flows.append(Spacer(1, 4 * mm))
    return flows


def _build_notes_band(notes: Optional[Sequence[str]]) -> list:
    """Render the optional disclaimer / methodology lines.

    Each note becomes its own italic paragraph so multi-line guidance
    reads naturally. A trailing spacer keeps the section table from
    crashing into the last note.
    """

    if not notes:
        return []

    flows: list = [Paragraph(f"Note: {line}", _NOTE_STYLE) for line in notes]
    flows.append(Spacer(1, 4 * mm))
    return flows


def _format_source_date(when: datetime) -> str:
    """Render a source-date `datetime` for display.

    Filenames may carry only a date (`extract_source_date` then sets
    the time to midnight). We omit the time component in that case so
    the band reads naturally either way.
    """

    if when.hour == 0 and when.minute == 0 and when.second == 0:
        return when.strftime("%Y-%m-%d")
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _build_section_flowables(section: PdfSection) -> list:
    """Render a single `PdfSection` into a list of flowables."""

    out: list = []

    if section.subtitle:
        out.append(Paragraph(section.subtitle, _SUBTITLE_STYLE))

    if section.body:
        # Header is row 0 of the table data; LongTable repeats it on
        # every new page thanks to `repeatRows=1`. Cells flagged via
        # `wrap_columns` become Paragraphs so long content wraps inside
        # its assigned cell width instead of pushing the whole table
        # off the page. Headers are *always* wrapped so long titles
        # (e.g. "Average Purchase Price", "% of Family Portfolio")
        # fold onto a second line instead of bleeding into the next
        # column - regardless of how the operator chose `col_widths_mm`.
        wrapped_headers = _wrap_headers(section.headers)
        body_with_wrapping = _apply_cell_wrapping(
            section.body, section.wrap_columns,
        )
        table_data = [wrapped_headers] + body_with_wrapping
        col_widths = (
            [w * mm for w in section.col_widths_mm]
            if section.col_widths_mm else None
        )

        table = LongTable(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(_TABLE_STYLE)
        out.append(table)
    else:
        # Empty datasets must not produce a blank section - tell the
        # reader explicitly that there was nothing to render.
        out.append(Paragraph(
            "<i>No data available for this section.</i>",
            _BASE_STYLES["Normal"],
        ))

    if section.totals:
        out.append(Spacer(1, 4 * mm))
        for label, value in section.totals.items():
            out.append(Paragraph(f"{label}: {value}", _TOTALS_STYLE))

    return out


def _wrap_headers(headers: Sequence[str]) -> list:
    """Wrap each header cell into a Paragraph so it auto-wraps.

    Why every header (not just the long ones)? Because the bold white
    header style is identical for all of them - rendering a mix of
    plain strings and Paragraphs would draw two visually different
    cells on the same row (different baselines, different padding).
    Wrapping uniformly keeps the row looking consistent regardless of
    which individual headers happen to be long.
    """

    return [
        Paragraph(xml_escape(h), _HEADER_PARAGRAPH_STYLE) for h in headers
    ]


def _apply_cell_wrapping(
    body: list[list[str]],
    wrap_columns: Sequence[int],
) -> list[list]:
    """Convert designated string cells into wrapping Paragraphs.

    Cells outside `wrap_columns` are returned unchanged so the table
    keeps its tight single-line layout for fixed-width fields like
    dates and money values. The XML escape is essential because
    instrument names occasionally contain `&` or `<` characters
    (e.g. "iShares S&P 500 ...") that ReportLab would otherwise treat
    as malformed markup.
    """

    if not wrap_columns:
        return body

    wrap_set = set(wrap_columns)
    return [
        [
            Paragraph(xml_escape(cell), _CELL_PARAGRAPH_STYLE)
            if idx in wrap_set else cell
            for idx, cell in enumerate(row)
        ]
        for row in body
    ]


def _draw_page_number(canvas, doc) -> None:
    """Footer callback: render `Page X` centred at the bottom."""

    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    page_text = f"Page {doc.page}"
    canvas.drawCentredString(
        doc.pagesize[0] / 2,
        6 * mm,
        page_text,
    )
    canvas.restoreState()
