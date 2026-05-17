"""Cost-basis report column schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.reports import _schema as schema
from app.services.cost_basis import CostBasisRow


def test_cost_basis_rows_include_gettex_exchange_column() -> None:
    row = CostBasisRow(
        account_name="ramu",
        isin="DE0007164600",
        symbol="SAP",
        acquisition_date=datetime(2024, 1, 15),
        quantity=Decimal("10"),
        cost_per_share=Decimal("100"),
        cost_basis=Decimal("1000"),
    )

    headers = schema.COST_BASIS_HEADERS
    body = schema.cost_basis_rows([row], "EUR", money_symbols=False)

    assert headers.index("Exchange") == headers.index("Symbol") + 1
    assert body[0][headers.index("Exchange")] == "GETTEX"
