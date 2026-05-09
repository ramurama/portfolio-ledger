"""The unified `Transaction` model.

Every parser - whether for Scalable Capital today or another broker
tomorrow - produces a list of `Transaction` objects. The rest of the
application depends on this shape and nothing else, which is what
keeps broker integrations cheap to add.

Implementation notes
--------------------
* All monetary fields are `Decimal`. Pydantic v2 happily validates
  Decimals natively, so we get strict type-checking for free.
* `quantity` and `price` are optional because Distribution / Tax events
  legitimately do not have those fields.
* Timestamps are stored as naive `datetime` (broker's local timezone).
  See `app.utils.date_utils.parse_broker_datetime` for the rationale.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TransactionType


class Transaction(BaseModel):
    """Normalised single transaction row.

    The field set matches the project specification exactly. Optional
    fields are explicit so that downstream code can rely on `None`
    meaning "not applicable" rather than "missing data".
    """

    # ``frozen=True`` makes Transaction effectively immutable, which is
    # important because we hand the same object to multiple services
    # (FIFO engine, holdings calculator, report writers) and accidental
    # mutation would be a debugging nightmare.
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    account_name: str = Field(..., description="Owner / account folder name")
    date: datetime = Field(..., description="Trade date (broker local time)")
    isin: Optional[str] = Field(
        default=None, description="ISIN of the security; None for cash-only events"
    )
    symbol: Optional[str] = Field(
        default=None, description="Human-readable instrument label as exported"
    )
    transaction_type: TransactionType
    quantity: Optional[Decimal] = Field(
        default=None, description="Shares traded; None for Distribution/Tax rows"
    )
    price: Optional[Decimal] = Field(
        default=None, description="Per-share price in `currency`"
    )
    fees: Decimal = Field(
        default=Decimal("0"), description="Transaction fees (always non-negative)"
    )
    currency: str = Field(default="EUR", description="ISO-4217 currency code")
    total_amount: Decimal = Field(
        ...,
        description=(
            "Signed cash amount as reported by the broker. Negative for "
            "outflows (Buy / Savings plan), positive for inflows (Sell, "
            "Distribution). Includes fees so it can be reconciled "
            "directly against the broker statement."
        ),
    )

    # ------------------------------------------------------------------
    # Convenience helpers used by the reports and FIFO engine. They are
    # implemented here so the report writers stay declarative.
    # ------------------------------------------------------------------
    @property
    def gross_amount(self) -> Decimal:
        """Cash impact ignoring sign - useful for sanity reconciliation."""
        return abs(self.total_amount)

    @property
    def signed_quantity(self) -> Decimal:
        """Quantity with sign convention used by the FIFO engine.

        Buys / Savings plans are positive (they add lots), Sells are
        negative (they consume lots). For non-trade events we return 0.
        """

        if self.quantity is None:
            return Decimal("0")
        if self.transaction_type.is_disposal:
            return -self.quantity
        if self.transaction_type.is_acquisition:
            return self.quantity
        return Decimal("0")
