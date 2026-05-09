"""Collapse paired Security transfer legs (broker-internal "switches").

Background
----------
Scalable Capital occasionally re-shelves shares between its two
sub-accounts (the regular brokerage account and the savings/depot
account). Each re-shelve appears in the export as TWO rows:

    1. an outbound `Security transfer` with negative quantity, dated on
       the day the shares left the source sub-account; its `reference`
       is a plain broker movement id (e.g. ``"WWUM 00596749782"``).
    2. an inbound `Security transfer` with the *same* absolute quantity
       and ISIN, typically dated one business day later; its
       `reference` carries the ``SWITCH-...-WDP`` marker.

Tax-lot wise the shares never left the customer - the broker preserves
the original lots across the move. If we let the tax-lot engine see
both legs it would:

    * pop the original cheap lots on the outbound (`_handle_transfer_out`)
    * push a brand-new lot at the inbound day's price on the
      acquisition (`_handle_acquisition`)

which destroys the original cost basis used by every later sell on the
ISIN. We therefore elide BOTH legs from the stream during ingestion so
the engine never observes them.

Detection rule
--------------
We use the broker's own marker on the inbound leg
(``reference`` starts with :data:`_SWITCH_REFERENCE_PREFIX`) and pair
it with the closest preceding outbound `Security transfer` matching:

    * same `account_name`
    * same `isin`
    * same `abs(quantity)`
    * within :data:`_SWITCH_TIME_WINDOW`

Unpaired Security transfers (e.g. the inbound row for shares brought
in from another broker via ``WWUM ...`` and not flagged as a switch,
or a one-way outbound transfer to a different broker) are intentionally
left alone and continue to flow through the tax-lot engine the usual
way.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from app.models import Transaction
from app.utils.logging import get_logger

logger = get_logger(__name__)


# Inbound leg of a Scalable Capital switch always starts with this
# prefix in the `reference` column. Centralised so the broker-specific
# detail lives in one obvious place.
_SWITCH_REFERENCE_PREFIX: str = "SWITCH-"

# Generous matching window. Real switches in observed data sit one
# business day apart; allowing a week tolerates oddities (weekends,
# month-end batching) without ever risking a false pair.
_SWITCH_TIME_WINDOW: timedelta = timedelta(days=7)


def collapse_switch_pairs(
    transactions: Iterable[Transaction],
) -> list[Transaction]:
    """Return `transactions` minus paired broker switch legs.

    Order is preserved. The function is pure and does not mutate the
    inputs - it returns a freshly built list so callers can replace
    their previous list reference safely.

    See module docstring for the detection rule and rationale.
    """

    rows: list[Transaction] = list(transactions)

    # Index outbound Security transfer rows by the matching key so we
    # can pair an inbound `SWITCH-` leg in O(1) average time. Using a
    # list per key allows a future inbound leg to skip outbounds that
    # were already paired by an earlier inbound.
    outbound_index: dict[tuple[str, str, Decimal], list[int]] = {}
    for idx, tx in enumerate(rows):
        if not tx.transaction_type.is_security_transfer:
            continue
        if tx.quantity is None or tx.isin is None:
            continue
        if tx.quantity >= 0:
            continue
        key = (tx.account_name, tx.isin, abs(tx.quantity))
        outbound_index.setdefault(key, []).append(idx)

    elided: set[int] = set()
    for idx, tx in enumerate(rows):
        if idx in elided:
            continue
        if not tx.transaction_type.is_security_transfer:
            continue
        if tx.quantity is None or tx.isin is None or tx.quantity <= 0:
            continue
        if not (tx.reference or "").startswith(_SWITCH_REFERENCE_PREFIX):
            continue

        key = (tx.account_name, tx.isin, abs(tx.quantity))
        candidates = [
            j for j in outbound_index.get(key, ())
            if j not in elided
            and abs(rows[j].date - tx.date) <= _SWITCH_TIME_WINDOW
        ]
        if not candidates:
            # An inbound switch with no plausible outbound. Defensive:
            # log loudly because it usually means the export is
            # incomplete (the outbound leg lives in an older snapshot
            # that was not provided).
            logger.warning(
                "Inbound switch reference=%r has no matching outbound "
                "leg (account=%s, isin=%s, qty=%s); leaving as a normal "
                "Security transfer.",
                tx.reference, tx.account_name, tx.isin, tx.quantity,
            )
            continue

        # Prefer the temporally closest outbound. Among ties, the
        # earliest (smallest index) wins, which keeps the pairing
        # deterministic across re-runs.
        match_idx = min(
            candidates,
            key=lambda j: (abs(rows[j].date - tx.date), j),
        )
        elided.add(idx)
        elided.add(match_idx)

        logger.info(
            "Collapsing broker switch pair: out=%s in=%s account=%s "
            "isin=%s qty=%s",
            rows[match_idx].date.date(),
            tx.date.date(),
            tx.account_name,
            tx.isin,
            tx.quantity,
        )

    if not elided:
        return rows

    return [tx for i, tx in enumerate(rows) if i not in elided]
