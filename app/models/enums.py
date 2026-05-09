"""Enumerations describing the unified transaction taxonomy.

The Scalable Capital CSV uses a fixed but broker-specific vocabulary
("Buy", "Sell", "Savings plan", "Distribution", "Taxes", ...). We map
those into a smaller, broker-agnostic enum so the FIFO engine and
report writers never have to special-case a particular broker's wording.
"""

from __future__ import annotations

from enum import Enum


class TransactionType(str, Enum):
    """Canonical transaction categories used by the FIFO engine.

    Membership in this enum is the contract between parsers and the
    rest of the system: anything not in the enum is dropped during
    ingestion.
    """

    BUY = "Buy"
    SELL = "Sell"
    SAVINGS_PLAN = "Savings plan"
    DISTRIBUTION = "Distribution"
    TAX = "Tax"
    SECURITY_TRANSFER = "Security transfer"

    @property
    def is_acquisition(self) -> bool:
        """Return True for events that *add* to the FIFO lot queue."""
        return self in {TransactionType.BUY, TransactionType.SAVINGS_PLAN}

    @property
    def is_disposal(self) -> bool:
        """Return True for events that *consume* the FIFO lot queue."""
        return self is TransactionType.SELL

    @property
    def is_security_transfer(self) -> bool:
        """Return True for broker-reported security transfer movements."""
        return self is TransactionType.SECURITY_TRANSFER
