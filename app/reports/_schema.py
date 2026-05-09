"""Tabular schema definitions shared by every renderer.

We define the column ordering and headers in *one* place so that the
CSV, Excel and PDF outputs are guaranteed to agree. Renderers consume
the helpers below to produce a `list[list[str]]` (header + body) that
they can hand to their respective backend with minimal further work.

Each `*_rows` function returns:

    * `headers` - list of column titles
    * `body`    - list of row values (already formatted as US-locale
                  strings via `format_us_decimal`)

Keeping the formatting at this layer (rather than inside each renderer)
guarantees identical numbers across all three output files.
"""

from __future__ import annotations

from typing import Iterable

from app.config import MONEY_QUANTIZE, PRICE_QUANTIZE, SHARE_QUANTIZE
from app.models import RealizedTrade
from app.services.holdings import HoldingRow
from app.services.portfolio import CombinedHoldingRow
from app.utils.decimal_utils import format_us_decimal


# ---------------------------------------------------------------------------
# FIFO realized-trades report
# ---------------------------------------------------------------------------
FIFO_HEADERS: list[str] = [
    "Account",
    "ISIN",
    "Symbol",
    "Buy Date",
    "Sell Date",
    "Shares Sold",
    "Acquisition Cost",
    "Sale Proceeds",
    "Realized Gain/Loss",
]


def fifo_rows(trades: Iterable[RealizedTrade]) -> list[list[str]]:
    """Render the FIFO realized-trades table body."""

    body: list[list[str]] = []
    for tr in trades:
        body.append(
            [
                tr.account_name,
                tr.isin,
                tr.symbol,
                tr.buy_date.strftime("%Y-%m-%d"),
                tr.sell_date.strftime("%Y-%m-%d"),
                format_us_decimal(tr.shares_sold, SHARE_QUANTIZE, thousands=True),
                format_us_decimal(tr.acquisition_cost, MONEY_QUANTIZE, thousands=True),
                format_us_decimal(tr.sale_proceeds, MONEY_QUANTIZE, thousands=True),
                format_us_decimal(tr.realized_gain_loss, MONEY_QUANTIZE, thousands=True),
            ]
        )
    return body


# ---------------------------------------------------------------------------
# Current holdings report
# ---------------------------------------------------------------------------
HOLDINGS_HEADERS: list[str] = [
    "Account",
    "ISIN",
    "Symbol",
    "Total Shares",
    "Average Purchase Price",
    "Invested Amount",
    "Remaining Lots",
]


def holdings_rows(holdings: Iterable[HoldingRow]) -> list[list[str]]:
    """Render the current-holdings table body."""

    body: list[list[str]] = []
    for h in holdings:
        body.append(
            [
                h.account_name,
                h.isin,
                h.symbol,
                format_us_decimal(h.total_shares, SHARE_QUANTIZE, thousands=True),
                format_us_decimal(h.average_purchase_price, PRICE_QUANTIZE, thousands=True),
                format_us_decimal(h.invested_amount, MONEY_QUANTIZE, thousands=True),
                str(h.remaining_lots),
            ]
        )
    return body


# ---------------------------------------------------------------------------
# Combined family portfolio report
# ---------------------------------------------------------------------------
def combined_headers(account_names: list[str]) -> list[str]:
    """Header row depends on which accounts exist in the input."""

    base = ["ISIN", "Symbol"]
    per_account = [f"Shares ({name})" for name in account_names]
    tail = [
        "Combined Shares",
        "Combined Avg Price",
        "Total Invested",
    ]
    return base + per_account + tail


def combined_rows(
    rows: Iterable[CombinedHoldingRow],
    account_names: list[str],
) -> list[list[str]]:
    """Render the combined-portfolio table body.

    `account_names` enforces a consistent column order across rows -
    even when an ISIN is held by only some of the accounts the table
    stays rectangular (empty cells render as ``""``).
    """

    body: list[list[str]] = []
    for r in rows:
        record = [r.isin, r.symbol]
        for name in account_names:
            shares = r.shares_per_account.get(name)
            record.append(
                format_us_decimal(shares, SHARE_QUANTIZE, thousands=True)
                if shares is not None else ""
            )
        record.extend(
            [
                format_us_decimal(r.combined_shares, SHARE_QUANTIZE, thousands=True),
                format_us_decimal(r.combined_average_price, PRICE_QUANTIZE, thousands=True),
                format_us_decimal(r.total_invested, MONEY_QUANTIZE, thousands=True),
            ]
        )
        body.append(record)
    return body
