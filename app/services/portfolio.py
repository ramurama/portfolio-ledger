"""Combined family-portfolio aggregation.

Given the per-account `HoldingRow` list produced by
`build_current_holdings`, this module merges holdings *across* accounts
to produce one row per ISIN. The output captures:

    * shares held by each individual account (one column per account)
    * combined total shares
    * combined weighted-average purchase price
    * combined total invested capital

The schema mirrors what the PDF / Excel report needs while remaining a
plain list of dataclasses so unit tests can assert on the values without
parsing rendered output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from app.services.holdings import HoldingRow
from app.utils.decimal_utils import ZERO, safe_divide


@dataclass(frozen=True)
class CombinedHoldingRow:
    """One row in the combined family portfolio report."""

    isin: str
    symbol: str
    # Map of account_name -> shares held in that account. Accounts with
    # no position in the ISIN are simply absent from the dict (rather
    # than carrying an explicit zero) so the report writer can decide
    # how to render the missing cells.
    shares_per_account: dict[str, Decimal]
    combined_shares: Decimal
    combined_average_price: Decimal
    total_invested: Decimal

    @property
    def account_names(self) -> list[str]:
        """Sorted account names that hold this ISIN."""
        return sorted(self.shares_per_account.keys())


@dataclass
class _IsinAggregate:
    """Mutable accumulator used while folding HoldingRows."""

    isin: str
    symbol: str = ""
    shares_per_account: dict[str, Decimal] = field(default_factory=dict)
    combined_shares: Decimal = ZERO
    total_invested: Decimal = ZERO

    def add(self, row: HoldingRow) -> None:
        # An ISIN can appear under different display names in different
        # exports - we keep the last non-empty one we see.
        if row.symbol:
            self.symbol = row.symbol

        self.shares_per_account[row.account_name] = (
            self.shares_per_account.get(row.account_name, ZERO)
            + row.total_shares
        )
        self.combined_shares += row.total_shares
        self.total_invested += row.invested_amount


def build_combined_portfolio(
    holdings: Iterable[HoldingRow],
) -> list[CombinedHoldingRow]:
    """Pivot per-account holdings into one combined row per ISIN."""

    aggregates: dict[str, _IsinAggregate] = defaultdict(_IsinAggregate)
    for row in holdings:
        agg = aggregates.setdefault(row.isin, _IsinAggregate(isin=row.isin))
        agg.add(row)

    combined: list[CombinedHoldingRow] = []
    for agg in aggregates.values():
        # Weighted average across accounts uses the same formula as
        # `build_current_holdings` - just at one level higher.
        avg_price = safe_divide(agg.total_invested, agg.combined_shares)

        combined.append(
            CombinedHoldingRow(
                isin=agg.isin,
                symbol=agg.symbol,
                shares_per_account=dict(agg.shares_per_account),
                combined_shares=agg.combined_shares,
                combined_average_price=avg_price,
                total_invested=agg.total_invested,
            )
        )

    combined.sort(key=lambda r: (r.symbol.lower(), r.isin))
    return combined
