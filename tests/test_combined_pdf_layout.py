"""Combined portfolio PDF must fit landscape A4 width."""

from __future__ import annotations

from app.reports import _schema as schema
from app.reports.report_manager import _PAGE_BUDGET_MM, _combined_col_widths_mm


def test_combined_pdf_column_widths_fill_page() -> None:
    for accounts in (1, 2, 3, 4):
        for prices in (False, True):
            widths = _combined_col_widths_mm(
                accounts,
                include_market_prices=prices,
            )
            assert abs(sum(widths) - _PAGE_BUDGET_MM) < 0.05
            assert min(widths) >= 10.0


def test_combined_pdf_headers_are_short() -> None:
    headers = schema.combined_pdf_headers(
        ["rakshana", "ramu"],
        include_market_prices=True,
    )
    assert headers == [
        "ISIN",
        "Sym",
        "Rakshana",
        "Ramu",
        "Combined",
        "Avg",
        "LTP",
        "Mkt. Value",
        "P/L",
        "Invested",
        "Alloc.",
    ]
