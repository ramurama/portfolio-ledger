"""Family-level market totals must reconcile with invested capital."""

from __future__ import annotations

from decimal import Decimal

from app.services.portfolio import (
    CombinedHoldingRow,
    combined_family_price_totals,
)


def _security(
    isin: str,
    invested: str,
    *,
    market_value: str | None = None,
) -> CombinedHoldingRow:
    mv = Decimal(market_value) if market_value is not None else None
    unrl = (mv - Decimal(invested)) if mv is not None else None
    return CombinedHoldingRow(
        isin=isin,
        symbol="Sym",
        shares_per_account={"ramu": Decimal("1")},
        combined_shares=Decimal("1"),
        combined_average_price=Decimal(invested),
        total_invested=Decimal(invested),
        family_percentage=Decimal("50"),
        market_value=mv,
        unrealized_gain_loss=unrl,
    )


def test_footer_totals_match_market_minus_invested() -> None:
    """Unquoted holdings count at cost so footer figures reconcile."""

    rows = [
        _security("ISIN_A", "1000", market_value="1100"),
        _security("ISIN_B", "500"),  # no live quote
        CombinedHoldingRow(
            isin="CASH",
            symbol="Cash",
            shares_per_account={"ramu": Decimal("200")},
            combined_shares=Decimal("0"),
            combined_average_price=Decimal("0"),
            total_invested=Decimal("200"),
            family_percentage=Decimal("10"),
            is_cash=True,
            market_value=Decimal("200"),
            unrealized_gain_loss=Decimal("0"),
        ),
    ]

    invested, market, unrealized = combined_family_price_totals(rows)

    assert invested == Decimal("1700")
    assert market == Decimal("1800")  # 1100 + 500 at cost + 200 cash
    assert unrealized == Decimal("100")
    assert market - invested == unrealized
