"""Tax-lot accounting engine.

Algorithm
---------
For every (account, ISIN) we maintain an ordered queue of `OpenLot`
objects representing the still-held tax lots. Buys / Savings plans and
inbound security transfers push new lots onto the *back* of the queue;
Sells consume shares from the *front* (oldest tax lot first - the
classic FIFO matching policy used by most German brokers and
acceptable to the Finanzamt for `Abgeltungsteuer` calculations).
Outbound security transfers also consume shares, but without creating
realized-trade rows because no sale happened. Lots that hit zero
remaining shares are popped off entirely.

Security-transfer cost adjustments
----------------------------------
Sells reduce invested capital by the cost basis of the consumed tax
lots, which is correct for taxable disposals. Security transfers are
*not* sales though - they describe the broker repricing the same
shares, and the user-facing rule for invested capital is:

    transfer-out  -> invested -= abs(transfer_out_amount)
    transfer-in   -> invested += abs(transfer_in_amount)

Transfer-ins fall out of `_handle_acquisition` automatically because
the new lot's cost basis equals the transfer-in amount. Transfer-outs
need explicit help: popping a lot only reduces invested by that lot's
own cost basis, which is generally NOT the transfer-out amount. We
absorb the difference into a per-(account, ISIN) `cost_adjustments`
ledger that the holdings calculator adds to the natural per-lot total.

Crucially, we use `collections.deque`:

    * `append`     - O(1) push
    * `popleft`    - O(1) pop
    * Index access via `[0]` is O(1) too, which is all we need for the
      "consume from the front" pattern.

Cost basis and proceeds treatment (Scalable Capital DE)
-------------------------------------------------------
For Scalable Capital exports, the CSV `amount` column equals
`shares * price` exactly on every trade row - i.e. it is the **gross**
amount, *excluding* the broker's `fee` and `tax` columns:

    Buy:  amount = -(shares * price)        (fee reported separately)
    Sell: amount =  (shares * price)        (fee + tax reported separately)

The engine therefore treats:

    cost_per_share     = abs(buy_amount)  / buy_quantity
    proceeds_per_share = abs(sell_amount) / sell_quantity

Both expressions are the *gross* per-share value. Withheld tax on the
Sell row is captured by the parser as its own `TransactionType.TAX`
event and is NOT subtracted from sale_proceeds. This means the
`realized_gain_loss` reported by the engine is **PRE-TAX** (and, for
Scalable Capital, also pre-fee since fees are nearly always 0). The
report writers surface this clearly to the operator via the column
header (`Realized Gain/Loss (Pre-Tax)`) and a footnote on the PDF.

If a future broker reports `amount` net of fees / tax, this engine
should still work correctly - the math is per-share and the gross/net
distinction collapses for that broker. The only thing that would need
to change is the column header / disclaimer.

Partial lot consumption
-----------------------
A single Sell transaction can match multiple buy lots. We yield one
`RealizedTrade` per matched buy fragment so the Tax Lots report shows
the exact buy/sell mapping that was actually used.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque, Iterable, Optional

from app.models import OpenLot, RealizedTrade, Transaction, TransactionType
from app.utils.decimal_utils import ZERO, safe_divide
from app.utils.logging import get_logger

logger = get_logger(__name__)


# Composite key: (account_name, ISIN). One tax-lot queue per pair.
_QueueKey = tuple[str, str]


@dataclass
class TaxLotResult:
    """Aggregated outcome of running the tax-lot engine over a stream."""

    realized_trades: list[RealizedTrade] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)
    # Per-(account, ISIN) cost-basis adjustment captured during security
    # transfers. The holdings calculator adds these to the natural per-lot
    # invested total so transfer-outs reduce invested capital by the
    # broker-reported transfer amount rather than the cost basis of the
    # consumed tax lots. See module docstring for the derivation.
    cost_adjustments: dict[_QueueKey, Decimal] = field(default_factory=dict)

    @property
    def total_realized_gain(self) -> Decimal:
        """Net P&L across every realized trade in the result."""
        return sum(
            (trade.realized_gain_loss for trade in self.realized_trades),
            start=ZERO,
        )


class TaxLotEngine:
    """Stateful tax-lot calculator.

    Designed for one-shot use: feed it a chronological iterable of
    `Transaction`s via :meth:`process` and read the aggregated result
    afterwards. A fresh engine should be used per CLI invocation.
    """

    def __init__(self) -> None:
        # `defaultdict(deque)` saves us from constantly checking key
        # presence when a brand-new (account, isin) pair shows up.
        self._queues: dict[_QueueKey, Deque[OpenLot]] = defaultdict(deque)
        self._realized: list[RealizedTrade] = []
        # Captures the per-position cost adjustment described in the
        # module docstring. Keyed identically to `_queues` so the
        # holdings layer can join on the same composite key.
        self._adjustments: dict[_QueueKey, Decimal] = defaultdict(lambda: ZERO)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process(self, transactions: Iterable[Transaction]) -> TaxLotResult:
        """Apply every transaction in order and return the result."""

        for tx in transactions:
            self._dispatch(tx)

        return TaxLotResult(
            realized_trades=list(self._realized),
            open_lots=self._collect_open_lots(),
            cost_adjustments={
                key: value
                for key, value in self._adjustments.items()
                if value != ZERO
            },
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch(self, tx: Transaction) -> None:
        """Route a single transaction to the appropriate handler."""

        if tx.transaction_type.is_acquisition:
            self._handle_acquisition(tx)
        elif tx.transaction_type.is_disposal:
            self._handle_disposal(tx)
        elif tx.transaction_type.is_security_transfer:
            self._handle_security_transfer(tx)
        elif tx.transaction_type.is_corporate_action:
            self._handle_corporate_action(tx)
        else:
            # Distribution / Tax events do not move the tax-lot queue.
            # They are still useful to keep around at the parser level
            # for other reports (dividend totals, withheld tax) so we
            # just ignore them silently here.
            return

    # ------------------------------------------------------------------
    # Acquisition (Buy / Savings plan / inbound security transfer)
    # ------------------------------------------------------------------
    def _handle_acquisition(self, tx: Transaction) -> None:
        """Append a new tax lot to the back of the queue."""

        if tx.isin is None or tx.quantity is None or tx.quantity <= 0:
            logger.warning(
                "Skipping acquisition with missing isin/quantity at %s: %s",
                tx.date, tx,
            )
            return

        # Per-share **gross** cost basis (= price for Scalable Capital).
        # We use abs(total_amount) so the broker's sign convention
        # (negative for cash outflow) does not flip cost into credit.
        # Buy-side fees are reported separately by the parser and are
        # intentionally NOT folded into cost basis - this keeps the
        # tax-lot math symmetric with the Sell side and easy to audit.
        cost_per_share = safe_divide(abs(tx.total_amount), tx.quantity)

        lot = OpenLot(
            account_name=tx.account_name,
            isin=tx.isin,
            symbol=tx.symbol or "",
            buy_date=tx.date,
            original_shares=tx.quantity,
            remaining_shares=tx.quantity,
            cost_per_share=cost_per_share,
        )
        self._queue_for(tx).append(lot)

    # ------------------------------------------------------------------
    # Disposal (Sell)
    # ------------------------------------------------------------------
    def _handle_disposal(self, tx: Transaction) -> None:
        """Match a Sell against the oldest open tax lots.

        Emits one `RealizedTrade` per consumed lot fragment so partial
        consumption is visible in the Tax Lots report.
        """

        if tx.isin is None or tx.quantity is None or tx.quantity <= 0:
            logger.warning(
                "Skipping disposal with missing isin/quantity at %s: %s",
                tx.date, tx,
            )
            return

        queue = self._queue_for(tx)
        remaining_to_sell: Decimal = tx.quantity

        # Per-share **gross** proceeds for THIS sell. For Scalable
        # Capital `total_amount` equals `shares * price`, i.e. it is
        # already the gross figure before withholding tax / fees.
        # Tax events for this sell are emitted separately by the parser
        # so they remain visible without polluting the cost-basis math.
        proceeds_per_share = safe_divide(abs(tx.total_amount), tx.quantity)

        while remaining_to_sell > ZERO:
            lot = self._peek_or_log_short_sale(queue, tx, remaining_to_sell)
            if lot is None:
                # Short-sale: more shares sold than ever bought. This
                # can happen on Security Transfer-imported portfolios
                # where the original lots were never recorded. We log
                # and stop rather than fabricating cost basis.
                return

            # How much of this particular lot do we consume?
            consumed = min(lot.remaining_shares, remaining_to_sell)

            acquisition_cost = consumed * lot.cost_per_share
            sale_proceeds = consumed * proceeds_per_share

            self._realized.append(
                RealizedTrade(
                    account_name=tx.account_name,
                    isin=tx.isin,
                    symbol=tx.symbol or lot.symbol,
                    buy_date=lot.buy_date,
                    sell_date=tx.date,
                    shares_sold=consumed,
                    acquisition_cost=acquisition_cost,
                    sale_proceeds=sale_proceeds,
                )
            )

            lot.remaining_shares -= consumed
            remaining_to_sell -= consumed

            # Drop the lot once fully consumed. Using `popleft` keeps
            # the oldest-first ordering invariant intact.
            if lot.remaining_shares == ZERO:
                queue.popleft()

    # ------------------------------------------------------------------
    # Security transfers
    # ------------------------------------------------------------------
    def _handle_security_transfer(self, tx: Transaction) -> None:
        """Apply a security transfer without recording taxable proceeds."""

        if tx.quantity is None or tx.quantity == ZERO:
            logger.warning(
                "Skipping security transfer with missing/zero quantity at %s: %s",
                tx.date, tx,
            )
            return

        if tx.quantity > ZERO:
            self._handle_acquisition(tx)
        else:
            self._handle_transfer_out(tx)

    def _handle_transfer_out(self, tx: Transaction) -> None:
        """Consume open tax lots for an outbound transfer.

        Net effect on invested capital: ``-abs(tx.total_amount)``. Popping
        the lots only reduces invested by the lots' own cost basis, so
        for every consumed share we book the difference between the
        broker's transfer-out price and the lot's cost-per-share into
        the per-position `cost_adjustments` ledger. The holdings layer
        adds that ledger back into invested capital, so the displayed
        reduction matches the broker amount one-to-one.
        """

        if tx.isin is None or tx.quantity is None:
            logger.warning(
                "Skipping outbound transfer with missing isin/quantity at %s: %s",
                tx.date, tx,
            )
            return

        transfer_shares = abs(tx.quantity)
        transfer_amount = abs(tx.total_amount)
        # Per-share value the broker reports for this transfer-out. Using
        # `safe_divide` guards against the (legitimate) zero-amount case.
        transfer_price_per_share = safe_divide(transfer_amount, transfer_shares)

        queue = self._queue_for(tx)
        key = (tx.account_name, tx.isin)
        remaining_to_transfer = transfer_shares

        while remaining_to_transfer > ZERO:
            lot = self._peek_or_log_short_sale(queue, tx, remaining_to_transfer)
            if lot is None:
                return

            consumed = min(lot.remaining_shares, remaining_to_transfer)

            # Natural reduction = consumed * lot.cost_per_share (what the
            # pop would do on its own). Desired reduction = consumed *
            # transfer_price_per_share. The delta lives in the ledger.
            natural_reduction = consumed * lot.cost_per_share
            desired_reduction = consumed * transfer_price_per_share
            self._adjustments[key] += natural_reduction - desired_reduction

            lot.remaining_shares -= consumed
            remaining_to_transfer -= consumed

            if lot.remaining_shares == ZERO:
                queue.popleft()

    # ------------------------------------------------------------------
    # Corporate actions
    # ------------------------------------------------------------------
    def _handle_corporate_action(self, tx: Transaction) -> None:
        """Apply a broker-reported corporate action to the tax-lot queue.

        Modelled with deliberately simple semantics (see
        :class:`TransactionType.is_corporate_action` for the rationale):

            * ``+qty`` rows are admitted as **zero cost basis**
              acquisitions. The broker reports ``price=0`` and
              ``amount=0`` on every corporate action row in the
              Scalable Capital export, which already drives
              :meth:`_handle_acquisition` to a per-share cost of zero -
              so we can simply route through that handler.
            * ``-qty`` rows (the broker reducing a parent position when
              shares are converted into a successor security) are
              ignored. The original parent lots stay in the queue;
              accuracy is traded for full automation.
            * ``qty=0`` / ``qty is None`` rows are ignored defensively.
        """

        if tx.quantity is None or tx.quantity == ZERO:
            logger.debug(
                "Skipping corporate action with zero/missing quantity "
                "at %s: %s",
                tx.date, tx,
            )
            return

        if tx.quantity > ZERO:
            self._handle_acquisition(tx)
            return

        # Negative quantity - intentionally a no-op. Logged at info so
        # the operator can correlate ignored deductions with surviving
        # parent lots when auditing.
        logger.info(
            "Ignoring corporate action deduction: account=%s isin=%s qty=%s "
            "(parent cost basis preserved)",
            tx.account_name, tx.isin, tx.quantity,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _queue_for(self, tx: Transaction) -> Deque[OpenLot]:
        """Return the tax-lot queue for the (account, ISIN) of `tx`."""
        # ISIN nullability is enforced by the dispatch handlers above,
        # so the type ignore is safe here.
        return self._queues[(tx.account_name, tx.isin)]  # type: ignore[index]

    @staticmethod
    def _peek_or_log_short_sale(
        queue: Deque[OpenLot],
        tx: Transaction,
        remaining_to_sell: Decimal,
    ) -> Optional[OpenLot]:
        """Return the front lot or log a short-sale warning."""
        if not queue:
            logger.warning(
                "Short sale detected: %s sold %s of %s but no open lots remain. "
                "This can happen if the original buy is not included in the "
                "input data (e.g. Security Transfer from another broker).",
                tx.account_name, remaining_to_sell, tx.isin,
            )
            return None
        return queue[0]

    def _collect_open_lots(self) -> list[OpenLot]:
        """Flatten every active queue into a single list."""
        return [lot for queue in self._queues.values() for lot in queue]
