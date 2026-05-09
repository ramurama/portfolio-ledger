"""Parser for Scalable Capital Germany broker transaction exports.

Source format
-------------
The CSV is semicolon-delimited with a single header row:

    date;time;status;reference;description;assetType;type;isin;
    shares;price;amount;fee;tax;currency

Notable quirks we handle:

    * German number formatting (`,` decimal, `.` thousands).
    * `shares`, `price`, `fee`, `tax` may be empty (Distribution / Tax
      cash rows). `parse_german_decimal` returns `None` for those.
    * The `tax` column captures broker-withheld tax on Sells and
      Distributions. The spec models Tax as its *own* transaction type,
      so we emit a synthetic `TransactionType.TAX` row whenever a
      non-zero tax is present alongside a Buy / Sell / Distribution.
    * Rows with `status != "Executed"` are ignored - cancelled or
      pending orders must never reach FIFO.
"""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Iterator, Optional

from app.config import DEFAULT_CURRENCY, SUPPORTED_TRANSACTION_TYPES
from app.models import Transaction, TransactionType
from app.parsers.base import BrokerParser
from app.utils.date_utils import parse_broker_datetime
from app.utils.decimal_utils import (
    ZERO,
    parse_german_decimal,
    parse_german_decimal_or_zero,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Mapping from raw CSV `type` values to our internal enum. The configured
# transaction type list in `app.config` decides which of these raw broker
# values are admitted; anything else is intentionally dropped.
# ---------------------------------------------------------------------------
_KNOWN_TYPE_MAP: dict[str, TransactionType] = {
    "Buy": TransactionType.BUY,
    "Sell": TransactionType.SELL,
    "Savings plan": TransactionType.SAVINGS_PLAN,
    "Distribution": TransactionType.DISTRIBUTION,
    "Taxes": TransactionType.TAX,
    # The CSV occasionally uses singular "Tax" too - accept both.
    "Tax": TransactionType.TAX,
    "Security transfer": TransactionType.SECURITY_TRANSFER,
}
_UNKNOWN_CONFIGURED_TYPES = set(SUPPORTED_TRANSACTION_TYPES) - set(_KNOWN_TYPE_MAP)
if _UNKNOWN_CONFIGURED_TYPES:
    raise ValueError(
        "Unsupported transaction type(s) configured: "
        f"{sorted(_UNKNOWN_CONFIGURED_TYPES)}"
    )
_TYPE_MAP: dict[str, TransactionType] = {
    raw_type: _KNOWN_TYPE_MAP[raw_type]
    for raw_type in SUPPORTED_TRANSACTION_TYPES
}

# Required column headers - validated up-front to fail fast on broken exports.
_EXPECTED_COLUMNS: set[str] = {
    "date", "time", "status", "reference", "description", "assetType",
    "type", "isin", "shares", "price", "amount", "fee", "tax", "currency",
}


class ScalableCapitalParser(BrokerParser):
    """Concrete parser for Scalable Capital DE CSV exports."""

    broker_id = "scalable_capital"
    broker_name = "Scalable Capital (DE)"

    # ------------------------------------------------------------------
    # Sniffing
    # ------------------------------------------------------------------
    @classmethod
    def can_parse(cls, path: Path) -> bool:
        """Recognise the file by header signature."""

        if path.suffix.lower() != ".csv":
            return False

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                first_line = fh.readline().strip()
        except OSError:
            return False

        # Header is fixed across exports - matching the leading three
        # columns is enough to be confident without false positives.
        return first_line.startswith("date;time;status")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse(self, path: Path, account_name: str) -> Iterable[Transaction]:
        """Yield `Transaction`s for every supported row in `path`.

        The method is implemented as a generator so very large historical
        exports stream rather than materialising in one go.
        """

        logger.info(
            "Parsing %s file %s for account %s",
            self.broker_name,
            path.name,
            account_name,
        )

        # `utf-8-sig` strips the BOM that Scalable Capital sometimes adds.
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=";")

            self._validate_columns(reader.fieldnames, path)

            row_number = 1  # account for the header line
            for row in reader:
                row_number += 1
                yield from self._convert_row(row, account_name, path, row_number)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_columns(
        fieldnames: Optional[list[str]], path: Path
    ) -> None:
        """Fail fast if the export is missing required columns."""

        if not fieldnames:
            raise ValueError(f"{path}: empty CSV (no header row)")
        missing = _EXPECTED_COLUMNS - set(fieldnames)
        if missing:
            raise ValueError(
                f"{path}: missing expected columns {sorted(missing)}"
            )

    def _convert_row(
        self,
        row: dict[str, str],
        account_name: str,
        path: Path,
        row_number: int,
    ) -> Iterator[Transaction]:
        """Convert a single CSV row to zero, one or two `Transaction`s.

        We may emit two transactions for a single row when the broker
        reports tax alongside a Sell / Distribution - in that case the
        tax becomes its own `TransactionType.TAX` event so realized
        gains and tax burdens stay separable downstream.
        """

        status = (row.get("status") or "").strip()
        if status != "Executed":
            # Skip cancelled / pending / failed orders. Logging at
            # debug-only avoids drowning the operator in noise on large
            # historical exports.
            logger.debug(
                "Skipping non-executed row at %s:%d (status=%r)",
                path.name, row_number, status,
            )
            return

        raw_type = (row.get("type") or "").strip()
        tx_type = _TYPE_MAP.get(raw_type)
        if tx_type is None:
            # Unknown / unsupported type (Cash Transfer, Interest,
            # Corporate action, ...). These are intentionally dropped.
            logger.debug(
                "Ignoring unsupported type %r at %s:%d",
                raw_type, path.name, row_number,
            )
            return

        try:
            transaction = self._build_primary_transaction(
                row, account_name, tx_type
            )
        except ValueError as exc:
            logger.warning(
                "Malformed row at %s:%d skipped (%s)",
                path.name, row_number, exc,
            )
            return

        if transaction is not None:
            yield transaction

        # Emit a synthetic Tax transaction whenever the broker withheld
        # tax on a non-Tax row. This keeps the FIFO / realized-gains
        # report clean while still capturing tax events as first-class
        # records.
        synthetic_tax = self._maybe_build_synthetic_tax(
            row, account_name, tx_type
        )
        if synthetic_tax is not None:
            yield synthetic_tax

    # ------------------------------------------------------------------
    # Primary transaction construction
    # ------------------------------------------------------------------
    def _build_primary_transaction(
        self,
        row: dict[str, str],
        account_name: str,
        tx_type: TransactionType,
    ) -> Optional[Transaction]:
        """Assemble the main `Transaction` for a CSV row.

        Returns `None` when the row is structurally incomplete and
        cannot be safely admitted into the model.
        """

        when: datetime = parse_broker_datetime(row["date"], row.get("time"))

        quantity = parse_german_decimal(row.get("shares"))
        price = parse_german_decimal(row.get("price"))
        fees = parse_german_decimal_or_zero(row.get("fee"))
        amount = parse_german_decimal(row.get("amount"))
        currency = (row.get("currency") or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY
        isin = (row.get("isin") or "").strip() or None
        symbol = (row.get("description") or "").strip() or None

        # Trade-style events must carry quantity and price - skip the
        # row entirely if those are missing, since the FIFO engine
        # would have nothing meaningful to do with it.
        if tx_type in (
            TransactionType.BUY,
            TransactionType.SELL,
            TransactionType.SAVINGS_PLAN,
            TransactionType.SECURITY_TRANSFER,
        ):
            if quantity is None or price is None or isin is None:
                raise ValueError(
                    f"trade row missing quantity/price/isin "
                    f"(qty={quantity!r}, price={price!r}, isin={isin!r})"
                )

        # `amount` is mandatory for all supported types.
        if amount is None:
            raise ValueError("amount column is empty")

        return Transaction(
            account_name=account_name,
            date=when,
            isin=isin,
            symbol=symbol,
            transaction_type=tx_type,
            quantity=quantity,
            price=price,
            fees=fees,
            currency=currency,
            total_amount=amount,
        )

    # ------------------------------------------------------------------
    # Synthetic tax extraction
    # ------------------------------------------------------------------
    def _maybe_build_synthetic_tax(
        self,
        row: dict[str, str],
        account_name: str,
        primary_type: TransactionType,
    ) -> Optional[Transaction]:
        """Lift the embedded `tax` column into its own Tax transaction.

        We only do this when:
            * the primary row is not itself a Tax row, AND
            * the tax cell is non-empty AND non-zero.

        The synthetic transaction's `total_amount` carries the same sign
        as the broker reported - positive means tax debited from the
        account, negative means a tax refund credited back.
        """

        if primary_type is TransactionType.TAX:
            return None

        tax_value = parse_german_decimal(row.get("tax"))
        if tax_value is None or tax_value == ZERO:
            return None

        when = parse_broker_datetime(row["date"], row.get("time"))
        isin = (row.get("isin") or "").strip() or None
        symbol = (row.get("description") or "").strip() or None
        currency = (row.get("currency") or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY

        return Transaction(
            account_name=account_name,
            date=when,
            isin=isin,
            symbol=symbol,
            transaction_type=TransactionType.TAX,
            quantity=None,
            price=None,
            fees=Decimal("0"),
            currency=currency,
            total_amount=tax_value,
        )
