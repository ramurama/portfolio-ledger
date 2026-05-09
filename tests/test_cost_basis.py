"""Tests for `app.services.cost_basis`.

The cost-basis projection is intentionally lossless: each still-open
FIFO lot becomes one row in the report. Two invariants matter:

    * lots are NEVER aggregated by ISIN - the receiving broker (IBKR)
      needs the per-lot cost so future sells can be tax-matched
    * Decimal precision is preserved end-to-end (no float coercion,
      `cost_basis = quantity * cost_per_share` exactly)

Sorting is the third piece worth pinning down because the operator
relies on the order to fill IBKR's intake form efficiently.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import OpenLot
from app.services.cost_basis import CostBasisRow, build_cost_basis_rows


def _lot(
    account: str,
    isin: str,
    symbol: str,
    shares: str,
    cost_per_share: str,
    when: datetime = datetime(2024, 1, 1),
    remaining: str | None = None,
) -> OpenLot:
    """Build an OpenLot. `remaining` defaults to `shares` (a fresh,
    untouched lot); pass it explicitly to simulate a partially-sold
    lot."""

    original = Decimal(shares)
    return OpenLot(
        account_name=account,
        isin=isin,
        symbol=symbol,
        buy_date=when,
        original_shares=original,
        remaining_shares=Decimal(remaining) if remaining else original,
        cost_per_share=Decimal(cost_per_share),
    )


class TestBuildCostBasisRows:
    def test_one_row_per_lot_no_aggregation(self) -> None:
        """Two lots of the same ISIN MUST stay as two rows.

        This is the headline guarantee of the report: the receiving
        broker needs the exact per-lot cost basis, not an average.
        """
        rows = build_cost_basis_rows([
            _lot("ramu", "ISIN_A", "Alpha", "5", "100",
                 when=datetime(2023, 1, 1)),
            _lot("ramu", "ISIN_A", "Alpha", "5", "200",
                 when=datetime(2024, 1, 1)),
            _lot("ramu", "ISIN_B", "Bravo", "1", "500"),
        ])

        # 2 Alpha lots + 1 Bravo lot = 3 rows.
        assert len(rows) == 3

        alpha_rows = [r for r in rows if r.isin == "ISIN_A"]
        assert len(alpha_rows) == 2
        # Different cost-per-share values are preserved verbatim.
        assert {r.cost_per_share for r in alpha_rows} == {
            Decimal("100"), Decimal("200"),
        }

    def test_uses_remaining_shares_not_original(self) -> None:
        """`quantity` reflects what is still held, since fully or
        partially-consumed shares are not part of any future transfer.
        Cost basis is then quantity x cost_per_share, again using the
        remaining quantity."""
        rows = build_cost_basis_rows([
            _lot("ramu", "ISIN_A", "Alpha", "10", "100",
                 remaining="3"),
        ])

        assert len(rows) == 1
        row = rows[0]
        assert row.quantity == Decimal("3")
        assert row.cost_per_share == Decimal("100")
        assert row.cost_basis == Decimal("300")

    def test_zero_remaining_shares_are_skipped(self) -> None:
        """A lot whose `remaining_shares` is 0 should never reach the
        report - it has nothing to transfer. The FIFO engine pops
        consumed lots so we expect zero, but defending against a
        non-pruned input list is cheap."""
        rows = build_cost_basis_rows([
            _lot("ramu", "ISIN_A", "Alpha", "10", "100",
                 remaining="0"),
            _lot("ramu", "ISIN_B", "Bravo", "1", "500"),
        ])

        assert [r.isin for r in rows] == ["ISIN_B"]

    def test_sorted_by_account_then_symbol_isin_date(self) -> None:
        rows = build_cost_basis_rows([
            _lot("ramu", "ISIN_B", "Bravo", "1", "100"),
            _lot("rakshana", "ISIN_A", "Alpha", "1", "100",
                 when=datetime(2024, 6, 1)),
            _lot("ramu", "ISIN_A", "Alpha", "1", "100",
                 when=datetime(2023, 1, 1)),
            _lot("ramu", "ISIN_A", "Alpha", "1", "100",
                 when=datetime(2024, 1, 1)),
        ])

        keys = [(r.account_name, r.symbol, r.acquisition_date) for r in rows]
        assert keys == [
            ("rakshana", "Alpha", datetime(2024, 6, 1)),
            ("ramu",     "Alpha", datetime(2023, 1, 1)),
            ("ramu",     "Alpha", datetime(2024, 1, 1)),
            ("ramu",     "Bravo", datetime(2024, 1, 1)),
        ]

    def test_decimal_precision_preserved(self) -> None:
        """Fractional savings-plan shares often have 6 dp on the
        quantity side and 4 dp on the price side; multiplying them
        must not introduce float drift."""
        rows = build_cost_basis_rows([
            _lot("ramu", "ISIN_A", "Alpha",
                 shares="0.123456", cost_per_share="123.4567"),
        ])

        assert len(rows) == 1
        row = rows[0]
        assert row.cost_basis == Decimal("0.123456") * Decimal("123.4567")

    def test_empty_input_yields_empty_output(self) -> None:
        assert build_cost_basis_rows([]) == []

    def test_from_open_lot_uses_remaining_cost_basis(self) -> None:
        """Cross-check that `CostBasisRow.from_open_lot` reuses the
        same `remaining_cost_basis` property as the rest of the app,
        so no rounding diverges between reports."""
        lot = _lot("ramu", "ISIN_A", "Alpha", "10", "100", remaining="3")
        row = CostBasisRow.from_open_lot(lot)
        assert row.cost_basis == lot.remaining_cost_basis
