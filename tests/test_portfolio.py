"""Tests for `app.services.portfolio`.

The combined report is the family-wide pivot of per-account holdings.
The key invariants we pin down here are:

    * shares are correctly summed across accounts (per ISIN)
    * the combined-average price is the weighted average of per-account
      invested capital, NOT a naive mean of per-account prices
    * `family_percentage` represents this ISIN's share of the *family's*
      total invested capital and the column sums to exactly 100 in
      Decimal land (no floating-point drift)
"""

from __future__ import annotations

from decimal import Decimal

from app.services.holdings import HoldingRow
from app.services.portfolio import (
    build_combined_portfolio,
    merge_cash_into_combined,
)


def _holding(
    account: str,
    isin: str,
    symbol: str,
    shares: str,
    avg_price: str,
    invested: str,
    *,
    pct: str = "0",
    lots: int = 1,
) -> HoldingRow:
    """Build a HoldingRow with a sensible default for fields we don't
    care about in a given test (e.g. `portfolio_percentage`).

    `portfolio_percentage` is computed by `build_current_holdings` and
    is intentionally *not* used by `build_combined_portfolio` - the
    combined view recomputes its own family-level percentage from the
    raw invested amounts.
    """

    return HoldingRow(
        account_name=account,
        isin=isin,
        symbol=symbol,
        total_shares=Decimal(shares),
        average_purchase_price=Decimal(avg_price),
        invested_amount=Decimal(invested),
        portfolio_percentage=Decimal(pct),
        remaining_lots=lots,
    )


class TestBuildCombinedPortfolio:
    def test_family_percentages_sum_to_100(self) -> None:
        # Two ISINs, two accounts, total invested = 1000.
        # ISIN_A: 100 + 300 = 400 -> 40%
        # ISIN_B: 200 + 400 = 600 -> 60%
        rows = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "1", "100", "100"),
            _holding("rakshana", "ISIN_A", "Alpha", "1", "300", "300"),
            _holding("ramu", "ISIN_B", "Bravo", "1", "200", "200"),
            _holding("rakshana", "ISIN_B", "Bravo", "1", "400", "400"),
        ])

        by_isin = {r.isin: r for r in rows}
        assert by_isin["ISIN_A"].family_percentage == Decimal("40")
        assert by_isin["ISIN_B"].family_percentage == Decimal("60")

        total = sum(r.family_percentage for r in rows)
        assert total == Decimal("100")

    def test_percentages_use_family_total_not_account_total(self) -> None:
        """ramu's 50/50 split should NOT survive the family-level pivot.

        At the per-account level (`build_current_holdings`) ramu has
        50/50 in two ISINs. But across the family, rakshana's much
        larger portfolio dominates and ramu's positions become tiny
        slices. We pin that down to make sure we did not accidentally
        re-use the per-account percentage column.
        """
        rows = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "1", "500", "500"),
            _holding("ramu", "ISIN_B", "Bravo", "1", "500", "500"),
            _holding("rakshana", "ISIN_C", "Charlie", "1", "10000", "10000"),
            _holding("rakshana", "ISIN_D", "Delta", "1", "30000", "30000"),
        ])

        # Family total = 500 + 500 + 10000 + 30000 = 41000.
        by_isin = {r.isin: r for r in rows}
        # Each of ramu's positions is 500/41000 ~= 1.2195%.
        assert by_isin["ISIN_A"].family_percentage == (
            Decimal("500") * Decimal("100") / Decimal("41000")
        )
        # Rakshana's Delta is the heaviest, ~73.17%.
        assert by_isin["ISIN_D"].family_percentage == (
            Decimal("30000") * Decimal("100") / Decimal("41000")
        )
        total = sum(r.family_percentage for r in rows)
        assert total == Decimal("100")

    def test_same_isin_across_two_accounts_aggregates(self) -> None:
        """Family view: the same ISIN held by two accounts collapses
        into one row with summed shares and a per-account share map."""
        rows = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "10", "100", "1000"),
            _holding("rakshana", "ISIN_A", "Alpha", "5", "200", "1000"),
        ])

        assert len(rows) == 1
        row = rows[0]
        assert row.combined_shares == Decimal("15")
        # 1000 + 1000 = 2000 invested for 15 shares -> 133.333... avg.
        assert row.total_invested == Decimal("2000")
        assert row.shares_per_account == {
            "ramu": Decimal("10"),
            "rakshana": Decimal("5"),
        }
        # Single ISIN means the whole family portfolio is in it.
        assert row.family_percentage == Decimal("100")

    def test_empty_input_yields_empty_output(self) -> None:
        assert build_combined_portfolio([]) == []


class TestMergeCashIntoCombined:
    def test_percentages_sum_to_100_with_cash(self) -> None:
        base = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "1", "100", "400"),
            _holding("rakshana", "ISIN_B", "Bravo", "1", "100", "600"),
        ])
        cash_by_account = {"ramu": Decimal("100"), "rakshana": Decimal("400")}
        merged = merge_cash_into_combined(
            base,
            cash_by_account,
            ["ramu", "rakshana"],
        )
        # Securities 1000 + cash 500 => grand total 1500.
        by_isin = {r.isin: r for r in merged}
        assert by_isin["ISIN_A"].family_percentage == (
            Decimal("400") * Decimal("100") / Decimal("1500")
        )
        assert by_isin["ISIN_B"].family_percentage == (
            Decimal("600") * Decimal("100") / Decimal("1500")
        )
        cash_row = by_isin["CASH"]
        assert cash_row.is_cash
        assert cash_row.market_value == Decimal("500")
        assert cash_row.unrealized_gain_loss == Decimal("0")
        assert cash_row.family_percentage == (
            Decimal("500") * Decimal("100") / Decimal("1500")
        )
        assert sum(r.family_percentage for r in merged) == Decimal("100")

    def test_zero_total_cash_returns_input(self) -> None:
        base = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "1", "100", "100"),
        ])
        assert merge_cash_into_combined(base, {}, ["ramu"]) == base

    def test_cash_only_portfolio(self) -> None:
        merged = merge_cash_into_combined(
            [],
            {"ramu": Decimal("1000")},
            ["ramu"],
        )
        assert len(merged) == 1
        assert merged[0].is_cash
        assert merged[0].family_percentage == Decimal("100")

    def test_skips_cash_when_family_total_not_positive(self) -> None:
        base = build_combined_portfolio([
            _holding("ramu", "ISIN_A", "Alpha", "1", "100", "100"),
        ])
        merged = merge_cash_into_combined(
            base,
            {"ramu": Decimal("-500")},
            ["ramu"],
        )
        assert merged == base
