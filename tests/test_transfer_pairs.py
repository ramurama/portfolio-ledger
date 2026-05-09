"""Tests for `app.services.transfer_pairs.collapse_switch_pairs`.

Switches are the broker's internal sub-account moves: an outbound
Security transfer leg (qty < 0) followed by an inbound leg (qty > 0)
whose `reference` carries the ``SWITCH-`` marker. Both legs preserve
the original tax lots and must be elided before the tax-lot engine
sees them.

Real-world scenarios covered:

    * Single ISIN switch pair -> both legs dropped.
    * Multiple ISIN switch pairs in the same export -> each pair is
      paired independently and dropped.
    * Inbound `SWITCH-` leg with no plausible outbound -> kept (we
      log a warning, but do not fabricate a partner).
    * Outbound Security transfer with no matching inbound -> kept
      (real broker-to-broker withdrawal).
    * Inbound Security transfer without `SWITCH-` prefix -> kept
      (real broker-to-broker deposit).
    * Engine-level effect: a Buy + SWITCH-out + SWITCH-in + Sell
      sequence yields the same realized gain as the Buy + Sell alone.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import Transaction, TransactionType
from app.services.tax_lot_engine import TaxLotEngine
from app.services.transfer_pairs import collapse_switch_pairs


def _security_transfer(
    *,
    account: str,
    isin: str,
    qty: str,
    total: str,
    when: datetime,
    reference: str,
) -> Transaction:
    return Transaction(
        account_name=account,
        date=when,
        isin=isin,
        symbol=isin,
        transaction_type=TransactionType.SECURITY_TRANSFER,
        quantity=Decimal(qty),
        price=abs(Decimal(total) / Decimal(qty)) if Decimal(qty) != 0 else Decimal("0"),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),
        reference=reference,
    )


def _buy(
    *,
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
        transaction_type=TransactionType.BUY,
        quantity=Decimal(qty),
        price=abs(Decimal(total) / Decimal(qty)),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),
        reference=None,
    )


def _sell(
    *,
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
        transaction_type=TransactionType.SELL,
        quantity=Decimal(qty),
        price=Decimal(total) / Decimal(qty),
        fees=Decimal("0"),
        currency="EUR",
        total_amount=Decimal(total),
        reference=None,
    )


class TestCollapseSwitchPairs:
    def test_collapses_canonical_pair(self) -> None:
        out_leg = _security_transfer(
            account="ramu",
            isin="IE00B4ND3602",
            qty="-152.361",
            total="-10689.64776",
            when=datetime(2025, 12, 5, 1, 0, 0),
            reference="WWUM 00596749782",
        )
        in_leg = _security_transfer(
            account="ramu",
            isin="IE00B4ND3602",
            qty="152.361",
            total="10666.79361",
            when=datetime(2025, 12, 6, 1, 0, 0),
            reference="SWITCH-101-acct-IE00B4ND3602-WDP",
        )

        result = collapse_switch_pairs([out_leg, in_leg])

        assert result == []

    def test_does_not_collapse_unpaired_inbound_with_switch_prefix(self) -> None:
        """Defensive: if the export is missing the outbound leg, the
        inbound row is still admitted - we log a warning but do not
        fabricate a partner. This is the safer behaviour: the user
        will see an over-stated invested capital rather than silently
        losing a real share movement."""

        in_leg = _security_transfer(
            account="ramu",
            isin="IE00B4ND3602",
            qty="10",
            total="700",
            when=datetime(2025, 12, 6, 1, 0, 0),
            reference="SWITCH-101-acct-IE00B4ND3602-WDP",
        )

        assert collapse_switch_pairs([in_leg]) == [in_leg]

    def test_keeps_inbound_without_switch_prefix(self) -> None:
        """Real broker-to-broker incoming transfers (e.g. moving shares
        in from another bank) carry a `WWUM ...` reference, not
        `SWITCH-`. They MUST flow through the tax-lot engine so the
        new lot is recorded."""

        in_leg = _security_transfer(
            account="ramu",
            isin="DE000CBK1001",
            qty="2",
            total="24.61",
            when=datetime(2024, 8, 13, 1, 0, 0),
            reference="WWUM 00352270182",
        )

        assert collapse_switch_pairs([in_leg]) == [in_leg]

    def test_keeps_outbound_without_switch_partner(self) -> None:
        """An outbound leg with no later SWITCH inbound is a real
        broker-to-broker withdrawal (e.g. transfer out to IBKR). It
        must remain in the stream so the engine can consume the lots
        and adjust invested capital correctly."""

        out_leg = _security_transfer(
            account="ramu",
            isin="IE00B4ND3602",
            qty="-5",
            total="-350",
            when=datetime(2025, 12, 5, 1, 0, 0),
            reference="WWUM 00596749782",
        )

        assert collapse_switch_pairs([out_leg]) == [out_leg]

    def test_collapses_multiple_isin_pairs_in_one_pass(self) -> None:
        """Each ISIN's switch pair is matched independently. Order in
        the input is irrelevant - the function indexes outbounds by
        (account, isin, |qty|) and pairs them up by proximity."""

        rows = [
            _security_transfer(
                account="ramu",
                isin="IE00B4ND3602",
                qty="-216.092",
                total="-15161.01",
                when=datetime(2025, 12, 5, 1, 0, 0),
                reference="WWUM 1",
            ),
            _security_transfer(
                account="ramu",
                isin="JP3164720009",
                qty="-1",
                total="-11.62",
                when=datetime(2025, 12, 5, 1, 0, 0),
                reference="WWUM 2",
            ),
            _security_transfer(
                account="ramu",
                isin="JP3164720009",
                qty="1",
                total="11.32",
                when=datetime(2025, 12, 6, 1, 0, 0),
                reference="SWITCH-101-acct-JP3164720009-WDP",
            ),
            _security_transfer(
                account="ramu",
                isin="IE00B4ND3602",
                qty="216.092",
                total="15128.60",
                when=datetime(2025, 12, 6, 1, 0, 0),
                reference="SWITCH-101-acct-IE00B4ND3602-WDP",
            ),
        ]

        assert collapse_switch_pairs(rows) == []

    def test_collapse_preserves_realized_gain_after_pair(self) -> None:
        """End-to-end: a Buy + SWITCH-out + SWITCH-in + Sell sequence
        must produce the same realized gain as Buy + Sell alone, once
        the switch legs are collapsed in ingestion."""

        rows = [
            _buy(
                account="ramu",
                isin="IE00B4ND3602",
                qty="10",
                total="-200",
                when=datetime(2024, 1, 1),
            ),
            _security_transfer(
                account="ramu",
                isin="IE00B4ND3602",
                qty="-10",
                total="-300",
                when=datetime(2025, 12, 5, 1, 0, 0),
                reference="WWUM",
            ),
            _security_transfer(
                account="ramu",
                isin="IE00B4ND3602",
                qty="10",
                total="300",
                when=datetime(2025, 12, 6, 1, 0, 0),
                reference="SWITCH-101-acct-IE00B4ND3602-WDP",
            ),
            _sell(
                account="ramu",
                isin="IE00B4ND3602",
                qty="10",
                total="500",
                when=datetime(2026, 3, 1),
            ),
        ]

        collapsed = collapse_switch_pairs(rows)
        result = TaxLotEngine().process(collapsed)

        # Cost basis preserved at €20/share (the original buy), not
        # €30/share (the inbound SWITCH price). Realized gain is then
        # 500 - 10*20 = 300.
        assert len(result.realized_trades) == 1
        trade = result.realized_trades[0]
        assert trade.acquisition_cost == Decimal("200")
        assert trade.sale_proceeds == Decimal("500")
        assert trade.realized_gain_loss == Decimal("300")
        # Switch legs do not leak a cost adjustment either.
        assert result.cost_adjustments == {}
