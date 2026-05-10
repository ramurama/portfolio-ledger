"""Tests for `app.reports.report_manager`.

Focused on the per-account bucketing helpers used by the Holdings,
Tax Lots and Cost-Basis writers - the helpers that decide which rows
land on which PDF page / Excel sheet / CSV group.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import RealizedTrade
from app.reports.report_manager import (
    ReportFormat,
    ReportKind,
    ReportManager,
    ReportPayload,
)
from app.services.cost_basis import CostBasisRow
from app.services.holdings import HoldingRow


def _holding(account: str, isin: str, symbol: str = "Sym") -> HoldingRow:
    """Minimal HoldingRow factory; only the fields the bucketing
    helper consults are non-default. Decimals are zero so equality
    checks below stay obvious."""

    return HoldingRow(
        account_name=account,
        isin=isin,
        symbol=symbol,
        total_shares=Decimal("0"),
        average_purchase_price=Decimal("0"),
        invested_amount=Decimal("0"),
        portfolio_percentage=Decimal("0"),
        remaining_lots=0,
    )


def _cost_basis(account: str, isin: str, symbol: str = "Sym") -> CostBasisRow:
    """Minimal CostBasisRow factory mirroring `_holding` above."""

    return CostBasisRow(
        account_name=account,
        isin=isin,
        symbol=symbol,
        acquisition_date=datetime(2024, 1, 1),
        quantity=Decimal("1"),
        cost_per_share=Decimal("0"),
        cost_basis=Decimal("0"),
    )


def _realized_trade(
    account: str,
    acquisition_cost: str,
    sale_proceeds: str,
) -> RealizedTrade:
    """Minimal RealizedTrade factory for report-total tests."""

    return RealizedTrade(
        account_name=account,
        isin="ISIN_A",
        symbol="Sym",
        buy_date=datetime(2024, 1, 1),
        sell_date=datetime(2024, 2, 1),
        shares_sold=Decimal("1"),
        acquisition_cost=Decimal(acquisition_cost),
        sale_proceeds=Decimal(sale_proceeds),
    )


class TestGroupHoldingsByAccount:
    def test_buckets_in_account_order(self) -> None:
        """Bucket order MUST follow `ordered_account_names`, not the
        insertion order of the input list. This is what guarantees the
        PDF page order is identical to the Tax Lots report and the
        ingestion log."""
        holdings = [
            _holding("ramu", "ISIN_A"),
            _holding("rakshana", "ISIN_B"),
            _holding("ramu", "ISIN_C"),
        ]

        result = ReportManager._group_holdings_by_account(
            holdings, ["rakshana", "ramu"],
        )

        assert [name for name, _ in result] == ["rakshana", "ramu"]
        assert [r.isin for r in result[0][1]] == ["ISIN_B"]
        assert [r.isin for r in result[1][1]] == ["ISIN_A", "ISIN_C"]

    def test_account_with_no_holdings_still_appears(self) -> None:
        """An account that exists on disk but happens to have nothing
        invested still gets a page/sheet so the report visibly covers
        every account the operator gave us."""
        holdings = [_holding("ramu", "ISIN_A")]

        result = ReportManager._group_holdings_by_account(
            holdings, ["ramu", "rakshana"],
        )

        assert result == [
            ("ramu", [holdings[0]]),
            ("rakshana", []),
        ]

    def test_unknown_accounts_are_appended_alphabetically(self) -> None:
        """Defensive: a HoldingRow whose account is not in the
        `ordered_account_names` list should still surface in the report
        (so the operator can investigate) and we fall back to a stable
        alphabetical order for those orphans."""
        holdings = [
            _holding("zeta", "ISIN_Z"),
            _holding("alpha", "ISIN_A"),
            _holding("ramu", "ISIN_R"),
        ]

        result = ReportManager._group_holdings_by_account(
            holdings, ["ramu"],
        )

        assert [name for name, _ in result] == ["ramu", "alpha", "zeta"]

    def test_empty_inputs(self) -> None:
        assert ReportManager._group_holdings_by_account([], []) == []
        assert ReportManager._group_holdings_by_account([], ["ramu"]) == [
            ("ramu", []),
        ]


class TestGroupCostBasisByAccount:
    """Cost-basis bucketing must mirror Holdings / Tax Lots bucketing
    exactly so all per-account-split reports line up across the three
    commands.
    """

    def test_buckets_in_account_order(self) -> None:
        rows = [
            _cost_basis("ramu", "ISIN_A"),
            _cost_basis("rakshana", "ISIN_B"),
            _cost_basis("ramu", "ISIN_C"),
        ]

        result = ReportManager._group_cost_basis_by_account(
            rows, ["rakshana", "ramu"],
        )

        assert [name for name, _ in result] == ["rakshana", "ramu"]
        assert [r.isin for r in result[0][1]] == ["ISIN_B"]
        assert [r.isin for r in result[1][1]] == ["ISIN_A", "ISIN_C"]

    def test_account_with_no_lots_still_appears(self) -> None:
        rows = [_cost_basis("ramu", "ISIN_A")]

        result = ReportManager._group_cost_basis_by_account(
            rows, ["ramu", "rakshana"],
        )

        assert result == [
            ("ramu", [rows[0]]),
            ("rakshana", []),
        ]

    def test_unknown_accounts_are_appended_alphabetically(self) -> None:
        rows = [
            _cost_basis("zeta", "ISIN_Z"),
            _cost_basis("alpha", "ISIN_A"),
            _cost_basis("ramu", "ISIN_R"),
        ]

        result = ReportManager._group_cost_basis_by_account(
            rows, ["ramu"],
        )

        assert [name for name, _ in result] == ["ramu", "alpha", "zeta"]

    def test_empty_inputs(self) -> None:
        assert ReportManager._group_cost_basis_by_account([], []) == []
        assert ReportManager._group_cost_basis_by_account([], ["ramu"]) == [
            ("ramu", []),
        ]


class TestTaxLotsPdfTotals:
    def test_pdf_includes_account_and_report_totals(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_write_pdf(*args, **kwargs):
            captured["kwargs"] = kwargs
            return args[0]

        monkeypatch.setattr(
            "app.reports.report_manager.write_pdf",
            fake_write_pdf,
        )

        manager = ReportManager(pdf_dir=tmp_path)
        trades = [
            _realized_trade("ramu", "10", "15"),
            _realized_trade("rakshana", "20", "27.50"),
        ]

        path = manager._write_tax_lots_pdf(
            per_account=[
                ("ramu", [trades[0]]),
                ("rakshana", [trades[1]]),
            ],
            base_filename="tax_lots_report_test",
            title="Tax Lots Realized Gains Report",
            source_dates={},
            currency="EUR",
        )

        assert path == tmp_path / "tax_lots_report_test.pdf"
        kwargs = captured["kwargs"]
        sections = kwargs["sections"]
        assert sections[0].totals == {
            "Account Realized Trades": "1",
            "Account Realized Gain/Loss": "€5.00",
        }
        assert sections[1].totals == {
            "Account Realized Trades": "1",
            "Account Realized Gain/Loss": "€7.50",
        }
        assert kwargs["footer_totals"] == {
            "Total Realized Trades": "2",
            "Total Realized Gain/Loss": "€12.50",
        }
        assert kwargs["footer_totals_title"] == "Tax Lots Total"


class TestReportManagerWriteSelection:
    def test_write_emits_only_requested_reports(
        self, tmp_path: Path,
    ) -> None:
        stamp = "1999-01-01_00-00-00"
        manager = ReportManager(
            csv_dir=tmp_path,
            excel_dir=tmp_path,
            pdf_dir=tmp_path,
        )
        paths = manager.write(
            ReportPayload(account_names=["ramu"]),
            report_formats={
                ReportKind.HOLDINGS: [ReportFormat.CSV],
            },
            generated_at=datetime(1999, 1, 1, 0, 0, 0),
        )
        assert len(paths) == 1
        assert paths[0].name == f"current_holdings_{stamp}.csv"
