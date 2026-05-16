"""High-level orchestrator that ties the renderers together.

`ReportManager.write` is the single function the CLI calls when the
operator asks for reports. It:

    1. Builds the formatted header + body for each logical report
       (Tax Lots, current holdings, combined portfolio).
    2. Hands those to every requested renderer (CSV / Excel / PDF).
    3. Returns the list of files actually written, so the CLI can
       echo them to the operator.

Tax Lots splitting
------------------
The Tax Lots realized-gains report is split per account:

    * Excel - one sheet per account.
    * PDF   - one page per account.
    * CSV   - rows are grouped per account (single file, no notion of
              pages) and sorted by (buy_date, sell_date) within each.

The other two reports (current holdings, combined portfolio) are
rendered as a single section because they are inherently per-account
or cross-account already.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.config import (
    DEFAULT_CURRENCY,
    OUTPUT_CSV_DIR,
    OUTPUT_EXCEL_DIR,
    OUTPUT_PDF_DIR,
    REPORT_TIMESTAMP_FORMAT,
)
from app.models import RealizedTrade
from app.reports import _schema as schema
from app.reports.csv_report import write_csv
from app.reports.excel_report import ExcelSection, write_excel
from app.reports.pdf_report import PdfSection, write_pdf
from app.services.cost_basis import CostBasisRow
from app.services.holdings import HoldingRow
from app.services.portfolio import (
    CombinedHoldingRow,
    combined_family_price_totals,
)
from app.utils.decimal_utils import ZERO, format_money
from app.utils.logging import get_logger
from app.utils.text import display_account_name

logger = get_logger(__name__)


# Disclaimer lines shown at the top of the Tax Lots PDF report. Kept
# at module scope so they live next to the column header naming so a
# future change here is impossible to forget.
_TAX_LOTS_PDF_NOTES: tuple[str, ...] = (
    "Realized Gain/Loss is reported PRE-TAX - i.e. gross of any "
    "withholding tax (Abgeltungsteuer / Solidaritätszuschlag) deducted "
    "by the broker at the point of sale.",
    "Withheld tax is captured separately as TAX transactions during "
    "ingestion and is not netted against realized gains in this report.",
)


# Disclaimer lines shown at the top of the Current Holdings and Combined
# Portfolio PDFs. Both reports surface the same "Total Invested" figure,
# so the wording is shared verbatim and the constant lives at module
# scope to make any future tweak a single edit.
_INVESTED_CAPITAL_PDF_NOTES: tuple[str, ...] = (
    "The total invested capital shown includes reinvested profits.",
)


# Disclaimer lines shown at the top of the cost-basis transfer PDF.
# This report exists to support broker-to-broker transfers (e.g.
# Scalable Capital -> IBKR), so the notes spell out exactly what the
# operator should enter on the receiving broker's intake form and how
# the per-share figure was derived.
_COST_BASIS_PDF_NOTES: tuple[str, ...] = (
    "One row per still-open tax lot. Lots are NOT aggregated by ISIN: "
    "for IBKR-style cost-basis transfers the receiving broker needs the "
    "purchase price of EACH lot separately so future sells can be tax-"
    "matched correctly.",
    "Cost per Share is the gross per-share acquisition price at the time "
    "of the original Buy / Savings plan (Scalable Capital reports trade "
    "amounts gross of withheld tax; broker fees on the Buy side are "
    "negligible for our exports).",
    "On the IBKR cost-basis intake form, enter Quantity and Cost per "
    "Share for each row; Cost Basis (= Quantity x Cost per Share) is "
    "shown only as a sanity check.",
)


# ---------------------------------------------------------------------------
# Per-PDF column-width tables
# ---------------------------------------------------------------------------
# Landscape A4 (297mm wide) with 8mm side margins (set in
# `app.reports.pdf_report._build_doc`) gives ~281mm of usable width. Each
# tuple below sums to <= 281mm so the table always fits without horizontal
# overflow. The Symbol column is the only one that wraps, giving us room
# for long instrument names like "iShares S&P 500 Information Technology
# Sector (Acc)" without sacrificing the rest of the layout.

# Tax Lots: Account, ISIN, Symbol(wrap), BuyDate, SellDate, SharesSold,
#           AcquisitionCost, SaleProceeds, RealizedGain
_TAX_LOTS_COL_WIDTHS_MM: list[float] = [18, 25, 60, 22, 22, 22, 26, 26, 38]
_TAX_LOTS_SYMBOL_COL_INDEX: int = 2

# Holdings: Account, ISIN, Symbol(wrap), TotalShares, AvgPrice,
#           InvestedAmount, Allocation
_HOLDINGS_COL_WIDTHS_MM: list[float] = [20, 25, 68, 26, 34, 32, 26]
_HOLDINGS_SYMBOL_COL_INDEX: int = 2

# Cost basis transfer: Account, ISIN, Symbol(wrap), AcquisitionDate,
#                      Quantity, CostPerShare, CostBasis
# Total = 252mm, comfortably within the 281mm landscape A4 budget.
_COST_BASIS_COL_WIDTHS_MM: list[float] = [22, 28, 70, 28, 32, 38, 34]
_COST_BASIS_SYMBOL_COL_INDEX: int = 2


# Width budget for landscape A4 minus 16mm of margins (8mm each side).
_PAGE_BUDGET_MM: float = 281.0


def _distribute_col_widths_mm(
  specs: list[tuple[float, float]],
) -> list[float]:
    """Expand column mins across ``_PAGE_BUDGET_MM`` using relative weights.

    Each spec is ``(minimum_mm, weight)``. When mins exceed the page
    budget, every column is scaled down proportionally.
    """

    if not specs:
        return []

    mins = [s[0] for s in specs]
    weights = [s[1] for s in specs]
    total_min = sum(mins)
    budget = _PAGE_BUDGET_MM

    if total_min >= budget:
        scale = budget / total_min
        return [m * scale for m in mins]

    extra = budget - total_min
    weight_sum = sum(weights)
    return [
        min_w + extra * (w / weight_sum)
        for min_w, w in zip(mins, weights)
    ]


def _combined_col_widths_mm(
    num_accounts: int,
    *,
    include_market_prices: bool = False,
) -> list[float]:
    """Build column widths that use the full printable landscape width."""

    specs: list[tuple[float, float]] = [
        (26.0, 1.0),   # ISIN — single line for 12-char codes
        (30.0, 1.4),   # Symbol (wrap)
    ]
    specs.extend((16.0, 1.0) for _ in range(max(num_accounts, 0)))
    specs.append((16.0, 0.9))   # Combined shares
    specs.append((18.0, 1.1))   # Avg
    if include_market_prices:
        specs.extend(
            [
                (18.0, 1.1),   # Last
                (20.0, 1.2),   # Mkt.
                (18.0, 1.0),   # G/L
            ]
        )
    specs.extend(
        [
            (20.0, 1.2),   # Invest.
            (12.0, 0.7),   # Alloc%
        ]
    )
    return _distribute_col_widths_mm(specs)


_COMBINED_SYMBOL_COL_INDEX: int = 1


class ReportFormat(str, Enum):
    """Output formats the manager knows how to produce."""

    CSV = "csv"
    EXCEL = "excel"
    PDF = "pdf"
    # CLI sentinel expanded to CSV + Excel + PDF before writing files.
    ALL = "all"

    @classmethod
    def all(cls) -> list["ReportFormat"]:
        """Every concrete file format (excludes the ``all`` sentinel)."""

        return [cls.CSV, cls.EXCEL, cls.PDF]


class ReportKind(str, Enum):
    """Logical reports emitted by :meth:`ReportManager.write`."""

    TAX_LOTS = "tax-lots"
    HOLDINGS = "holdings"
    COMBINED = "combined"


@dataclass
class ReportPayload:
    """The full set of data the manager renders into reports.

    Not every command populates every field: `generate-reports` fills
    `realized_trades`, `holdings`, `combined_portfolio`,
    while
    `generate-cost-basis` only fills `cost_basis`. Each `_write_*`
    helper consumes only the field(s) it needs, so unused fields
    simply default to empty.
    """

    realized_trades: list[RealizedTrade] = field(default_factory=list)
    holdings: list[HoldingRow] = field(default_factory=list)
    combined_portfolio: list[CombinedHoldingRow] = field(default_factory=list)
    # When True, combined report tables include live quote columns.
    include_market_prices: bool = False
    # Per-lot rows for the IBKR-style cost-basis transfer report. Built
    # from `TaxLotResult.open_lots` via `build_cost_basis_rows`.
    cost_basis: list[CostBasisRow] = field(default_factory=list)
    account_names: list[str] = field(default_factory=list)
    # account_name -> datetime extracted from the input file used. PDF
    # reports render this as a "Source data" band so the operator can
    # tell which broker export drove the numbers on the page.
    source_dates: dict[str, datetime] = field(default_factory=dict)
    # ISO-4217 code applied to every money value in the report. The
    # CLI determines this from the ingested transactions (Scalable
    # Capital DE always reports EUR) and falls back to the project
    # default if no transactions exist.
    currency: str = DEFAULT_CURRENCY


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
        formats: Iterable[ReportFormat] | None = None,
        *,
        report_formats: dict[ReportKind, list[ReportFormat]] | None = None,
        generated_at: datetime | None = None,
    ) -> list[Path]:
        """Generate reports in the requested format(s).

        Callers supply either the legacy ``formats`` iterable (same formats
        for every logical report) or ``report_formats`` mapping each
        :class:`ReportKind` to its own format list.

        `generated_at` is injectable so tests can produce deterministic
        filenames; production callers omit it and we use "now".
        """

        plan: dict[ReportKind, list[ReportFormat]]
        if report_formats is not None:
            plan = {
                k: list(v)
                for k, v in report_formats.items()
                if v
            }
        else:
            if formats is None:
                logger.warning(
                    "ReportManager.write called with no formats and no "
                    "report_formats"
                )
                return []
            fmt_list = list(formats)
            if not fmt_list:
                logger.warning("ReportManager.write called with no formats")
                return []
            plan = {
                ReportKind.TAX_LOTS: fmt_list,
                ReportKind.HOLDINGS: fmt_list,
                ReportKind.COMBINED: fmt_list,
            }

        if not plan:
            logger.warning("ReportManager.write called with empty report plan")
            return []

        stamp = (generated_at or datetime.now()).strftime(
            REPORT_TIMESTAMP_FORMAT
        )

        written: list[Path] = []
        if ReportKind.TAX_LOTS in plan:
            written.extend(
                self._write_tax_lots(payload, plan[ReportKind.TAX_LOTS], stamp)
            )
        if ReportKind.HOLDINGS in plan:
            written.extend(
                self._write_holdings(payload, plan[ReportKind.HOLDINGS], stamp)
            )
        if ReportKind.COMBINED in plan:
            written.extend(
                self._write_combined(payload, plan[ReportKind.COMBINED], stamp)
            )

        logger.info("Generated %d report file(s).", len(written))
        return written

    def write_cost_basis(
        self,
        payload: ReportPayload,
        formats: Iterable[ReportFormat],
        generated_at: datetime | None = None,
    ) -> list[Path]:
        """Generate ONLY the cost-basis transfer report.

        Used by the dedicated `generate-cost-basis` CLI command. The
        cost-basis report is a specialised, infrequent artefact (only
        produced when actually transferring assets between brokers),
        so we keep it off the default `write()` pipeline and require
        the caller to opt-in via this dedicated method.
        """

        formats = list(formats)
        if not formats:
            logger.warning(
                "ReportManager.write_cost_basis called with no formats"
            )
            return []

        stamp = (generated_at or datetime.now()).strftime(
            REPORT_TIMESTAMP_FORMAT
        )

        written = self._write_cost_basis(payload, formats, stamp)
        logger.info("Generated %d cost-basis file(s).", len(written))
        return written

    # ------------------------------------------------------------------
    # Tax Lots (split per account)
    # ------------------------------------------------------------------
    def _write_tax_lots(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        # Bucket realized trades per account, sort each bucket by
        # (buy_date, sell_date), and remember the account ordering so
        # the same sequence is used for every output format.
        per_account = self._group_trades_by_account(
            payload.realized_trades, payload.account_names,
        )

        base_filename = f"tax_lots_report_{stamp}"
        title = "Tax Lots Realized Gains Report"
        outputs: list[Path] = []

        if ReportFormat.CSV in formats:
            outputs.append(self._write_tax_lots_csv(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.EXCEL in formats:
            outputs.append(self._write_tax_lots_excel(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.PDF in formats:
            outputs.append(self._write_tax_lots_pdf(
                per_account,
                base_filename,
                title,
                payload.source_dates,
                payload.currency,
            ))

        return outputs

    @staticmethod
    def _group_trades_by_account(
        trades: list[RealizedTrade],
        ordered_account_names: list[str],
    ) -> list[tuple[str, list[RealizedTrade]]]:
        """Bucket trades per account and sort each bucket chronologically.

        The returned list preserves the order of `ordered_account_names`
        so every output format renders accounts in the same sequence
        the operator saw during ingestion. Accounts that exist in the
        input directory but have zero realized trades are still
        represented (with an empty list) so the per-account split is
        visible in PDF/Excel even when there is nothing to show.
        """

        buckets: dict[str, list[RealizedTrade]] = defaultdict(list)
        for trade in trades:
            buckets[trade.account_name].append(trade)

        # Sort within each bucket by (buy_date, sell_date, isin).
        for account, account_trades in buckets.items():
            buckets[account] = schema.sort_tax_lots_trades(account_trades)

        ordered: list[tuple[str, list[RealizedTrade]]] = []
        seen: set[str] = set()
        for name in ordered_account_names:
            ordered.append((name, buckets.get(name, [])))
            seen.add(name)

        # Catch any account names that show up in trades but not in
        # `account_names` (defensive - should not happen in practice).
        for name in sorted(buckets.keys() - seen):
            ordered.append((name, buckets[name]))

        return ordered

    # ----- Per-format Tax Lots writers --------------------------------
    def _write_tax_lots_csv(
        self,
        per_account: list[tuple[str, list[RealizedTrade]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        """Write a single CSV grouped (and sorted) per account.

        CSV has no notion of pages or sheets, so we keep one file but
        emit the rows account-by-account in the order produced by
        `_group_trades_by_account`. Each row already carries the
        `Account` column, so consumers can group / pivot if needed.
        """

        body: list[list[str]] = []
        for _account, trades in per_account:
            body.extend(
                schema.tax_lots_rows(trades, currency, money_symbols=False),
            )

        return write_csv(
            self.csv_dir / f"{base_filename}.csv",
            schema.TAX_LOTS_HEADERS,
            body,
        )

    def _write_tax_lots_excel(
        self,
        per_account: list[tuple[str, list[RealizedTrade]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        """One sheet per account."""

        sections = [
            ExcelSection(
                sheet_name=display_account_name(account) or "Unknown",
                headers=schema.TAX_LOTS_HEADERS,
                body=schema.tax_lots_rows(trades, currency, money_symbols=False),
            )
            for account, trades in per_account
        ]
        # `write_excel` requires at least one section - ingestion
        # guarantees at least one account folder, but be explicit.
        if not sections:
            sections = [ExcelSection("Tax Lots", schema.TAX_LOTS_HEADERS, [])]

        return write_excel(self.excel_dir / f"{base_filename}.xlsx", sections)

    def _write_tax_lots_pdf(
        self,
        per_account: list[tuple[str, list[RealizedTrade]]],
        base_filename: str,
        title: str,
        source_dates: dict[str, datetime],
        currency: str,
    ) -> Path:
        """One page per account, with account and report totals.

        The PDF uses `schema.tax_lots_pdf_headers()` instead of the
        canonical `TAX_LOTS_HEADERS` so the bold "(Pre-Tax)" qualifier
        doesn't crowd the column header. The disclaimer is conveyed via
        the notes band on the first page (see `_TAX_LOTS_PDF_NOTES`).
        """

        pdf_headers = schema.tax_lots_pdf_headers()

        sections: list[PdfSection] = []
        for account, trades in per_account:
            total_gain = sum(
                (t.realized_gain_loss for t in trades),
                start=ZERO,
            )
            sections.append(
                PdfSection(
                    subtitle=f"Account: {display_account_name(account)}",
                    headers=pdf_headers,
                    body=schema.tax_lots_rows(trades, currency),
                    totals={
                        "Account Realized Trades": str(len(trades)),
                        # Header in the notes band already says the
                        # gain figure is pre-tax, so drop the qualifier
                        # from the totals label too for a clean look.
                        "Account Realized Gain/Loss":
                            format_money(total_gain, currency),
                    },
                    col_widths_mm=_TAX_LOTS_COL_WIDTHS_MM,
                    wrap_columns=(_TAX_LOTS_SYMBOL_COL_INDEX,),
                )
            )

        if not sections:
            sections = [
                PdfSection(
                    headers=pdf_headers,
                    body=[],
                    col_widths_mm=_TAX_LOTS_COL_WIDTHS_MM,
                    wrap_columns=(_TAX_LOTS_SYMBOL_COL_INDEX,),
                )
            ]

        return write_pdf(
            self.pdf_dir / f"{base_filename}.pdf",
            title=title,
            sections=sections,
            source_dates=_format_source_dates(source_dates),
            notes=_TAX_LOTS_PDF_NOTES,
            footer_totals={
                "Total Realized Trades": str(
                    sum(len(trades) for _account, trades in per_account)
                ),
                "Total Realized Gain/Loss": format_money(
                    sum(
                        (
                            trade.realized_gain_loss
                            for _account, trades in per_account
                            for trade in trades
                        ),
                        start=ZERO,
                    ),
                    currency,
                ),
            },
            footer_totals_title="Tax Lots Total",
        )

    # ------------------------------------------------------------------
    # Current holdings (split per account, with a family-wide grand
    # total drawn at the end of the last page)
    # ------------------------------------------------------------------
    def _write_holdings(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        # Bucket holdings per account using the same ordering rules as
        # the Tax Lots report so every output format renders accounts in
        # identical order across the three reports.
        per_account = self._group_holdings_by_account(
            payload.holdings, payload.account_names,
        )

        # Family-wide grand total - the value the operator wants to
        # see "at the end of the last page" alongside the per-account
        # subtotals on each individual page.
        family_total = sum(
            (h.invested_amount for h in payload.holdings),
            start=ZERO,
        )
        family_footer = {
            "Total Positions (Family)": str(len(payload.holdings)),
            "Total Invested (Family)": format_money(
                family_total, payload.currency,
            ),
        }

        base_filename = f"current_holdings_{stamp}"
        title = "Current Holdings Report"
        outputs: list[Path] = []

        if ReportFormat.CSV in formats:
            outputs.append(self._write_holdings_csv(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.EXCEL in formats:
            outputs.append(self._write_holdings_excel(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.PDF in formats:
            outputs.append(self._write_holdings_pdf(
                per_account,
                base_filename,
                title,
                payload.source_dates,
                payload.currency,
                family_footer,
            ))

        return outputs

    @staticmethod
    def _group_holdings_by_account(
        holdings: list[HoldingRow],
        ordered_account_names: list[str],
    ) -> list[tuple[str, list[HoldingRow]]]:
        """Bucket holdings per account in `ordered_account_names` order.

        Mirrors `_group_trades_by_account` so the per-account split
        renders identically across Tax Lots and Holdings:

            * Accounts that exist in the input but have zero holdings
              are still represented (with an empty list) so the report
              still shows a page / sheet for them.
            * Holdings whose account name is NOT in
              `ordered_account_names` (defensive - should never
              happen) are appended at the end in alphabetical order.
            * Within an account, rows keep the order produced by
              `build_current_holdings`, which already sorts by
              (symbol, isin) for a natural read.
        """

        buckets: dict[str, list[HoldingRow]] = defaultdict(list)
        for row in holdings:
            buckets[row.account_name].append(row)

        ordered: list[tuple[str, list[HoldingRow]]] = []
        seen: set[str] = set()
        for name in ordered_account_names:
            ordered.append((name, buckets.get(name, [])))
            seen.add(name)

        for name in sorted(buckets.keys() - seen):
            ordered.append((name, buckets[name]))

        return ordered

    # ----- Per-format Holdings writers -------------------------------
    def _write_holdings_csv(
        self,
        per_account: list[tuple[str, list[HoldingRow]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        """Single CSV file with rows grouped (and ordered) per account.

        The Account column is the first column of `HOLDINGS_HEADERS`
        so downstream tooling (Excel pivot, pandas groupby) can split
        the file back out without us having to emit account separator
        rows.
        """

        body: list[list[str]] = []
        for _account, rows in per_account:
            body.extend(
                schema.holdings_rows(rows, currency, money_symbols=False),
            )

        return write_csv(
            self.csv_dir / f"{base_filename}.csv",
            schema.HOLDINGS_HEADERS,
            body,
        )

    def _write_holdings_excel(
        self,
        per_account: list[tuple[str, list[HoldingRow]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        """One sheet per account."""

        sections = [
            ExcelSection(
                sheet_name=display_account_name(account) or "Unknown",
                headers=schema.HOLDINGS_HEADERS,
                body=schema.holdings_rows(rows, currency, money_symbols=False),
            )
            for account, rows in per_account
        ]
        if not sections:
            sections = [
                ExcelSection("Holdings", schema.HOLDINGS_HEADERS, [])
            ]

        return write_excel(self.excel_dir / f"{base_filename}.xlsx", sections)

    def _write_holdings_pdf(
        self,
        per_account: list[tuple[str, list[HoldingRow]]],
        base_filename: str,
        title: str,
        source_dates: dict[str, datetime],
        currency: str,
        family_footer: dict[str, str],
    ) -> Path:
        """One page per account, plus a "Family Total" strip at the end.

        Each per-account page carries its own subtotal so the page is
        self-contained; the family-wide grand total is then printed
        once after the last section via `write_pdf`'s `footer_totals`
        argument so it lands "at the end of the last page".
        """

        sections: list[PdfSection] = []
        for account, rows in per_account:
            account_invested = sum(
                (h.invested_amount for h in rows), start=ZERO,
            )
            sections.append(
                PdfSection(
                    subtitle=f"Account: {display_account_name(account)}",
                    headers=schema.HOLDINGS_HEADERS,
                    body=schema.holdings_rows(rows, currency, money_symbols=True),
                    totals={
                        "Total Positions": str(len(rows)),
                        "Total Invested":
                            format_money(account_invested, currency),
                    },
                    col_widths_mm=_HOLDINGS_COL_WIDTHS_MM,
                    wrap_columns=(_HOLDINGS_SYMBOL_COL_INDEX,),
                )
            )

        if not sections:
            sections = [
                PdfSection(
                    headers=schema.HOLDINGS_HEADERS,
                    body=[],
                    col_widths_mm=_HOLDINGS_COL_WIDTHS_MM,
                    wrap_columns=(_HOLDINGS_SYMBOL_COL_INDEX,),
                )
            ]

        return write_pdf(
            self.pdf_dir / f"{base_filename}.pdf",
            title=title,
            sections=sections,
            source_dates=_format_source_dates(source_dates),
            footer_totals=family_footer,
            footer_totals_title="Family Total",
            footer_notes=_INVESTED_CAPITAL_PDF_NOTES,
        )

    # ------------------------------------------------------------------
    # Combined family portfolio (single section)
    # ------------------------------------------------------------------
    def _write_combined(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        include_prices = payload.include_market_prices
        headers = schema.combined_headers(
            payload.account_names,
            include_market_prices=include_prices,
        )
        pdf_headers = schema.combined_pdf_headers(
            payload.account_names,
            include_market_prices=include_prices,
        )
        body_pdf = schema.combined_rows(
            payload.combined_portfolio,
            payload.account_names,
            payload.currency,
            money_symbols=True,
            include_market_prices=include_prices,
            compact=True,
        )
        body_tabular = schema.combined_rows(
            payload.combined_portfolio,
            payload.account_names,
            payload.currency,
            money_symbols=False,
            include_market_prices=include_prices,
        )

        total_invested, total_market, total_unrealized = (
            combined_family_price_totals(payload.combined_portfolio)
        )
        has_cash = any(r.is_cash for r in payload.combined_portfolio)
        n_isins = sum(1 for r in payload.combined_portfolio if not r.is_cash)
        totals = {
            "Securities (ISINs)": str(n_isins),
            "Total Invested (Family)": format_money(
                total_invested, payload.currency,
            ),
        }
        if include_prices:
            totals["Total Market Value"] = format_money(
                total_market, payload.currency,
            )
            totals["Total Unrealized G/L"] = format_money(
                total_unrealized, payload.currency,
            )

        n_without_quote = sum(
            1
            for r in payload.combined_portfolio
            if not r.is_cash and r.market_value is None
        ) if include_prices else 0

        combined_section_notes: tuple[str, ...] = (_INVESTED_CAPITAL_PDF_NOTES[0],)
        if has_cash:
            combined_section_notes = combined_section_notes + (
                "For the Cash row, per-account columns show current idle cash "
                "(currency) you entered—not share quantities.",
            )
        if include_prices:
            combined_section_notes = combined_section_notes + (
                "Current prices are indicative last quotes from Yahoo Finance "
                "(symbols resolved via OpenFIGI), shown in the report currency; "
                "non-EUR listings are converted using Yahoo FX rates. "
                "Allocation % is based on market value (cost for holdings "
                "without a live quote). Not licensed market data.",
            )
            if n_without_quote:
                combined_section_notes = combined_section_notes + (
                    f"Footer market value includes {n_without_quote} "
                    "holding(s) without a live quote at cost (invested amount); "
                    "total unrealized G/L is market value minus total invested.",
                )

        return self._dispatch_single_section(
            base_filename=f"combined_portfolio_{stamp}",
            sheet_name="Combined Portfolio",
            title="Combined Family Portfolio Report",
            headers=headers,
            body=body_pdf,
            totals=totals,
            formats=formats,
            tabular_body=body_tabular,
            source_dates=payload.source_dates,
            pdf_headers=pdf_headers,
            pdf_col_widths_mm=_combined_col_widths_mm(
                len(payload.account_names),
                include_market_prices=include_prices,
            ),
            pdf_wrap_columns=(_COMBINED_SYMBOL_COL_INDEX,),
            pdf_footer_notes=None,
            pdf_notes_after_totals=combined_section_notes,
        )

    # ------------------------------------------------------------------
    # Cost-basis transfer (split per account, family-wide footer total)
    # ------------------------------------------------------------------
    def _write_cost_basis(
        self,
        payload: ReportPayload,
        formats: list[ReportFormat],
        stamp: str,
    ) -> list[Path]:
        per_account = self._group_cost_basis_by_account(
            payload.cost_basis, payload.account_names,
        )

        family_total = sum(
            (r.cost_basis for r in payload.cost_basis), start=ZERO,
        )
        family_footer = {
            "Total Open Lots (Family)": str(len(payload.cost_basis)),
            "Total Cost Basis (Family)": format_money(
                family_total, payload.currency,
            ),
        }

        base_filename = f"cost_basis_transfer_{stamp}"
        title = "Cost Basis Transfer Report"
        outputs: list[Path] = []

        if ReportFormat.CSV in formats:
            outputs.append(self._write_cost_basis_csv(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.EXCEL in formats:
            outputs.append(self._write_cost_basis_excel(
                per_account, base_filename, payload.currency,
            ))

        if ReportFormat.PDF in formats:
            outputs.append(self._write_cost_basis_pdf(
                per_account,
                base_filename,
                title,
                payload.source_dates,
                payload.currency,
                family_footer,
            ))

        return outputs

    @staticmethod
    def _group_cost_basis_by_account(
        rows: list[CostBasisRow],
        ordered_account_names: list[str],
    ) -> list[tuple[str, list[CostBasisRow]]]:
        """Bucket cost-basis rows per account in `ordered_account_names`
        order. Mirrors `_group_holdings_by_account` and
        `_group_trades_by_account` so all per-account-split reports
        line up identically across formats.
        """

        buckets: dict[str, list[CostBasisRow]] = defaultdict(list)
        for row in rows:
            buckets[row.account_name].append(row)

        ordered: list[tuple[str, list[CostBasisRow]]] = []
        seen: set[str] = set()
        for name in ordered_account_names:
            ordered.append((name, buckets.get(name, [])))
            seen.add(name)

        for name in sorted(buckets.keys() - seen):
            ordered.append((name, buckets[name]))

        return ordered

    # ----- Per-format Cost-Basis writers -----------------------------
    def _write_cost_basis_csv(
        self,
        per_account: list[tuple[str, list[CostBasisRow]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        body: list[list[str]] = []
        for _account, rows in per_account:
            body.extend(
                schema.cost_basis_rows(rows, currency, money_symbols=False),
            )

        return write_csv(
            self.csv_dir / f"{base_filename}.csv",
            schema.COST_BASIS_HEADERS,
            body,
        )

    def _write_cost_basis_excel(
        self,
        per_account: list[tuple[str, list[CostBasisRow]]],
        base_filename: str,
        currency: str,
    ) -> Path:
        sections = [
            ExcelSection(
                sheet_name=display_account_name(account) or "Unknown",
                headers=schema.COST_BASIS_HEADERS,
                body=schema.cost_basis_rows(rows, currency, money_symbols=False),
            )
            for account, rows in per_account
        ]
        if not sections:
            sections = [
                ExcelSection("Cost Basis", schema.COST_BASIS_HEADERS, [])
            ]

        return write_excel(self.excel_dir / f"{base_filename}.xlsx", sections)

    def _write_cost_basis_pdf(
        self,
        per_account: list[tuple[str, list[CostBasisRow]]],
        base_filename: str,
        title: str,
        source_dates: dict[str, datetime],
        currency: str,
        family_footer: dict[str, str],
    ) -> Path:
        """One page per account, with a 'Family Total' strip at the end."""

        sections: list[PdfSection] = []
        for account, rows in per_account:
            account_basis = sum(
                (r.cost_basis for r in rows), start=ZERO,
            )
            sections.append(
                PdfSection(
                    subtitle=f"Account: {display_account_name(account)}",
                    headers=schema.COST_BASIS_HEADERS,
                    body=schema.cost_basis_rows(rows, currency, money_symbols=True),
                    totals={
                        "Open Lots": str(len(rows)),
                        "Total Cost Basis":
                            format_money(account_basis, currency),
                    },
                    col_widths_mm=_COST_BASIS_COL_WIDTHS_MM,
                    wrap_columns=(_COST_BASIS_SYMBOL_COL_INDEX,),
                )
            )

        if not sections:
            sections = [
                PdfSection(
                    headers=schema.COST_BASIS_HEADERS,
                    body=[],
                    col_widths_mm=_COST_BASIS_COL_WIDTHS_MM,
                    wrap_columns=(_COST_BASIS_SYMBOL_COL_INDEX,),
                )
            ]

        return write_pdf(
            self.pdf_dir / f"{base_filename}.pdf",
            title=title,
            sections=sections,
            source_dates=_format_source_dates(source_dates),
            notes=_COST_BASIS_PDF_NOTES,
            footer_totals=family_footer,
            footer_totals_title="Family Total",
        )

    # ------------------------------------------------------------------
    # Single-section dispatch shared by Holdings and Combined Portfolio
    # ------------------------------------------------------------------
    def _dispatch_single_section(
        self,
        *,
        base_filename: str,
        sheet_name: str,
        title: str,
        headers: list[str],
        body: list[list[str]],
        totals: dict[str, str],
        formats: list[ReportFormat],
        tabular_body: Optional[list[list[str]]] = None,
        source_dates: Optional[dict[str, datetime]] = None,
        pdf_headers: Optional[list[str]] = None,
        pdf_col_widths_mm: Optional[list[float]] = None,
        pdf_wrap_columns: tuple[int, ...] = (),
        pdf_footer_notes: Optional[Sequence[str]] = None,
        pdf_notes_after_totals: tuple[str, ...] = (),
    ) -> list[Path]:
        """Render one logical report through every requested format."""

        outputs: list[Path] = []
        csv_excel_body = tabular_body if tabular_body is not None else body

        if ReportFormat.CSV in formats:
            outputs.append(write_csv(
                self.csv_dir / f"{base_filename}.csv",
                headers,
                csv_excel_body,
            ))

        if ReportFormat.EXCEL in formats:
            outputs.append(write_excel(
                self.excel_dir / f"{base_filename}.xlsx",
                [
                    ExcelSection(
                        sheet_name=sheet_name,
                        headers=headers,
                        body=csv_excel_body,
                    )
                ],
            ))

        if ReportFormat.PDF in formats:
            outputs.append(write_pdf(
                self.pdf_dir / f"{base_filename}.pdf",
                title=title,
                sections=[PdfSection(
                    headers=pdf_headers if pdf_headers is not None else headers,
                    body=body,
                    totals=totals,
                    col_widths_mm=pdf_col_widths_mm,
                    wrap_columns=pdf_wrap_columns,
                    notes_after_totals=pdf_notes_after_totals,
                )],
                source_dates=_format_source_dates(source_dates),
                footer_notes=pdf_footer_notes,
            ))

        return outputs


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _format_source_dates(
    source_dates: Optional[dict[str, datetime]],
) -> Optional[dict[str, datetime]]:
    """Capitalize the keys of `source_dates` for display.

    The internal map uses raw folder names (e.g. ``"ramu"``) so lookups
    stay deterministic. The PDF renderer however shows these names
    directly to the operator, so we re-key the dict at the boundary.
    Returns ``None`` unchanged so the renderer keeps its skip semantics.
    """

    if not source_dates:
        return source_dates
    return {display_account_name(k): v for k, v in source_dates.items()}
