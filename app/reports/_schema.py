"""Tabular schema definitions shared by every renderer.

We define the column ordering and headers in *one* place so that the
CSV, Excel and PDF outputs are guaranteed to agree. Renderers consume
the helpers below to produce a `list[list[str]]` (header + body) that
they can hand to their respective backend with minimal further work.

Each `*_rows` function returns:

    * `headers` - list of column titles
    * `body`    - list of row values (already formatted as US-locale
                  strings via `format_us_decimal`)

Currency formatting also lives at this layer: the renderer never has
to know which columns hold money - it just hands the strings through.
Keeping the formatting at this layer (rather than inside each renderer)
guarantees identical numbers across all three output files.
"""

from __future__ import annotations

from typing import Iterable

from app.config import PRICE_QUANTIZE, SHARE_QUANTIZE
from app.models import RealizedTrade
from app.services.holdings import HoldingRow
from app.services.portfolio import CombinedHoldingRow
from app.utils.decimal_utils import format_money, format_us_decimal
from app.utils.text import display_account_name


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
    # The "(Pre-Tax)" qualifier is part of the canonical header so
    # the information travels with the data into CSV / Excel exports,
    # where there is no separate notes mechanism to convey it. The PDF
    # renderer drops the qualifier (see `fifo_pdf_headers`) because it
    # already surfaces the same disclaimer in the page-1 notes band.
    "Realized Gain/Loss (Pre-Tax)",
]


def fifo_pdf_headers() -> list[str]:
    """Header list to use when rendering FIFO into a PDF.

    Long parenthetical qualifiers like ``"(Pre-Tax)"`` clutter the
    bold table header and don't fit cleanly on a single line. The PDF
    already shows a "Note: Realized Gain/Loss is reported PRE-TAX..."
    band on the first page, so we strip the qualifier here. CSV and
    Excel keep the canonical `FIFO_HEADERS` because they have no
    equivalent notes mechanism.
    """

    return [h.replace(" (Pre-Tax)", "") for h in FIFO_HEADERS]


def sort_fifo_trades(trades: Iterable[RealizedTrade]) -> list[RealizedTrade]:
    """Return a list of trades sorted by (buy_date, sell_date).

    The sort is stable, so trades sharing the same (buy_date, sell_date)
    keep the order in which the FIFO engine emitted them - which itself
    is deterministic because the engine consumes lots strictly in queue
    order. We additionally tie-break on ISIN so reports are reproducible
    even across re-runs that use different lot allocation strategies.
    """

    return sorted(
        trades,
        key=lambda tr: (tr.buy_date, tr.sell_date, tr.isin),
    )


def fifo_rows(
    trades: Iterable[RealizedTrade],
    currency: str,
) -> list[list[str]]:
    """Render the FIFO realized-trades table body in `currency`."""

    body: list[list[str]] = []
    for tr in trades:
        body.append(
            [
                display_account_name(tr.account_name),
                tr.isin,
                tr.symbol,
                tr.buy_date.strftime("%Y-%m-%d"),
                tr.sell_date.strftime("%Y-%m-%d"),
                format_us_decimal(tr.shares_sold, SHARE_QUANTIZE, thousands=True),
                format_money(tr.acquisition_cost, currency),
                format_money(tr.sale_proceeds, currency),
                format_money(tr.realized_gain_loss, currency),
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
    "% of Portfolio",
]


def holdings_rows(
    holdings: Iterable[HoldingRow],
    currency: str,
) -> list[list[str]]:
    """Render the current-holdings table body in `currency`."""

    body: list[list[str]] = []
    for h in holdings:
        # Percentages typically read better with a trailing "%" symbol
        # and never need a thousand-separator (the value is in [0, 100]).
        pct_display = (
            format_us_decimal(h.portfolio_percentage, "0.01", thousands=False)
            + "%"
        )
        body.append(
            [
                display_account_name(h.account_name),
                h.isin,
                h.symbol,
                format_us_decimal(h.total_shares, SHARE_QUANTIZE, thousands=True),
                format_money(h.average_purchase_price, currency, PRICE_QUANTIZE),
                format_money(h.invested_amount, currency),
                pct_display,
            ]
        )
    return body


# ---------------------------------------------------------------------------
# Combined family portfolio report
# ---------------------------------------------------------------------------
def combined_headers(account_names: list[str]) -> list[str]:
    """Header row depends on which accounts exist in the input."""

    base = ["ISIN", "Symbol"]
    per_account = [
        f"Shares ({display_account_name(name)})" for name in account_names
    ]
    tail = [
        "Combined Shares",
        "Combined Avg Price",
        "Total Invested",
        # Family-level allocation: this ISIN's share of the family's
        # total invested capital. Mirrors the per-account "% of
        # Portfolio" column in the holdings report.
        "% of Family Portfolio",
    ]
    return base + per_account + tail


def combined_rows(
    rows: Iterable[CombinedHoldingRow],
    account_names: list[str],
    currency: str,
) -> list[list[str]]:
    """Render the combined-portfolio table body in `currency`.

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

        # Percentage cell uses the same display style as the per-
        # account holdings report: trailing "%", no thousands grouping.
        family_pct_display = (
            format_us_decimal(r.family_percentage, "0.01", thousands=False)
            + "%"
        )
        record.extend(
            [
                format_us_decimal(r.combined_shares, SHARE_QUANTIZE, thousands=True),
                format_money(r.combined_average_price, currency, PRICE_QUANTIZE),
                format_money(r.total_invested, currency),
                family_pct_display,
            ]
        )
        body.append(record)
    return body
