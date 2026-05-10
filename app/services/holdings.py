"""Build the per-account "current holdings" view.

After the tax-lot engine has run, each (account, ISIN) has a list of
open lots representing un-sold acquisition fragments. This module folds
those open lots into one row per (account, ISIN), exposing the metrics
required by the project specification:

    * total shares
    * average purchase price (weighted by lot size)
    * invested amount  (= total_shares * average_purchase_price)
    * portfolio percentage  (this position's share of the account's
                             total invested capital)
    * remaining lot count

The result is a plain `list[HoldingRow]` so the report writers can turn
it into CSV / Excel / PDF without further transformation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence

from app.models import OpenLot
from app.utils.decimal_utils import ZERO, safe_divide

_PositionKey = tuple[str, str]


# Used as the multiplier when converting a fraction into a percentage.
# Pre-built so we never accidentally introduce a `float`.
_HUNDRED: Decimal = Decimal("100")


@dataclass(frozen=True)
class HoldingRow:
    """Aggregated current holding for one (account, ISIN)."""

    account_name: str
    isin: str
    symbol: str
    total_shares: Decimal
    average_purchase_price: Decimal
    invested_amount: Decimal
    # Share of *this account's* total invested capital that is tied up
    # in this security, expressed as a percentage in the range [0, 100].
    # We compute it per-account (not across the family) because a per-
    # account allocation view is what an investor actually rebalances
    # against - the family-level equivalent lives in the combined
    # portfolio report.
    portfolio_percentage: Decimal
    remaining_lots: int


def build_current_holdings(
    open_lots: Iterable[OpenLot],
    cost_adjustments: Optional[Mapping[_PositionKey, Decimal]] = None,
) -> list[HoldingRow]:
    """Aggregate open lots into one row per (account, ISIN).

    Lots from the same account+ISIN may have different cost-per-share
    values (different buy dates / prices) so we compute a *weighted*
    average:

        avg_price = (sum(remaining_shares_i * cost_per_share_i)
                     + cost_adjustment) / sum(remaining_shares_i)

    The optional `cost_adjustments` map lets the tax-lot engine inject
    per-position corrections that cannot be expressed as per-lot cost
    bases (currently used for security transfers - see
    :class:`app.services.tax_lot_engine.TaxLotEngine` for the derivation).

    The portfolio percentage is then:

        pct = invested_amount / sum(invested_amount for that account) * 100

    `safe_divide` guards against zero-denominator edge cases (e.g. a
    fully-consumed lot that somehow lingered, or an account that has
    nothing invested yet).
    """

    adjustments: Mapping[_PositionKey, Decimal] = cost_adjustments or {}

    # First pass: bucket the lots so subsequent passes work in O(N).
    buckets: dict[_PositionKey, list[OpenLot]] = defaultdict(list)
    for lot in open_lots:
        buckets[(lot.account_name, lot.isin)].append(lot)

    # Second pass: per-account total invested capital. Done up-front so
    # the row-building pass can compute each row's percentage in one go.
    account_totals = _compute_account_totals(buckets, adjustments)

    # Third pass: build the rows. Each row knows its own slice of the
    # owning account's portfolio thanks to `account_totals`.
    rows = [
        _build_row(
            account_name,
            isin,
            lots,
            account_totals[account_name],
            adjustments.get((account_name, isin), ZERO),
        )
        for (account_name, isin), lots in buckets.items()
    ]

    # Stable sort: account first, then symbol (case-insensitive) so the
    # report reads naturally regardless of insertion order.
    rows.sort(key=lambda r: (r.account_name.lower(), r.symbol.lower(), r.isin))
    return rows


def apply_portfolio_isin_exclusions(
    rows: Sequence[HoldingRow],
    ignore_by_account_lower: Mapping[str, Iterable[str]],
) -> list[HoldingRow]:
    """Drop holdings matching configured (portfolio, ISIN) pairs and recompute %.

    Keys in ``ignore_by_account_lower`` are folder names in lower case; each
    iterable lists ISINs (any casing) to omit for that portfolio only. Used for
    reporting views where certain securities should not appear in current
    holdings or the combined family rollup.

    Rows excluded from an account no longer contribute to that account's
    total invested capital, so remaining rows get fresh ``portfolio_percentage``
    values that still sum to 100 within each account (when the account has
    positive invested capital).
    """

    if not ignore_by_account_lower:
        return list(rows)

    blocked: dict[str, frozenset[str]] = {
        account: frozenset(isin.upper() for isin in isins)
        for account, isins in ignore_by_account_lower.items()
    }

    def is_blocked(row: HoldingRow) -> bool:
        isins = blocked.get(row.account_name.lower())
        return bool(isins and row.isin.upper() in isins)

    kept = [r for r in rows if not is_blocked(r)]
    if len(kept) == len(rows):
        return list(rows)

    account_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in kept:
        account_totals[r.account_name] += r.invested_amount

    adjusted: list[HoldingRow] = []
    for r in kept:
        total = account_totals[r.account_name]
        pct = safe_divide(r.invested_amount * _HUNDRED, total)
        adjusted.append(
            HoldingRow(
                account_name=r.account_name,
                isin=r.isin,
                symbol=r.symbol,
                total_shares=r.total_shares,
                average_purchase_price=r.average_purchase_price,
                invested_amount=r.invested_amount,
                portfolio_percentage=pct,
                remaining_lots=r.remaining_lots,
            )
        )

    adjusted.sort(
        key=lambda row: (row.account_name.lower(), row.symbol.lower(), row.isin),
    )
    return adjusted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_account_totals(
    buckets: dict[_PositionKey, list[OpenLot]],
    adjustments: Mapping[_PositionKey, Decimal],
) -> dict[str, Decimal]:
    """Sum invested capital per account across every (account, ISIN)."""

    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for (account_name, isin), lots in buckets.items():
        natural = sum(
            (lot.remaining_cost_basis for lot in lots), start=ZERO
        )
        totals[account_name] += natural + adjustments.get(
            (account_name, isin), ZERO
        )
    return totals


def _build_row(
    account_name: str,
    isin: str,
    lots: list[OpenLot],
    account_total: Decimal,
    cost_adjustment: Decimal,
) -> HoldingRow:
    """Assemble one `HoldingRow` from the lots backing a single ISIN."""

    total_shares = sum((lot.remaining_shares for lot in lots), start=ZERO)
    invested_amount = sum(
        (lot.remaining_cost_basis for lot in lots), start=ZERO
    ) + cost_adjustment
    average_price = safe_divide(invested_amount, total_shares)

    # Multiply BEFORE dividing to preserve full Decimal precision; we
    # only quantize down to 2 decimal places when the value is rendered.
    portfolio_pct = safe_divide(invested_amount * _HUNDRED, account_total)

    # Use the most recent symbol we have for that ISIN. Symbols can
    # drift slightly over time (Reuters renames, etc.) so always
    # picking the freshest value yields the friendliest report.
    symbol = max(lots, key=lambda lot: lot.buy_date).symbol

    return HoldingRow(
        account_name=account_name,
        isin=isin,
        symbol=symbol,
        total_shares=total_shares,
        average_purchase_price=average_price,
        invested_amount=invested_amount,
        portfolio_percentage=portfolio_pct,
        remaining_lots=len(lots),
    )
