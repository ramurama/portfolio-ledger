"""Data classes used by the FIFO engine.

The FIFO engine produces two complementary outputs:

    1. A queue of *open* lots - the bits of the original purchases that
       have not yet been sold. These describe the current holding.
    2. A list of *realized* trades - one entry per buy-lot consumed by
       a sell, capturing the gain/loss that materialised.

Both are simple value objects, so we use `dataclass(frozen=False)` for
the open lots (because we mutate `remaining_shares` as sells consume
them) and `frozen=True` for realized trades (immutable history).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class OpenLot:
    """A still-held purchase fragment tracked by the FIFO queue.

    Each Buy / Savings plan transaction creates one OpenLot. As Sells
    consume shares from the front of the queue we decrement
    `remaining_shares`; lots that hit zero are popped off entirely.

    `cost_per_share` already includes any pro-rata fees that were paid
    on the original acquisition, so the realized P&L calculation can
    simply do `proceeds - shares_sold * cost_per_share` without having
    to reach back into fee tables.
    """

    account_name: str
    isin: str
    symbol: str
    buy_date: datetime
    original_shares: Decimal
    remaining_shares: Decimal
    cost_per_share: Decimal

    @property
    def remaining_cost_basis(self) -> Decimal:
        """Acquisition cost still locked up in this lot."""
        return self.remaining_shares * self.cost_per_share


@dataclass(frozen=True)
class RealizedTrade:
    """A buy-fragment matched against a sell - i.e. a closed position.

    One Sell transaction can produce *multiple* RealizedTrades, one per
    buy-lot it consumed (this is how partial lot consumption surfaces in
    the FIFO report). The `realized_gain_loss` field is signed:
    positive = profit, negative = loss.
    """

    account_name: str
    isin: str
    symbol: str
    buy_date: datetime
    sell_date: datetime
    shares_sold: Decimal
    acquisition_cost: Decimal  # shares_sold * original cost_per_share
    sale_proceeds: Decimal     # shares_sold * sell price (after fees, pro-rata)
    realized_gain_loss: Decimal = field(init=False)

    def __post_init__(self) -> None:
        # `frozen=True` blocks normal attribute assignment, so we go
        # through `object.__setattr__` to populate the derived field.
        object.__setattr__(
            self,
            "realized_gain_loss",
            self.sale_proceeds - self.acquisition_cost,
        )
