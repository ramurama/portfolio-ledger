"""Tests for `app.reports.report_manager`.

Focused on the per-account bucketing helpers used by the Holdings,
FIFO and Cost-Basis writers - the helpers that decide which rows
land on which PDF page / Excel sheet / CSV group.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.reports.report_manager import ReportManager
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


class TestGroupHoldingsByAccount:
    def test_buckets_in_account_order(self) -> None:
        """Bucket order MUST follow `ordered_account_names`, not the
        insertion order of the input list. This is what guarantees the
        PDF page order is identical to the FIFO report and the
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
    """Cost-basis bucketing must mirror Holdings/FIFO bucketing exactly
    so all per-account-split reports line up across the three commands.
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
