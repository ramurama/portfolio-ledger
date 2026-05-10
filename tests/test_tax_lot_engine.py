"""Tests for `app.services.tax_lot_engine`.

We focus on the partial-lot consumption path because it is the most
error-prone part of the tax-lot engine and the most important one to
get right for tax reporting.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import Transaction, TransactionType
from app.services.tax_lot_engine import TaxLotEngine


def _buy(account: str, isin: str, qty: str, total: str, when: datetime) -> Transaction:
    return Transaction(
        account_name=account,
        date=when,
        isin=isin,
        symbol=isin,
        transaction_type=TransactionType.BUY,
        quantity=Decimal(qty),
        price=Decimal(total) / Decimal(qty) * Decimal("-1"),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),  # negative for outflow
    )


def _sell(account: str, isin: str, qty: str, total: str, when: datetime) -> Transaction:
    return Transaction(
        account_name=account,
        date=when,
        isin=isin,
        symbol=isin,
        transaction_type=TransactionType.SELL,
        quantity=Decimal(qty),
        price=Decimal(total) / Decimal(qty),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),
    )


def _security_transfer(
    account: str,
    isin: str,
    qty: str,
    total: str,
    when: datetime,
) -> Transaction:
    return Transaction(
        account_name=account,
        date=when,
        isin=isin,
        symbol=isin,
        transaction_type=TransactionType.SECURITY_TRANSFER,
        quantity=Decimal(qty),
        price=abs(Decimal(total) / Decimal(qty)),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),
    )


def _corporate_action(
    account: str,
    isin: str,
    qty: str,
    when: datetime,
) -> Transaction:
    """Build a Corporate action row mirroring Scalable Capital's export.

    The broker reports `price=0` and `amount=0` on every Corporate
    action, so the engine sees a zero per-share cost.
    """

    return Transaction(
        account_name=account,
        date=when,
        isin=isin,
        symbol=isin,
        transaction_type=TransactionType.CORPORATE_ACTION,
        quantity=Decimal(qty),
        price=Decimal("0"),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal("0"),
    )


class TestTaxLotEngine:
    def test_simple_buy_then_full_sell(self) -> None:
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "10", "-1000", datetime(2024, 1, 1)),
            _sell("ramu", "US123", "10", "1500", datetime(2024, 6, 1)),
        ])

        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.shares_sold == Decimal("10")
        assert trade.acquisition_cost == Decimal("1000")
        assert trade.sale_proceeds == Decimal("1500")
        assert trade.realized_gain_loss == Decimal("500")
        assert result.open_lots == []

    def test_partial_lot_consumption(self) -> None:
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "10", "-1000", datetime(2024, 1, 1)),
            _sell("ramu", "US123", "4", "600", datetime(2024, 6, 1)),
        ])

        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.shares_sold == Decimal("4")
        assert trade.acquisition_cost == Decimal("400")
        assert trade.sale_proceeds == Decimal("600")
        assert trade.realized_gain_loss == Decimal("200")

        assert len(result.open_lots) == 1
        remaining = result.open_lots[0]
        assert remaining.remaining_shares == Decimal("6")
        assert remaining.cost_per_share == Decimal("100")

    def test_sell_consumes_multiple_lots_oldest_first(self) -> None:
        """One Sell should match the OLDEST tax lot first, then the next."""
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "5", "-500", datetime(2024, 1, 1)),   # $100/sh
            _buy("ramu", "US123", "5", "-750", datetime(2024, 2, 1)),   # $150/sh
            _sell("ramu", "US123", "8", "1600", datetime(2024, 6, 1)),  # $200/sh proceeds
        ])

        # 8 shares sold = full lot 1 (5) + 3 of lot 2 -> two RealizedTrades.
        assert len(result.realized_trades) == 2

        first, second = result.realized_trades
        assert first.shares_sold == Decimal("5")
        assert first.acquisition_cost == Decimal("500")
        assert first.sale_proceeds == Decimal("1000")
        assert first.realized_gain_loss == Decimal("500")

        assert second.shares_sold == Decimal("3")
        assert second.acquisition_cost == Decimal("450")
        assert second.sale_proceeds == Decimal("600")
        assert second.realized_gain_loss == Decimal("150")

        # Lot 1 is gone, lot 2 has 2 shares remaining.
        assert len(result.open_lots) == 1
        assert result.open_lots[0].remaining_shares == Decimal("2")

    def test_accounts_have_independent_queues(self) -> None:
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "5", "-500", datetime(2024, 1, 1)),
            _buy("rakshana", "US123", "5", "-1000", datetime(2024, 1, 2)),
            _sell("rakshana", "US123", "5", "1100", datetime(2024, 6, 1)),
        ])

        # The Sell on rakshana's account must NOT touch ramu's lot.
        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.account_name == "rakshana"
        assert trade.acquisition_cost == Decimal("1000")
        assert trade.realized_gain_loss == Decimal("100")

        # ramu's lot is still fully open.
        ramu_lots = [lot for lot in result.open_lots if lot.account_name == "ramu"]
        assert len(ramu_lots) == 1
        assert ramu_lots[0].remaining_shares == Decimal("5")

    def test_short_sale_does_not_crash(self) -> None:
        """Selling more than ever bought logs a warning and stops cleanly."""
        engine = TaxLotEngine()
        result = engine.process([
            _sell("ramu", "US123", "5", "500", datetime(2024, 6, 1)),
        ])
        assert result.realized_trades == []
        assert result.open_lots == []

    def test_security_transfer_in_opens_lot(self) -> None:
        engine = TaxLotEngine()
        result = engine.process([
            _security_transfer(
                "ramu", "US123", "5", "600", datetime(2024, 1, 1)
            ),
        ])

        assert result.realized_trades == []
        assert len(result.open_lots) == 1
        assert result.open_lots[0].remaining_shares == Decimal("5")
        assert result.open_lots[0].cost_per_share == Decimal("120")
        # Pure transfer-in needs no extra adjustment - the new lot's
        # cost basis already equals the broker amount.
        assert result.cost_adjustments == {}

    def test_security_transfer_out_records_cost_adjustment(self) -> None:
        """Transfer-out reduces invested capital by the broker amount.

        Lot cost basis is 10 * $100 = $1000. Transfer-out moves 4 shares
        at a $120 broker price ($480 total). The adjustment must absorb
        the gap between the per-lot pop ($400) and the broker amount
        ($480).
        """
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "10", "-1000", datetime(2024, 1, 1)),
            _security_transfer(
                "ramu", "US123", "-4", "-480", datetime(2024, 6, 1)
            ),
        ])

        assert result.realized_trades == []
        assert len(result.open_lots) == 1
        assert result.open_lots[0].remaining_shares == Decimal("6")
        # natural reduction = 4 * $100 = $400; desired = $480.
        # adjustment = $400 - $480 = -$80.
        assert result.cost_adjustments == {("ramu", "US123"): Decimal("-80")}

        # Sanity: invested capital after the transfer equals
        # original (1000) - transfer_out_amount (480) = 520.
        invested = (
            sum(
                lot.remaining_cost_basis for lot in result.open_lots
            )
            + result.cost_adjustments[("ramu", "US123")]
        )
        assert invested == Decimal("520")

    def test_security_transfer_wash_round_trip(self) -> None:
        """Out-then-in at near-equal prices should preserve invested capital.

        Mirrors the Sony Group "switch" pattern in the real exports.
        """
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "US123", "10", "-200", datetime(2024, 1, 1)),  # avg $20
            _security_transfer(
                "ramu", "US123", "-10", "-300", datetime(2024, 6, 1)
            ),  # transfer-out at $30/sh
            _security_transfer(
                "ramu", "US123", "10", "300", datetime(2024, 6, 2)
            ),  # transfer-in at $30/sh
        ])

        assert result.realized_trades == []
        # Only the transfer-in lot survives.
        assert len(result.open_lots) == 1
        assert result.open_lots[0].remaining_shares == Decimal("10")
        assert result.open_lots[0].cost_per_share == Decimal("30")

        # adjustment = 200 - 300 = -100.
        assert result.cost_adjustments == {("ramu", "US123"): Decimal("-100")}

        # Invested = 10*$30 lot + (-100) adjustment = $200 (the original
        # cost basis). The wash leaves invested unchanged.
        invested = (
            result.open_lots[0].remaining_cost_basis
            + result.cost_adjustments[("ramu", "US123")]
        )
        assert invested == Decimal("200")

    def test_corporate_action_inbound_is_zero_cost_acquisition(self) -> None:
        """A free-shares corporate action must add a lot at cost 0 so a
        later sell books the full proceeds as realized gain."""
        engine = TaxLotEngine()
        result = engine.process([
            _corporate_action("ramu", "US123", "5", datetime(2024, 1, 1)),
            _sell("ramu", "US123", "5", "150", datetime(2024, 6, 1)),
        ])

        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.shares_sold == Decimal("5")
        assert trade.acquisition_cost == Decimal("0")
        assert trade.sale_proceeds == Decimal("150")
        assert trade.realized_gain_loss == Decimal("150")
        assert result.open_lots == []

    def test_corporate_action_outbound_reduces_fifo_before_sell(self) -> None:
        """Negative corporate action consumes oldest lots first (e.g. ISIN
        exchange row on the old security)."""
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "PARENT", "10", "-200", datetime(2024, 1, 1)),
            _corporate_action("ramu", "PARENT", "-3", datetime(2024, 6, 1)),
            _sell("ramu", "PARENT", "7", "210", datetime(2024, 12, 1)),
        ])

        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.shares_sold == Decimal("7")
        assert trade.acquisition_cost == Decimal("140")
        assert trade.sale_proceeds == Decimal("210")
        assert trade.realized_gain_loss == Decimal("70")
        assert result.open_lots == []

    def test_corporate_action_isin_exchange_old_cleared_new_lot(self) -> None:
        """Broker replaces ISIN: deduction on old, zero-cost acquisition on new."""
        engine = TaxLotEngine()
        result = engine.process([
            _buy("ramu", "OLDISIN", "1", "-100", datetime(2024, 1, 1)),
            _corporate_action("ramu", "OLDISIN", "-1", datetime(2024, 6, 1)),
            _corporate_action("ramu", "NEWISIN", "1", datetime(2024, 6, 1)),
        ])

        assert result.realized_trades == []
        assert [lot.isin for lot in result.open_lots] == ["NEWISIN"]
        assert result.open_lots[0].remaining_shares == Decimal("1")
        assert result.open_lots[0].cost_per_share == Decimal("0")
