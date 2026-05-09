"""High-level orchestrator that ties the renderers together.

`ReportManager.write` is the single function the CLI calls when the
operator asks for reports. It:

    1. Builds the formatted header + body for each logical report
       (FIFO, current holdings, combined portfolio).
    2. Hands those to every requested renderer (CSV / Excel / PDF).
    3. Returns the list of files actually written, so the CLI can
       echo them to the operator.

The class is intentionally stateless apart from configuration so that
tests can spin up an instance pointing at a temp directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from app.config import (
    MONEY_QUANTIZE,
    OUTPUT_CSV_DIR,
    OUTPUT_EXCEL_DIR,
    OUTPUT_PDF_DIR,
    REPORT_TIMESTAMP_FORMAT,
)
from app.models import RealizedTrade
from app.reports import _schema as schema
from app.reports.csv_report import write_csv
from app.reports.excel_report import write_excel
from app.reports.pdf_report import write_pdf
from app.services.holdings import HoldingRow
from app.services.portfolio import CombinedHoldingRow
from app.utils.decimal_utils import ZERO, format_us_decimal
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ReportFormat(str, Enum):
    """Output formats the manager knows how to produce."""

    CSV = "csv"
    EXCEL = "excel"
    PDF = "pdf"

    @classmethod
    def all(cls) -> list["ReportFormat"]:
        """Convenience helper used by the `--format all` CLI option."""
        return list(cls)


@dataclass
class ReportPayload:
    """The full set of data the manager renders into reports."""

    realized_trades: list[RealizedTrade] = field(default_factory=list)
    holdings: list[HoldingRow] = field(default_factory=list)
    combined_portfolio: list[CombinedHoldingRow] = field(default_factory=list)
    account_names: list[str] = field(default_factory=list)


class ReportManager:
    """Render a `ReportPayload` into one or more on-disk reports."""

    def __init__(
        self,
        csv_dir: Path = OUTPUT_CSV_DIR,
        excel_dir: Path = OUTPUT_EXCEL_DIR,
        pdf_dir: Path = OUTPUT_PDF_DIR,
    ) -> None:
        self.csv_dir = csv_dir
        self.excel_dir = excel_dir
        self.pdf_dir = pdf_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def write(
        self,
        payload: ReportPayload,
        formats: Iterable[ReportFormat],
        generated_at: datetime | None = None,
    ) -> list[Path]:
        """Generate every report in every requested format.

        `generated_at` is injectable so tests can produce deterministic
        filenames; production callers omit it and we use "now".
        """

        formats = list(formats)
        if not formats:
            logger.warning("ReportManager.write called with no formats")
            return []

        stamp = (generated_at or datetime.now()).strftime(
            REPORT_TIMESTAMP_FORMAT
        )

        written: list[Path] = []
        written.extend(self._write_fifo(payload, formats, stamp))
        written.extend(self._write_holdings(payload, formats, stamp))
        written.extend(self._write_combined(payload, formats, stamp))

        logger.info("Generated %d report file(s).", len(written))
        return written

    # ------------------------------------------------------------------
    # FIFO
    # ------------------------------------------------------------------
    def _write_fifo(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        headers = schema.FIFO_HEADERS
        body = schema.fifo_rows(payload.realized_trades)

        # Sum the realized P&L for the totals strip in the PDF.
        total_gain = sum(
            (t.realized_gain_loss for t in payload.realized_trades),
            start=ZERO,
        )
        totals = {
            "Total Trades": str(len(payload.realized_trades)),
            "Total Realized Gain/Loss": format_us_decimal(
                total_gain, MONEY_QUANTIZE, thousands=True,
            ),
        }

        return self._dispatch(
            base_filename=f"fifo_report_{stamp}",
            sheet_name="FIFO Report",
            title="FIFO Realized Gains Report",
            headers=headers,
            body=body,
            totals=totals,
            formats=formats,
        )

    # ------------------------------------------------------------------
    # Current holdings
    # ------------------------------------------------------------------
    def _write_holdings(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        headers = schema.HOLDINGS_HEADERS
        body = schema.holdings_rows(payload.holdings)

        total_invested = sum(
            (h.invested_amount for h in payload.holdings),
            start=ZERO,
        )
        totals = {
            "Total Positions": str(len(payload.holdings)),
            "Total Invested": format_us_decimal(
                total_invested, MONEY_QUANTIZE, thousands=True,
            ),
        }

        return self._dispatch(
            base_filename=f"current_holdings_{stamp}",
            sheet_name="Current Holdings",
            title="Current Holdings Report",
            headers=headers,
            body=body,
            totals=totals,
            formats=formats,
        )

    # ------------------------------------------------------------------
    # Combined family portfolio
    # ------------------------------------------------------------------
    def _write_combined(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        headers = schema.combined_headers(payload.account_names)
        body = schema.combined_rows(
            payload.combined_portfolio, payload.account_names
        )

        total_invested = sum(
            (r.total_invested for r in payload.combined_portfolio),
            start=ZERO,
        )
        totals = {
            "Total ISINs": str(len(payload.combined_portfolio)),
            "Total Invested (Family)": format_us_decimal(
                total_invested, MONEY_QUANTIZE, thousands=True,
            ),
        }

        return self._dispatch(
            base_filename=f"combined_portfolio_{stamp}",
            sheet_name="Combined Portfolio",
            title="Combined Family Portfolio Report",
            headers=headers,
            body=body,
            totals=totals,
            formats=formats,
        )

    # ------------------------------------------------------------------
    # Format dispatch
    # ------------------------------------------------------------------
    def _dispatch(
        self,
        *,
        base_filename: str,
        sheet_name: str,
        title: str,
        headers: list[str],
        body: list[list[str]],
        totals: dict[str, str],
        formats: list[ReportFormat],
    ) -> list[Path]:
        """Run a single logical report through every requested format."""

        outputs: list[Path] = []

        if ReportFormat.CSV in formats:
            outputs.append(write_csv(
                self.csv_dir / f"{base_filename}.csv", headers, body,
            ))

        if ReportFormat.EXCEL in formats:
            outputs.append(write_excel(
                self.excel_dir / f"{base_filename}.xlsx",
                sheet_name, headers, body,
            ))

        if ReportFormat.PDF in formats:
            outputs.append(write_pdf(
                self.pdf_dir / f"{base_filename}.pdf",
                title, headers, body, totals,
            ))

        return outputs
