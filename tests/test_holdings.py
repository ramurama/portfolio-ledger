"""Tests for `app.services.holdings`.

The portfolio-percentage column is the most important behaviour to pin
down here - it has to be computed *per account* (each row's percentage
sums to 100 within the owning account), with full Decimal precision.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import OpenLot
from app.services.holdings import build_current_holdings


def _lot(
    account: str,
    isin: str,
    symbol: str,
    shares: str,
    cost_per_share: str,
    when: datetime = datetime(2024, 1, 1),
) -> OpenLot:
    return OpenLot(
        account_name=account,
        isin=isin,
        symbol=symbol,
        buy_date=when,
        original_shares=Decimal(shares),
        remaining_shares=Decimal(shares),
        cost_per_share=Decimal(cost_per_share),
    )


class TestBuildCurrentHoldings:
    def test_single_account_percentages_sum_to_100(self) -> None:
        # Account total = 100 + 300 + 600 = 1000.
        # Expected: 10%, 30%, 60%.
        rows = build_current_holdings([
            _lot("ramu", "ISIN_A", "Alpha", "1", "100"),
            _lot("ramu", "ISIN_B", "Bravo", "1", "300"),
            _lot("ramu", "ISIN_C", "Charlie", "1", "600"),
        ])

        by_isin = {r.isin: r for r in rows}
        assert by_isin["ISIN_A"].portfolio_percentage == Decimal("10")
        assert by_isin["ISIN_B"].portfolio_percentage == Decimal("30")
        assert by_isin["ISIN_C"].portfolio_percentage == Decimal("60")

        # And the percentages should sum to exactly 100 in Decimal land.
        total = sum(r.portfolio_percentage for r in rows)
        assert total == Decimal("100")

    def test_percentages_are_per_account(self) -> None:
        """ramu's 50/50 split should be unaffected by rakshana's holdings."""
        rows = build_current_holdings([
            _lot("ramu", "ISIN_A", "Alpha", "1", "500"),
            _lot("ramu", "ISIN_B", "Bravo", "1", "500"),
            # rakshana has totally different totals - these must not
            # leak into ramu's percentage calculation.
            _lot("rakshana", "ISIN_C", "Charlie", "1", "10000"),
            _lot("rakshana", "ISIN_D", "Delta", "1", "30000"),
        ])

        by_key = {(r.account_name, r.isin): r for r in rows}
        assert by_key[("ramu", "ISIN_A")].portfolio_percentage == Decimal("50")
        assert by_key[("ramu", "ISIN_B")].portfolio_percentage == Decimal("50")
        assert by_key[("rakshana", "ISIN_C")].portfolio_percentage == Decimal("25")
        assert by_key[("rakshana", "ISIN_D")].portfolio_percentage == Decimal("75")

    def test_multiple_lots_for_same_isin_aggregate(self) -> None:
        """Two lots of the same security collapse into a single row."""
        rows = build_current_holdings([
            _lot("ramu", "ISIN_A", "Alpha", "5", "100"),  # 500 invested
            _lot("ramu", "ISIN_A", "Alpha", "5", "200"),  # 1000 invested
            _lot("ramu", "ISIN_B", "Bravo", "1", "500"),  # 500 invested
        ])

        # Total = 500 + 1000 + 500 = 2000.
        # Alpha = 1500/2000 = 75%, Bravo = 25%.
        by_isin = {r.isin: r for r in rows}
        assert by_isin["ISIN_A"].invested_amount == Decimal("1500")
        assert by_isin["ISIN_A"].portfolio_percentage == Decimal("75")
        assert by_isin["ISIN_A"].remaining_lots == 2
        assert by_isin["ISIN_B"].portfolio_percentage == Decimal("25")
