"""Build the per-account "current holdings" view.

After the FIFO engine has run, each (account, ISIN) has a list of open
lots representing un-sold acquisition fragments. This module folds those
open lots into one row per (account, ISIN), exposing the four metrics
required by the project specification:

    * total shares
    * average purchase price (weighted by lot size)
    * invested amount  (= total_shares * average_purchase_price)
    * remaining lot count

The result is a plain `list[HoldingRow]` so the report writers can turn
it into CSV / Excel / PDF without further transformation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from app.models import OpenLot
from app.utils.decimal_utils import ZERO, safe_divide


@dataclass(frozen=True)
class HoldingRow:
    """Aggregated current holding for one (account, ISIN)."""

    account_name: str
    isin: str
    symbol: str
    total_shares: Decimal
    average_purchase_price: Decimal
    invested_amount: Decimal
    remaining_lots: int


def build_current_holdings(open_lots: Iterable[OpenLot]) -> list[HoldingRow]:
    """Aggregate open lots into one row per (account, ISIN).

    Lots from the same account+ISIN may have different cost-per-share
    values (different buy dates / prices) so we compute a *weighted*
    average:

        avg_price = sum(remaining_shares_i * cost_per_share_i)
                    / sum(remaining_shares_i)

    `safe_divide` guards against zero-denominator edge cases (e.g. a lot
    that was fully consumed but somehow not popped - belt-and-braces).
    """

    # First pass: bucket the lots.
    buckets: dict[tuple[str, str], list[OpenLot]] = defaultdict(list)
    for lot in open_lots:
        buckets[(lot.account_name, lot.isin)].append(lot)

    rows: list[HoldingRow] = []
    for (account_name, isin), lots in buckets.items():
        total_shares = sum((lot.remaining_shares for lot in lots), start=ZERO)
        invested_amount = sum(
            (lot.remaining_cost_basis for lot in lots), start=ZERO
        )
        avg_price = safe_divide(invested_amount, total_shares)

        # Use the most recent symbol we have for that ISIN. Symbols can
        # drift slightly over time (Reuters renames, etc.) so always
        # picking the freshest value yields the friendliest report.
        symbol = max(lots, key=lambda lot: lot.buy_date).symbol

        rows.append(
            HoldingRow(
                account_name=account_name,
                isin=isin,
                symbol=symbol,
                total_shares=total_shares,
                average_purchase_price=avg_price,
                invested_amount=invested_amount,
                remaining_lots=len(lots),
            )
        )

    # Stable sort: account first, then symbol (case-insensitive) so the
    # report reads naturally regardless of insertion order.
    rows.sort(key=lambda r: (r.account_name.lower(), r.symbol.lower(), r.isin))
    return rows
