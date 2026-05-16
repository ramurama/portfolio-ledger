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

Money columns use ``format_money`` with symbols for PDF-style rows and
without symbols for CSV/Excel (``money_symbols=False``), so tabular
exports stay plain numeric text while PDFs keep human-readable ``€``.
"""

from __future__ import annotations

from typing import Iterable

from app.config import PRICE_QUANTIZE, SHARE_QUANTIZE
from app.models import RealizedTrade
from app.services.cost_basis import CostBasisRow
from app.services.holdings import HoldingRow
from app.services.portfolio import CombinedHoldingRow
from app.utils.decimal_utils import format_money, format_us_decimal
from app.utils.text import display_account_name, short_account_label


# ---------------------------------------------------------------------------
# Tax Lots realized-trades report
# ---------------------------------------------------------------------------
TAX_LOTS_HEADERS: list[str] = [
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
    # renderer drops the qualifier (see `tax_lots_pdf_headers`) because
    # it already surfaces the same disclaimer in the page-1 notes band.
    "Realized Gain/Loss (Pre-Tax)",
]


def tax_lots_pdf_headers() -> list[str]:
    """Header list to use when rendering Tax Lots into a PDF.

    Long parenthetical qualifiers like ``"(Pre-Tax)"`` clutter the
    bold table header and don't fit cleanly on a single line. The PDF
    already shows a "Note: Realized Gain/Loss is reported PRE-TAX..."
    band on the first page, so we strip the qualifier here. CSV and
    Excel keep the canonical `TAX_LOTS_HEADERS` because they have no
    equivalent notes mechanism.
    """

    return [h.replace(" (Pre-Tax)", "") for h in TAX_LOTS_HEADERS]


def sort_tax_lots_trades(trades: Iterable[RealizedTrade]) -> list[RealizedTrade]:
    """Return a list of trades sorted by (buy_date, sell_date).

    The sort is stable, so trades sharing the same (buy_date, sell_date)
    keep the order in which the tax-lot engine emitted them - which
    itself is deterministic because the engine consumes lots strictly
    in queue order (oldest first). We additionally tie-break on ISIN so
    reports are reproducible even across re-runs that use different lot
    allocation strategies.
    """

    return sorted(
        trades,
        key=lambda tr: (tr.buy_date, tr.sell_date, tr.isin),
    )


def tax_lots_rows(
    trades: Iterable[RealizedTrade],
    currency: str,
    *,
    money_symbols: bool = True,
) -> list[list[str]]:
    """Render the Tax Lots realized-trades table body in `currency`."""

    sym = money_symbols
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
                format_money(
                    tr.acquisition_cost, currency,
                    include_currency_symbol=sym,
                ),
                format_money(
                    tr.sale_proceeds, currency,
                    include_currency_symbol=sym,
                ),
                format_money(
                    tr.realized_gain_loss, currency,
                    include_currency_symbol=sym,
                ),
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
    "Allocation",
]


def holdings_rows(
    holdings: Iterable[HoldingRow],
    currency: str,
    *,
    money_symbols: bool = True,
) -> list[list[str]]:
    """Render the current-holdings table body in `currency`."""

    sym = money_symbols
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
                format_money(
                    h.average_purchase_price, currency, PRICE_QUANTIZE,
                    include_currency_symbol=sym,
                ),
                format_money(
                    h.invested_amount, currency,
                    include_currency_symbol=sym,
                ),
                pct_display,
            ]
        )
    return body


# ---------------------------------------------------------------------------
# Combined family portfolio report
# ---------------------------------------------------------------------------
def combined_headers(
    account_names: list[str],
    *,
    include_market_prices: bool = False,
) -> list[str]:
    """Header row depends on which accounts exist in the input."""

    base = ["ISIN", "Symbol"]
    per_account = [
        f"Shares ({display_account_name(name)})" for name in account_names
    ]
    tail = [
        "Combined Shares",
        "Combined Avg Price",
    ]
    if include_market_prices:
        tail.extend(
            [
                "Current Price",
                "Market Value",
                "Unrealized G/L",
            ]
        )
    tail.extend(
        [
            "Total Invested",
            # Family-level allocation: this ISIN's share of the family's
            # total invested capital. Mirrors the per-account "Allocation"
            # column in the holdings report.
            "Allocation",
        ]
    )
    return base + per_account + tail


def combined_pdf_headers(
    account_names: list[str],
    *,
    include_market_prices: bool = False,
) -> list[str]:
    """Short headers for the combined portfolio PDF (fits landscape A4)."""

    base = ["ISIN", "Sym"]
    per_account = [
        display_account_name(name) if name.lower() == "rakshana"
        else short_account_label(name)
        for name in account_names
    ]
    tail = ["Combined", "Avg"]
    if include_market_prices:
        tail.extend(["LTP", "Mkt. Value", "P/L"])
    tail.extend(["Invested", "Alloc."])
    return base + per_account + tail


def _combined_market_price_cells(
    row: CombinedHoldingRow,
    currency: str,
    *,
    money_symbols: bool,
    price_quantize: str = PRICE_QUANTIZE,
) -> list[str]:
    sym = money_symbols
    if row.current_price is None:
        return ["", "", ""]
    return [
        format_money(
            row.current_price, currency, price_quantize,
            include_currency_symbol=sym,
        ),
        format_money(
            row.market_value, currency,
            include_currency_symbol=sym,
        )
        if row.market_value is not None
        else "",
        format_money(
            row.unrealized_gain_loss, currency,
            include_currency_symbol=sym,
        )
        if row.unrealized_gain_loss is not None
        else "",
    ]


def combined_rows(
    rows: Iterable[CombinedHoldingRow],
    account_names: list[str],
    currency: str,
    *,
    money_symbols: bool = True,
    include_market_prices: bool = False,
    compact: bool = False,
) -> list[list[str]]:
    """Render the combined-portfolio table body in `currency`.

    `account_names` enforces a consistent column order across rows -
    even when an ISIN is held by only some of the accounts the table
    stays rectangular (empty cells render as ``""``).

    ``compact=True`` uses fewer decimals for PDF columns (shares/prices).
    """

    sym = money_symbols
    share_q = "0.01" if compact else SHARE_QUANTIZE
    price_q = "0.01" if compact else PRICE_QUANTIZE
    body: list[list[str]] = []
    for r in rows:
        record = [r.isin, r.symbol]
        if r.is_cash:
            for name in account_names:
                amt = r.shares_per_account.get(name)
                record.append(
                    format_money(
                        amt, currency,
                        include_currency_symbol=sym,
                    )
                    if amt is not None
                    else ""
                )
            family_pct_display = (
                format_us_decimal(r.family_percentage, "0.01", thousands=False)
                + "%"
            )
            tail_cells: list[str] = ["", ""]
            if include_market_prices:
                tail_cells.extend(
                    [
                        "",
                        format_money(
                            r.market_value, currency,
                            include_currency_symbol=sym,
                        )
                        if r.market_value is not None
                        else "",
                        "",
                    ]
                )
            tail_cells.extend(
                [
                    format_money(
                        r.total_invested, currency,
                        include_currency_symbol=sym,
                    ),
                    family_pct_display,
                ]
            )
            record.extend(tail_cells)
            body.append(record)
            continue

        for name in account_names:
            shares = r.shares_per_account.get(name)
            record.append(
                format_us_decimal(shares, share_q, thousands=not compact)
                if shares is not None else ""
            )

        # Percentage cell uses the same display style as the per-
        # account holdings report: trailing "%", no thousands grouping.
        family_pct_display = (
            format_us_decimal(r.family_percentage, "0.01", thousands=False)
            + "%"
        )
        record.append(
            format_us_decimal(
                r.combined_shares, share_q, thousands=not compact,
            ),
        )
        record.append(
            format_money(
                r.combined_average_price, currency, price_q,
                include_currency_symbol=sym,
            ),
        )
        if include_market_prices:
            record.extend(
                _combined_market_price_cells(
                    r, currency, money_symbols=sym, price_quantize=price_q,
                ),
            )
        record.append(
            format_money(
                r.total_invested, currency,
                include_currency_symbol=sym,
            ),
        )
        record.append(family_pct_display)
        body.append(record)
    return body


# ---------------------------------------------------------------------------
# Cost-basis transfer report (one row per still-open tax lot)
# ---------------------------------------------------------------------------
COST_BASIS_HEADERS: list[str] = [
    "Account",
    "ISIN",
    "Symbol",
    "Acquisition Date",
    "Quantity",
    # The cost-basis report is the only place we explicitly call out a
    # per-LOT price; "Cost per Share" is the canonical IBKR-form term.
    "Cost per Share",
    "Cost Basis",
]


def cost_basis_rows(
    rows: Iterable[CostBasisRow],
    currency: str,
    *,
    money_symbols: bool = True,
) -> list[list[str]]:
    """Render the cost-basis table body in `currency`.

    Quantity uses the standard 6-dp share quantize so fractional
    savings-plan units render exactly as the broker reports them. Cost
    per Share uses the 4-dp price quantize - that is the precision IBKR
    accepts on its intake form, and it survives a `quantity * price`
    multiplication without losing significant figures.
    """

    sym = money_symbols
    body: list[list[str]] = []
    for r in rows:
        body.append(
            [
                display_account_name(r.account_name),
                r.isin,
                r.symbol,
                r.acquisition_date.strftime("%Y-%m-%d"),
                format_us_decimal(r.quantity, SHARE_QUANTIZE, thousands=True),
                format_money(
                    r.cost_per_share, currency, PRICE_QUANTIZE,
                    include_currency_symbol=sym,
                ),
                format_money(
                    r.cost_basis, currency,
                    include_currency_symbol=sym,
                ),
            ]
        )
    return body
