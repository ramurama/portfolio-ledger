"""Combined report schema when market prices are included."""

from __future__ import annotations

from decimal import Decimal

from app.reports import _schema as schema
from app.services.portfolio import CombinedHoldingRow


def test_combined_headers_and_rows_include_market_columns() -> None:
    row = CombinedHoldingRow(
        isin="ISIN_A",
        symbol="Alpha",
        shares_per_account={"ramu": Decimal("10")},
        combined_shares=Decimal("10"),
        combined_average_price=Decimal("5"),
        total_invested=Decimal("50"),
        family_percentage=Decimal("100"),
        current_price=Decimal("6"),
        market_value=Decimal("60"),
        unrealized_gain_loss=Decimal("10"),
    )

    headers = schema.combined_headers(["ramu"], include_market_prices=True)
    assert "Current Price" in headers
    assert "Market Value" in headers
    assert "Unrealized G/L" in headers

    body = schema.combined_rows(
        [row],
        ["ramu"],
        "EUR",
        money_symbols=False,
        include_market_prices=True,
    )
    assert len(body) == 1
    assert len(body[0]) == len(headers)
    invested_idx = headers.index("Total Invested")
    mkt_idx = headers.index("Market Value")
    assert invested_idx == mkt_idx - 1
    assert body[0][invested_idx] == "50.00"
    assert body[0][-1] == "100.00%"
