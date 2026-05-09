"""Enumerations describing the unified transaction taxonomy.

The Scalable Capital CSV uses a fixed but broker-specific vocabulary
("Buy", "Sell", "Savings plan", "Distribution", "Taxes", ...). We map
those into a smaller, broker-agnostic enum so the tax-lot engine and
report writers never have to special-case a particular broker's wording.
"""

from __future__ import annotations

from enum import Enum


class TransactionType(str, Enum):
    """Canonical transaction categories used by the tax-lot engine.

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
    CORPORATE_ACTION = "Corporate action"

    @property
    def is_acquisition(self) -> bool:
        """Return True for events that *add* to the tax-lot queue."""
        return self in {TransactionType.BUY, TransactionType.SAVINGS_PLAN}

    @property
    def is_disposal(self) -> bool:
        """Return True for events that *consume* the tax-lot queue."""
        return self is TransactionType.SELL

    @property
    def is_security_transfer(self) -> bool:
        """Return True for broker-reported security transfer movements."""
        return self is TransactionType.SECURITY_TRANSFER

    @property
    def is_corporate_action(self) -> bool:
        """Return True for broker-reported corporate action movements.

        Modelled with a deliberately simple semantics:

            * ``+qty`` rows (free shares from spin-offs, scrip dividends,
              splits, ticker swaps that add a new ISIN) are admitted into
              the tax-lot queue at **zero cost basis**. Subsequent sells
              of those shares therefore book the full proceeds as
              realized gain - the German Finanzamt's proportional cost-
              basis split for spin-offs would be more accurate but
              requires per-action ratios that are not present in the
              broker export.
            * ``-qty`` rows (the broker reducing a parent position when
              shares are converted into a successor security) are
              ignored. The original lots stay in the queue; this
              over-states the parent's surviving cost basis by a small
              amount but keeps the model fully automatic.
        """
        return self is TransactionType.CORPORATE_ACTION
