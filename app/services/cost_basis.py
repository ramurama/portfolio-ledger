"""Per-lot cost-basis view used for broker transfers (e.g. IBKR).

Most reports surface AVERAGED metrics per ISIN: total shares, weighted
average price, allocation. That works for "what do I own" but is
useless when transferring assets between brokers, because the receiving
broker (IBKR) needs the acquisition price of *each lot* to keep future
sells correctly tax-matched.

The tax-lot engine already produces the exact data we need: every
still-held purchase fragment lives in `TaxLotResult.open_lots` with its
own `buy_date` and `cost_per_share` (`app.services.tax_lot_engine`).
This module is a thin presentational projection of those `OpenLot`
records:

    * one row per open lot (NEVER aggregated by ISIN)
    * sorted per account, then by symbol / ISIN / acquisition date so
      the operator can fill an IBKR form one security at a time, oldest
      lot first

The output is consumed by the report writers via `app.reports._schema`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from app.models import OpenLot
from app.utils.decimal_utils import ZERO


@dataclass(frozen=True)
class CostBasisRow:
    """One open lot, projected for the cost-basis transfer report.

    All numeric fields are taken straight from the source `OpenLot` so
    no information is lost in the projection - this is intentional:
    the report layer must be able to reproduce the broker-required
    `quantity * cost_per_share` exactly without rounding mid-flight.
    """

    account_name: str
    isin: str
    symbol: str
    acquisition_date: datetime
    quantity: Decimal
    cost_per_share: Decimal
    cost_basis: Decimal

    @classmethod
    def from_open_lot(cls, lot: OpenLot) -> "CostBasisRow":
        """Project an `OpenLot` into a `CostBasisRow`.

        We use `remaining_shares` (NOT `original_shares`): the report
        describes what is still held today, since fully-consumed shares
        are not part of any future transfer.
        """

        return cls(
            account_name=lot.account_name,
            isin=lot.isin,
            symbol=lot.symbol,
            acquisition_date=lot.buy_date,
            quantity=lot.remaining_shares,
            cost_per_share=lot.cost_per_share,
            cost_basis=lot.remaining_cost_basis,
        )


def build_cost_basis_rows(open_lots: Iterable[OpenLot]) -> list[CostBasisRow]:
    """Project every still-open lot into a sorted list of report rows.

    Sort key
    --------
    `(account_name.lower, symbol.lower, isin, acquisition_date)`

    Why this order? Operators typically work through one security at a
    time on IBKR's intake form, oldest lot first - that matches the
    natural chronological ordering the broker uses for future sell-side
    tax calculations. Account is the outermost grouping because each
    sub-account transfers independently.

    Defensively skip any lot with non-positive `remaining_shares`. The
    tax-lot engine pops fully-consumed lots, but a future caller might
    feed us a list that has not been pruned, and a zero-share row is
    nonsense in a transfer context.
    """

    rows = [
        CostBasisRow.from_open_lot(lot)
        for lot in open_lots
        if lot.remaining_shares > ZERO
    ]
    rows.sort(
        key=lambda r: (
            r.account_name.lower(),
            r.symbol.lower(),
            r.isin,
            r.acquisition_date,
        )
    )
    return rows
