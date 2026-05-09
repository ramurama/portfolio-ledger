"""Tests for the Scalable Capital CSV parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.models import TransactionType
from app.parsers import ScalableCapitalParser


CSV_HEADER = (
    "date;time;status;reference;description;assetType;type;isin;"
    "shares;price;amount;fee;tax;currency\n"
)


@pytest.fixture()
def csv_path(tmp_path: Path) -> Path:
    """Return a path to a temp CSV with a few representative rows."""

    rows = [
        # Buy with non-zero fee
        '2024-01-15;10:00:00;Executed;"R1";"ServiceNow";Security;Buy;'
        'US81762P1021;10;77,75;-778,49;0,99;0,00;EUR\n',
        # Sell with German thousands in shares (1.200 = 1200)
        '2024-02-20;14:30:00;Executed;"R2";"iShares Treasury";Security;Sell;'
        'IE00BGR7L912;1.200;4,2203;5.064,36;0,00;-22,19;EUR\n',
        # Savings plan (fractional shares)
        '2024-03-01;11:00:00;Executed;"R3";"Xtrackers Nasdaq";Security;Savings plan;'
        'IE00BMFKG444;4,635638;53,93;-249,99995734;0,00;0,00;EUR\n',
        # Distribution with withheld tax (synthetic Tax tx should follow)
        '2024-04-01;02:00:00;Executed;"R4";"Sanofi";Cash;Distribution;'
        'FR0000120578;;;14,79;0,00;3,05;EUR\n',
        # Cancelled order - must be ignored
        '2024-05-01;10:00:00;Cancelled;"R5";"Apple";Security;Buy;'
        'US0378331005;5;200,00;-1000,00;0,00;0,00;EUR\n',
        # Cash Transfer - unsupported type, must be ignored
        '2024-05-02;02:00:00;Executed;"R6";"Internal Transfer";Cash;Cash Transfer In;'
        ';;;500,00;0,00;;EUR\n',
    ]
    path = tmp_path / "scalable.csv"
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return path


class TestScalableCapitalParser:
    def test_can_parse_recognises_header(self, csv_path: Path) -> None:
        assert ScalableCapitalParser.can_parse(csv_path)

    def test_can_parse_rejects_other_files(self, tmp_path: Path) -> None:
        other = tmp_path / "other.csv"
        other.write_text("foo;bar;baz\n1;2;3\n")
        assert not ScalableCapitalParser.can_parse(other)

    def test_parses_supported_rows(self, csv_path: Path) -> None:
        parser = ScalableCapitalParser()
        txs = list(parser.parse(csv_path, "ramu"))

        # Buy + Sell (with synthetic Tax) + Savings plan + Distribution
        # (with synthetic Tax) = 6 transactions. Cancelled and Cash
        # Transfer rows are dropped.
        types = [tx.transaction_type for tx in txs]
        assert types.count(TransactionType.BUY) == 1
        assert types.count(TransactionType.SELL) == 1
        assert types.count(TransactionType.SAVINGS_PLAN) == 1
        assert types.count(TransactionType.DISTRIBUTION) == 1
        assert types.count(TransactionType.TAX) == 2

    def test_decimal_parsing_uses_german_format(self, csv_path: Path) -> None:
        txs = list(ScalableCapitalParser().parse(csv_path, "ramu"))
        sell = next(tx for tx in txs if tx.transaction_type == TransactionType.SELL)
        # 1.200 must mean 1200 - this is the most important assertion.
        assert sell.quantity == Decimal("1200")
        assert sell.total_amount == Decimal("5064.36")

    def test_synthetic_tax_carries_sign(self, csv_path: Path) -> None:
        txs = list(ScalableCapitalParser().parse(csv_path, "ramu"))
        taxes = [tx for tx in txs if tx.transaction_type == TransactionType.TAX]
        # Sell row had tax = -22,19 (refund); Distribution row had tax = 3,05.
        amounts = sorted(tx.total_amount for tx in taxes)
        assert amounts == [Decimal("-22.19"), Decimal("3.05")]

    def test_account_name_is_propagated(self, csv_path: Path) -> None:
        txs = list(ScalableCapitalParser().parse(csv_path, "ramu"))
        assert all(tx.account_name == "ramu" for tx in txs)
